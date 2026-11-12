"""
Cycle X1: конвертер DOCX -> ODT с сохранением оформления.

Зачем: раньше экспорт в ODT принимал только текст и строил простой документ
(Times New Roman, без цветов, таблиц и выравнивания). Здесь исходный DOCX
(уже с применёнными улучшениями) переводится в ODT напрямую: страница и поля,
абзацы (выравнивание, отступы, интервалы, линии под заголовками), фрагменты
(шрифт, размер, жирный, курсив, подчёркивание, цвет), списки, таблицы, RTL.

Работает только на python-docx и odfpy, без внешних программ и без сети.
Главная функция: docx_bytes_to_odt(docx_bytes) -> BytesIO.

Что НЕ переносится: картинки и фигуры, вложенные таблицы, колонтитулы,
сноски, вертикальное объединение ячеек (ячейки остаются, но не сливаются),
цвета и шрифты из темы документа (кроме шрифтов темы, они читаются),
условное форматирование табличных стилей, поля и сноски.
"""
import io
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from odf.opendocument import OpenDocumentText
from odf import style as ostyle
from odf import text as otext
from odf import table as otable
from odf.style import FontFace

_FALSE = ("0", "false", "off", "none")
_RTL_RE = re.compile("[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f]")
_SKIP_COLORS = ("auto", "")

# Известные шрифты и их запасные (метрически совместимые) семейства.
_FONT_FALLBACK = {
    "calibri": ("Carlito", "swiss"),
    "cambria": ("Caladea", "roman"),
    "arial": ("Liberation Sans", "swiss"),
    "helvetica": ("Liberation Sans", "swiss"),
    "times new roman": ("Liberation Serif", "roman"),
    "courier new": ("Liberation Mono", "modern"),
    "georgia": ("DejaVu Serif", "roman"),
    "verdana": ("DejaVu Sans", "swiss"),
    "tahoma": ("DejaVu Sans", "swiss"),
    "segoe ui": ("Open Sans", "swiss"),
    "garamond": ("Liberation Serif", "roman"),
    "cambria math": ("Caladea", "roman"),
}
_SERIF_HINT = ("serif", "roman", "garamond", "georgia", "times", "book", "palatino")
_BULLET_MAP = {"\uf0b7": "\u2022", "\uf0a7": "\u25aa", "\uf0d8": "\u27a4", "\uf076": "\u2756",
               "o": "\u25e6", "\u00b7": "\u2022", "\uf0fc": "\u2713"}
_NUMFMT = {"decimal": "1", "lowerLetter": "a", "upperLetter": "A",
           "lowerRoman": "i", "upperRoman": "I", "decimalZero": "1"}


# ---------------------------------------------------------------------------
# Единицы и мелкие помощники
# ---------------------------------------------------------------------------

def _tw_cm(v):
    """Twips -> строка в сантиметрах."""
    return "%.3fcm" % (int(v) / 567.0)


def _pt(v):
    return ("%.2f" % v).rstrip("0").rstrip(".") + "pt"


def _val(el, name="w:val"):
    return None if el is None else el.get(qn(name))


def _on(el):
    """Переключатель Word (b, i, ...): элемент есть и val не 'false/0/off'."""
    if el is None:
        return None
    v = el.get(qn("w:val"))
    return v is None or v.lower() not in _FALSE


def _first(chain, tag):
    """Первый элемент tag в цепочке pPr/rPr (прямые -> стиль -> умолчания)."""
    for props in chain:
        if props is None:
            continue
        el = props.find(qn(tag))
        if el is not None:
            return el
    return None


def _style_chain_props(style, kind):
    """pPr или rPr всех стилей по цепочке basedOn."""
    out = []
    seen = 0
    while style is not None and seen < 20:
        el = style.element
        out.append(el.find(qn("w:" + kind)))
        style = style.base_style
        seen += 1
    return out


def _guess_generic(name):
    low = name.lower()
    if low in _FONT_FALLBACK:
        return _FONT_FALLBACK[low][1]
    if any(h in low for h in _SERIF_HINT):
        return "roman"
    if "mono" in low or "courier" in low or "consolas" in low:
        return "modern"
    return "swiss"


# ---------------------------------------------------------------------------
# Конвертер
# ---------------------------------------------------------------------------

class _Converter:
    def __init__(self, docx_bytes):
        self.doc = Document(io.BytesIO(docx_bytes))
        self.odt = OpenDocumentText()
        self._styles = {}       # ключ -> имя автостиля
        self._counter = {}
        self._fonts = set()
        self._list_styles = {}
        self._theme = self._load_theme_fonts()
        self._numbering = self._load_numbering()
        self._defaults_ppr, self._defaults_rpr = self._doc_defaults()
        self._first_in_paragraph = True
        self._tbl_ppr = []      # pPr/rPr стиля таблицы для абзацев внутри неё
        self._tbl_rpr = []

    # ---------------- служебное ----------------

    def _name(self, prefix):
        n = self._counter.get(prefix, 0) + 1
        self._counter[prefix] = n
        return "%s%d" % (prefix, n)

    def _load_theme_fonts(self):
        res = {}
        try:
            for part in self.doc.part.package.iter_parts():
                if str(part.partname).endswith("/theme/theme1.xml"):
                    from lxml import etree
                    root = etree.fromstring(part.blob)
                    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
                    for kind in ("major", "minor"):
                        for sub, key in (("latin", ""), ("cs", "Bidi")):
                            el = root.find(".//a:%sFont/a:%s" % (kind, sub), ns)
                            if el is not None and el.get("typeface"):
                                res[kind + key] = el.get("typeface")
                    break
        except Exception:
            pass
        return res

    def _load_numbering(self):
        try:
            return self.doc.part.numbering_part.element
        except Exception:
            return None

    def _doc_defaults(self):
        root = self.doc.styles.element
        dd = root.find(qn("w:docDefaults"))
        ppr = rpr = None
        if dd is not None:
            p = dd.find(qn("w:pPrDefault"))
            r = dd.find(qn("w:rPrDefault"))
            ppr = p.find(qn("w:pPr")) if p is not None else None
            rpr = r.find(qn("w:rPr")) if r is not None else None
        return ppr, rpr

    def _font_name(self, rfonts, attr, theme_attr):
        if rfonts is None:
            return None
        name = rfonts.get(qn("w:" + attr))
        if name:
            return name
        theme = rfonts.get(qn("w:" + theme_attr))
        if theme:
            kind = "major" if theme.startswith("major") else "minor"
            suffix = "Bidi" if theme.endswith("Bidi") else ""
            return self._theme.get(kind + suffix)
        return None

    def _register_font(self, name):
        if not name or name in self._fonts:
            return
        self._fonts.add(name)
        fallback = _FONT_FALLBACK.get(name.lower())
        family = "'%s'" % name
        if fallback:
            family += ", '%s'" % fallback[0]
        self.odt.fontfacedecls.addElement(FontFace(
            name=name, fontfamily=family, fontfamilygeneric=_guess_generic(name), fontpitch="variable"))

    def _auto_style(self, key, family, builder, prefix):
        full = (family, key)
        if full in self._styles:
            return self._styles[full]
        name = self._name(prefix)
        st = ostyle.Style(name=name, family=family)
        builder(st)
        self.odt.automaticstyles.addElement(st)
        self._styles[full] = name
        return name

    # ---------------- свойства текста ----------------

    def _run_props(self, run_style, para_style, direct_rpr):
        """Итоговые свойства фрагмента: прямые -> стиль символов -> стиль абзаца -> умолчания."""
        chain = [direct_rpr]
        chain += _style_chain_props(run_style, "rPr") if run_style is not None else []
        chain += _style_chain_props(para_style, "rPr") if para_style is not None else []
        chain += self._tbl_rpr
        chain.append(self._defaults_rpr)

        rfonts_all = [c.find(qn("w:rFonts")) for c in chain if c is not None and c.find(qn("w:rFonts")) is not None]
        font = cs_font = None
        for rf in rfonts_all:
            font = font or self._font_name(rf, "ascii", "asciiTheme") or self._font_name(rf, "hAnsi", "hAnsiTheme")
            cs_font = cs_font or self._font_name(rf, "cs", "cstheme")
        font = font or "Calibri"
        cs_font = cs_font or font

        sz = _val(_first(chain, "w:sz"))
        szcs = _val(_first(chain, "w:szCs"))
        size = int(sz) / 2.0 if sz else 11.0
        size_cs = int(szcs) / 2.0 if szcs else size

        bold = bool(_on(_first(chain, "w:b")))
        bold_cs = _on(_first(chain, "w:bCs"))
        bold_cs = bold if bold_cs is None else bold_cs
        italic = bool(_on(_first(chain, "w:i")))
        italic_cs = _on(_first(chain, "w:iCs"))
        italic_cs = italic if italic_cs is None else italic_cs

        u = _val(_first(chain, "w:u"))
        underline = bool(u) and u.lower() not in _FALSE
        color = _val(_first(chain, "w:color"))
        if color is not None and (color.lower() in _SKIP_COLORS or not re.match(r"^[0-9A-Fa-f]{6}$", color)):
            color = None
        strike = bool(_on(_first(chain, "w:strike")))
        caps = bool(_on(_first(chain, "w:caps")))
        small = bool(_on(_first(chain, "w:smallCaps")))
        va = _val(_first(chain, "w:vertAlign"))
        return (font, cs_font, size, size_cs, bold, bold_cs, italic, italic_cs,
                underline, color.upper() if color else None, strike, caps, small, va)

    def _fill_text_props(self, st, props):
        (font, cs_font, size, size_cs, bold, bold_cs, italic, italic_cs,
         underline, color, strike, caps, small, va) = props
        self._register_font(font)
        self._register_font(cs_font)
        kw = dict(fontname=font, fontnamecomplex=cs_font,
                  fontsize=_pt(size), fontsizecomplex=_pt(size_cs),
                  fontweight="bold" if bold else "normal",
                  fontweightcomplex="bold" if bold_cs else "normal",
                  fontstyle="italic" if italic else "normal",
                  fontstylecomplex="italic" if italic_cs else "normal")
        if underline:
            kw.update(textunderlinestyle="solid", textunderlinewidth="auto", textunderlinecolor="font-color")
        if color:
            kw["color"] = "#" + color
        if strike:
            kw["textlinethroughstyle"] = "solid"
        if caps:
            kw["texttransform"] = "uppercase"
        elif small:
            kw["fontvariant"] = "small-caps"
        if va == "superscript":
            kw["textposition"] = "super 58%"
        elif va == "subscript":
            kw["textposition"] = "sub 58%"
        st.addElement(ostyle.TextProperties(**kw))

    def _span_style(self, props):
        return self._auto_style(props, "text", lambda st: self._fill_text_props(st, props), "T")

    # ---------------- свойства абзаца ----------------

    def _border(self, el):
        if el is None:
            return None, 0.0
        v = (el.get(qn("w:val")) or "").lower()
        if v in ("nil", "none", ""):
            return None, 0.0
        sz = el.get(qn("w:sz"))
        width = max(0.25, int(sz) / 8.0) if sz else 0.5
        color = el.get(qn("w:color")) or "000000"
        if color.lower() == "auto" or not re.match(r"^[0-9A-Fa-f]{6}$", color):
            color = "000000"
        space = el.get(qn("w:space"))
        kind = "double" if v == "double" else ("dotted" if v == "dotted" else ("dashed" if v == "dashed" else "solid"))
        return "%s %s #%s" % (_pt(width), kind, color.upper()), float(space) if space else 0.0

    def _numbering_info(self, num_id, ilvl):
        """(bullet?, numFmt, lvlText, left, hanging) для уровня списка или None."""
        if self._numbering is None or num_id is None:
            return None
        num = None
        for n in self._numbering.findall(qn("w:num")):
            if n.get(qn("w:numId")) == str(num_id):
                num = n
                break
        if num is None:
            return None
        aid = _val(num.find(qn("w:abstractNumId")))
        for an in self._numbering.findall(qn("w:abstractNum")):
            if an.get(qn("w:abstractNumId")) == aid:
                for lvl in an.findall(qn("w:lvl")):
                    if lvl.get(qn("w:ilvl")) == str(ilvl):
                        fmt = _val(lvl.find(qn("w:numFmt"))) or "decimal"
                        txt = _val(lvl.find(qn("w:lvlText"))) or ""
                        ind = lvl.find(qn("w:pPr") + "/" + qn("w:ind")) if lvl.find(qn("w:pPr")) is not None else None
                        left = int(ind.get(qn("w:left")) or ind.get(qn("w:start")) or 0) if ind is not None else 720 * (int(ilvl) + 1)
                        hang = int(ind.get(qn("w:hanging")) or 0) if ind is not None else 360
                        return fmt, txt, left, hang
        return None

    def _para_ctx(self, para):
        """Собрать итоговые параметры абзаца."""
        ppr = para._p.pPr
        style = para.style
        chain = [ppr] + _style_chain_props(style, "pPr") + self._tbl_ppr + [self._defaults_ppr]
        ctx = {}
        ctx["bidi"] = bool(_on(_first(chain, "w:bidi")))
        jc = (_val(_first(chain, "w:jc")) or "").lower()
        ctx["jc"] = jc
        sp = _first(chain, "w:spacing")
        # у spacing атрибуты могут быть заданы в разных уровнях цепочки: берём по каждому
        def sp_attr(name):
            for props in chain:
                if props is None:
                    continue
                el = props.find(qn("w:spacing"))
                if el is not None and el.get(qn("w:" + name)) is not None:
                    return el.get(qn("w:" + name))
            return None
        ctx["before"] = sp_attr("before")
        ctx["after"] = sp_attr("after")
        if _on(_first(chain, "w:contextualSpacing")):
            ctx["before"] = ctx["after"] = "0"  # приближение: между абзацами одного стиля интервала нет
        ctx["line"] = sp_attr("line")
        ctx["rule"] = sp_attr("lineRule") or "auto"

        def ind_attr(*names):
            for props in chain:
                if props is None:
                    continue
                el = props.find(qn("w:ind"))
                if el is not None:
                    for n in names:
                        if el.get(qn("w:" + n)) is not None:
                            return int(el.get(qn("w:" + n)))
            return None
        ctx["left"] = ind_attr("left", "start")
        ctx["right"] = ind_attr("right", "end")
        ctx["first"] = ind_attr("firstLine")
        ctx["hanging"] = ind_attr("hanging")
        ctx["ind_direct"] = ppr is not None and ppr.find(qn("w:ind")) is not None

        borders = {}
        for side in ("top", "bottom", "left", "right"):
            for props in chain:
                if props is None:
                    continue
                bd = props.find(qn("w:pBdr"))
                if bd is not None and bd.find(qn("w:" + side)) is not None:
                    borders[side] = self._border(bd.find(qn("w:" + side)))
                    break
        ctx["borders"] = borders

        shd = _first(chain, "w:shd")
        fill = _val(shd, "w:fill") if shd is not None else None
        ctx["fill"] = fill.upper() if fill and re.match(r"^[0-9A-Fa-f]{6}$", fill) else None

        tabs = None
        for props in chain:
            if props is not None and props.find(qn("w:tabs")) is not None:
                tabs = props.find(qn("w:tabs"))
                break
        ctx["tabs"] = [(t.get(qn("w:val")), t.get(qn("w:pos")), t.get(qn("w:leader")))
                       for t in tabs.findall(qn("w:tab"))] if tabs is not None else []
        ctx["page_break"] = bool(_on(_first(chain, "w:pageBreakBefore")))

        num = _first(chain, "w:numPr")
        ctx["num_id"] = ctx["ilvl"] = None
        if num is not None:
            nid = _val(num.find(qn("w:numId")))
            il = _val(num.find(qn("w:ilvl")))
            # numPr стиля/прямой может задавать только ilvl: числовой id ищем по всей цепочке
            if nid is None:
                for props in chain:
                    if props is not None and props.find(qn("w:numPr")) is not None:
                        e = props.find(qn("w:numPr")).find(qn("w:numId"))
                        if e is not None:
                            nid = e.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val")
                            break
            ctx["num_id"] = nid if nid not in (None, "0") else None
            ctx["ilvl"] = int(il) if il is not None else 0
        return ctx

    def _fill_para_props(self, st, ctx, props_default, is_rtl, in_list, list_ind):
        kw = {}
        align = ctx["jc"]
        if is_rtl:
            kw["writingmode"] = "rl-tb"
            # В RTL-абзаце Word 'left/start' = от правого края; LibreOffice читает start/end как
            # физические left/right (проверено рендером), поэтому пишем физическое направление.
            amap = {"left": "right", "start": "right", "right": "left", "end": "left",
                    "center": "center", "both": "justify", "distribute": "justify"}
        else:
            amap = {"left": "left", "start": "left", "right": "right", "end": "right",
                    "center": "center", "both": "justify", "distribute": "justify"}
        if align in amap:
            kw["textalign"] = amap[align]
        elif is_rtl:
            kw["textalign"] = "right"  # RTL без явного выравнивания: от правого края
        if ctx["before"] is not None:
            kw["margintop"] = _tw_cm(ctx["before"])
        if ctx["after"] is not None:
            kw["marginbottom"] = _tw_cm(ctx["after"])
        if ctx["line"] is not None:
            line = int(ctx["line"])
            if ctx["rule"] == "auto":
                kw["lineheight"] = "%d%%" % round(line / 240.0 * 100)
            elif ctx["rule"] == "exact":
                kw["lineheight"] = _tw_cm(line)
            else:
                kw["linespacing"] = _tw_cm(line)
        if not in_list:
            left, right = ctx["left"], ctx["right"]
            if is_rtl:  # в RTL-абзаце левый отступ Word означает отступ «от начала» (справа)
                left, right = right, left
            if left is not None:
                kw["marginleft"] = _tw_cm(left)
            if right is not None:
                kw["marginright"] = _tw_cm(right)
            if ctx["hanging"]:
                kw["textindent"] = _tw_cm(-ctx["hanging"])
            elif ctx["first"]:
                kw["textindent"] = _tw_cm(ctx["first"])
        if ctx["fill"]:
            kw["backgroundcolor"] = "#" + ctx["fill"]
        if ctx["page_break"]:
            kw["breakbefore"] = "page"
        for side, (b, space) in ctx["borders"].items():
            if b:
                kw["border" + side] = b
                if space:
                    kw["padding" + side] = _pt(space)
        st.addElement(ostyle.ParagraphProperties(**kw))
        if ctx["tabs"]:
            tabs = ostyle.TabStops()
            tmap = {"left": "left", "right": "right", "center": "center", "decimal": "char", "start": "left", "end": "right"}
            for kind, pos, leader in ctx["tabs"]:
                if kind in ("clear", "bar") or pos is None:
                    continue
                kwt = dict(position=_tw_cm(pos), type=tmap.get(kind or "left", "left"))
                if leader in ("dot", "hyphen", "underscore"):
                    kwt["leaderstyle"] = "solid"
                    kwt["leadertext"] = {"dot": ".", "hyphen": "-", "underscore": "_"}[leader]
                tabs.addElement(ostyle.TabStop(**kwt))
            st.addElement(tabs)
        self._fill_text_props(st, props_default)

    def _para_style(self, para, ctx, is_rtl, in_list, list_ind):
        pst = para.style
        props = self._run_props(None, pst, None)
        key = (tuple(sorted((k, str(v)) for k, v in ctx.items() if k not in ("num_id", "ilvl", "ind_direct"))),
               is_rtl, in_list, props)

        def build(st):
            st.setAttribute("parentstylename", "Standard")
            self._fill_para_props(st, ctx, props, is_rtl, in_list, list_ind)
        return self._auto_style(key, "paragraph", build, "P")

    # ---------------- списки ----------------

    def _list_style(self, num_id, ilvl, left, hanging):
        key = (num_id, ilvl, left, hanging)
        if key in self._list_styles:
            return self._list_styles[key]
        name = self._name("L")
        ls = otext.ListStyle(name=name)
        for lvl in range(10):
            info = self._numbering_info(num_id, lvl)
            if lvl == ilvl:
                lvl_left, lvl_hang = left, hanging
            elif info:
                lvl_left, lvl_hang = info[2], info[3]
            else:
                lvl_left, lvl_hang = 720 * (lvl + 1), 360
            fmt, txt = (info[0], info[1]) if info else ("bullet", "\u2022")
            if fmt in ("bullet", "none") or fmt not in _NUMFMT:
                ch = _BULLET_MAP.get(txt, txt) or "\u2022"
                if ord(ch[0]) >= 0xE000:
                    ch = "\u2022"
                el = otext.ListLevelStyleBullet(level=lvl + 1, bulletchar=ch[0])
            else:
                suffix = "." if "." in txt else (")" if ")" in txt else "")
                el = otext.ListLevelStyleNumber(level=lvl + 1, numformat=_NUMFMT[fmt], numsuffix=suffix)
            llp = ostyle.ListLevelProperties(listlevelpositionandspacemode="label-alignment")
            llp.addElement(ostyle.ListLevelLabelAlignment(
                labelfollowedby="listtab", listtabstopposition=_tw_cm(lvl_left),
                textindent=_tw_cm(-lvl_hang), marginleft=_tw_cm(lvl_left)))
            el.addElement(llp)
            ls.addElement(el)
        self.odt.automaticstyles.addElement(ls)
        self._list_styles[key] = name
        return name

    # ---------------- текст ----------------

    def _add_text(self, parent, s):
        for tok in re.finditer(r"\t|\n|\r| {2,}| |[^\t\n\r ]+", s):
            t = tok.group(0)
            if t == "\t":
                parent.addElement(otext.Tab())
            elif t == "\n":
                parent.addElement(otext.LineBreak())
            elif t == "\r":
                continue
            elif t.startswith(" ") and (len(t) > 1 or self._first_in_paragraph):
                parent.addElement(otext.S(c=len(t)))
                self._first_in_paragraph = False
            else:
                parent.addText(t)
                self._first_in_paragraph = False

    def _add_run(self, parent, run, para_style):
        txt = run.text
        if not txt:
            return
        props = self._run_props(run.style if run._r.rPr is not None and run._r.rPr.find(qn("w:rStyle")) is not None else None,
                                para_style, run._r.rPr)
        name = self._span_style(props)
        span = otext.Span(stylename=name)
        self._add_text(span, txt)
        parent.addElement(span)

    def _fill_paragraph(self, p_el, para):
        self._first_in_paragraph = True
        for item in para.iter_inner_content():
            if isinstance(item, Hyperlink):
                url = None
                try:
                    url = item.url
                except Exception:
                    url = None
                holder = otext.A(href=url, type="simple") if url else p_el
                for r in item.runs:
                    self._add_run(holder, r, para.style)
                if url:
                    p_el.addElement(holder)
            elif isinstance(item, Run):
                self._add_run(p_el, item, para.style)

    def _is_rtl(self, para, ctx):
        if ctx["bidi"]:
            return True
        runs = [r for r in para.runs if r.text.strip()]
        if not runs:
            return False
        rtl_runs = 0
        for r in runs:
            rpr = r._r.rPr
            if rpr is not None and _on(rpr.find(qn("w:rtl"))):
                rtl_runs += 1
        return rtl_runs == len(runs)

    def _make_paragraph(self, para):
        ctx = self._para_ctx(para)
        is_rtl = self._is_rtl(para, ctx)
        num_id, ilvl = ctx["num_id"], ctx["ilvl"]
        info = self._numbering_info(num_id, ilvl) if num_id else None
        in_list = info is not None and info[0] != "none"
        list_ind = None
        if in_list:
            left = ctx["left"] if ctx["ind_direct"] and ctx["left"] is not None else info[2]
            hang = ctx["hanging"] if ctx["ind_direct"] and ctx["hanging"] is not None else info[3]
            list_ind = (left, hang)
        sname = self._para_style(para, ctx, is_rtl, in_list, list_ind)
        p_el = otext.P(stylename=sname)
        self._fill_paragraph(p_el, para)
        return p_el, (num_id, ilvl, list_ind) if in_list else None

    # ---------------- таблицы ----------------

    def _table_borders(self, tbl):
        """Границы таблицы: прямые tblBorders, затем стиль таблицы."""
        chain = [tbl._tbl.tblPr]
        try:
            chain += _style_chain_props(tbl.style, "tblPr")
        except Exception:
            pass
        res = {}
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            for props in chain:
                if props is None:
                    continue
                b = props.find(qn("w:tblBorders"))
                if b is not None and b.find(qn("w:" + side)) is not None:
                    res[side] = b.find(qn("w:" + side))
                    break
        return res, chain

    def _cell_style(self, borders, tcpr, tbl_borders, tbl_chain, r, c, nr, nc, span):
        def pick(side, inside):
            el = None
            if borders is not None and borders.find(qn("w:" + side)) is not None:
                el = borders.find(qn("w:" + side))
            else:
                at_edge = {"top": r == 0, "bottom": r == nr - 1, "left": c == 0, "right": c + span >= nc}[side]
                el = tbl_borders.get(side if at_edge else inside)
                if el is None:
                    el = tbl_borders.get(inside)
            return self._border(el)[0]
        b = {"top": pick("top", "insideH"), "bottom": pick("bottom", "insideH"),
             "left": pick("left", "insideV"), "right": pick("right", "insideV")}
        valign = _val(tcpr.find(qn("w:vAlign"))) if tcpr is not None and tcpr.find(qn("w:vAlign")) is not None else None
        shd = tcpr.find(qn("w:shd")) if tcpr is not None else None
        fill = _val(shd, "w:fill") if shd is not None else None
        if fill and not re.match(r"^[0-9A-Fa-f]{6}$", fill):
            fill = None
        mar = None
        for props in [tcpr] + tbl_chain:
            if props is None:
                continue
            m = props.find(qn("w:tcMar")) if props is tcpr else props.find(qn("w:tblCellMar"))
            if m is not None:
                mar = m
                break

        def pad(side, default):
            if mar is not None:
                e = mar.find(qn("w:" + side))
                if e is None and side in ("left", "right"):
                    e = mar.find(qn("w:" + ("start" if side == "left" else "end")))
                if e is not None and e.get(qn("w:w")) is not None:
                    return _tw_cm(e.get(qn("w:w")))
            return default
        key = (tuple(sorted(b.items(), key=lambda kv: kv[0])), valign, fill,
               pad("top", "0cm"), pad("bottom", "0cm"), pad("left", "0.19cm"), pad("right", "0.19cm"))

        def build(st):
            kw = {}
            for side, v in b.items():
                kw["border" + side] = v or "none"
            kw["paddingtop"], kw["paddingbottom"], kw["paddingleft"], kw["paddingright"] = key[3:7]
            if valign:
                kw["verticalalign"] = {"center": "middle", "bottom": "bottom"}.get(valign, "top")
            if fill:
                kw["backgroundcolor"] = "#" + fill.upper()
            st.addElement(ostyle.TableCellProperties(**kw))
        return self._auto_style(key, "table-cell", build, "TC")

    def _make_table(self, tbl):
        grid = [int(g.get(qn("w:w")) or 0) for g in tbl._tbl.tblGrid.findall(qn("w:gridCol"))]
        rows = tbl._tbl.findall(qn("w:tr"))
        if not grid:
            grid = [2000]
        ncols = len(grid)
        total = sum(grid)
        tbl_borders, tbl_chain = self._table_borders(tbl)
        try:
            self._tbl_ppr = _style_chain_props(tbl.style, "pPr")
            self._tbl_rpr = _style_chain_props(tbl.style, "rPr")
        except Exception:
            self._tbl_ppr, self._tbl_rpr = [], []
        pr = tbl._tbl.tblPr
        jc = _val(pr.find(qn("w:jc"))) if pr is not None and pr.find(qn("w:jc")) is not None else None
        ind = pr.find(qn("w:tblInd")) if pr is not None else None
        ind_w = int(ind.get(qn("w:w")) or 0) if ind is not None and ind.get(qn("w:type")) in (None, "dxa") else 0
        bidi_tbl = pr is not None and _on(pr.find(qn("w:bidiVisual"))) is True

        def tstyle(st):
            kw = dict(width=_tw_cm(total), align="center" if jc == "center" else ("right" if jc in ("right", "end") else "left"))
            if kw["align"] == "left" and ind_w:
                kw["align"] = "margins"
                kw["marginleft"] = _tw_cm(ind_w)
            kw["margintop"] = "0cm"
            kw["marginbottom"] = "0cm"
            if bidi_tbl:
                kw["writingmode"] = "rl-tb"
            st.addElement(ostyle.TableProperties(**kw))
        tname = self._auto_style(("tbl", total, jc, ind_w, bidi_tbl), "table", tstyle, "Tbl")
        t = otable.Table(name=self._name("Table"), stylename=tname)

        for w in grid:
            def cstyle(st, w=w):
                st.addElement(ostyle.TableColumnProperties(columnwidth=_tw_cm(w)))
            cname = self._auto_style(("col", w), "table-column", cstyle, "Col")
            t.addElement(otable.TableColumn(stylename=cname))

        nrows = len(rows)
        for r, tr in enumerate(rows):
            row_el = otable.TableRow()
            c = 0
            for tc in tr.findall(qn("w:tc")):
                tcpr = tc.find(qn("w:tcPr"))
                span = 1
                if tcpr is not None and tcpr.find(qn("w:gridSpan")) is not None:
                    span = max(1, int(_val(tcpr.find(qn("w:gridSpan"))) or 1))
                span = min(span, max(1, ncols - c))
                borders = tcpr.find(qn("w:tcBorders")) if tcpr is not None else None
                sname = self._cell_style(borders, tcpr, tbl_borders, tbl_chain, r, c, nrows, ncols, span)
                kw = dict(stylename=sname, valuetype="string")
                if span > 1:
                    kw["numbercolumnsspanned"] = span
                cell = otable.TableCell(**kw)
                made = False
                for child in tc.iterchildren():
                    if child.tag == qn("w:p"):
                        p_el, _info = self._make_paragraph(Paragraph(child, tbl))
                        cell.addElement(p_el)
                        made = True
                if not made:
                    cell.addElement(otext.P(stylename="Standard"))
                row_el.addElement(cell)
                for _ in range(span - 1):
                    row_el.addElement(otable.CoveredTableCell())
                c += span
            while c < ncols:  # короткая строка: добиваем пустыми ячейками
                row_el.addElement(otable.TableCell(valuetype="string"))
                c += 1
            t.addElement(row_el)
        self._tbl_ppr, self._tbl_rpr = [], []
        return t

    # ---------------- сборка ----------------

    def _page(self):
        sec = self.doc.sections[0]
        kw = {}
        for attr, key in (("page_width", "pagewidth"), ("page_height", "pageheight"),
                          ("top_margin", "margintop"), ("bottom_margin", "marginbottom"),
                          ("left_margin", "marginleft"), ("right_margin", "marginright")):
            v = getattr(sec, attr)
            if v is not None:
                kw[key] = "%.3fcm" % v.cm
        if "pagewidth" in kw and "pageheight" in kw:
            kw["printorientation"] = "landscape" if sec.page_width > sec.page_height else "portrait"
        pl = ostyle.PageLayout(name="pm1")
        pl.addElement(ostyle.PageLayoutProperties(**kw))
        self.odt.automaticstyles.addElement(pl)
        self.odt.masterstyles.addElement(ostyle.MasterPage(name="Standard", pagelayoutname="pm1"))

    def _standard_style(self):
        props = self._run_props(None, self.doc.styles.default(1) if hasattr(self.doc.styles, "default") else None, None)
        st = ostyle.Style(name="Standard", family="paragraph")
        kw = {}
        for tag, key in (("w:spacing", None),):
            pass
        st.addElement(ostyle.ParagraphProperties(margintop="0cm", marginbottom="0cm"))
        self._fill_text_props(st, props)
        self.odt.styles.addElement(st)

    def convert(self):
        self._page()
        self._standard_style()
        cur_list = None        # (num_id, list_el, depth, items)
        stack = []

        def close_list():
            nonlocal cur_list, stack
            cur_list = None
            stack = []

        for item in self.doc.iter_inner_content():
            if isinstance(item, Table):
                close_list()
                try:
                    self.odt.text.addElement(self._make_table(item))
                except Exception:
                    continue  # таблицу не удалось перенести: пропускаем, остальное сохраняем
            elif isinstance(item, Paragraph):
                p_el, linfo = self._make_paragraph(item)
                if linfo is None:
                    close_list()
                    self.odt.text.addElement(p_el)
                    continue
                num_id, ilvl, ind = linfo
                if cur_list is None or cur_list != num_id:
                    close_list()
                    lname = self._list_style(num_id, ilvl, ind[0], ind[1])
                    root = otext.List(stylename=lname)
                    self.odt.text.addElement(root)
                    stack = [[root, None]]
                    cur_list = num_id
                depth = ilvl + 1
                while len(stack) > depth:
                    stack.pop()
                while len(stack) < depth:
                    parent_item = stack[-1][1]
                    if parent_item is None:
                        parent_item = otext.ListItem()
                        stack[-1][0].addElement(parent_item)
                        stack[-1][1] = parent_item
                    sub = otext.List()
                    parent_item.addElement(sub)
                    stack.append([sub, None])
                li = otext.ListItem()
                li.addElement(p_el)
                stack[-1][0].addElement(li)
                stack[-1][1] = li
        out = io.BytesIO()
        self.odt.write(out)
        out.seek(0)
        return out


def docx_bytes_to_odt(docx_bytes):
    """DOCX (bytes) -> ODT (BytesIO) с сохранением оформления. Исключения не глушит: их ловит вызывающий."""
    return _Converter(docx_bytes).convert()
