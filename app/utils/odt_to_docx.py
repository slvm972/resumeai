# app/utils/odt_to_docx.py
"""
Cycle N2a: адаптер ODT -> DOCX (чистый Python: zipfile, lxml, python-docx; без soffice).

Читает content.xml и styles.xml, разрешает стили (автоматические, именованные, наследование через
style:parent-style-name, style:default-style, стили списков, таблиц, ячеек, колонок, шрифты из font-face-decls)
и строит DOCX с ПРЯМЫМ форматированием абзацев и фрагментов, настоящей нумерацией списков, таблицами,
табами, нижней границей абзаца, RTL и размером страницы. Результат работает во всей цепочке DOCX:
_extract_structured, _apply_improved_text_to_docx, docx_bytes_to_odt/rtf/pdf, creative_docx_builder.

Не переносится (игнорируется без ошибки): картинки и фигуры, поля, сноски, гиперссылки (остаётся текст),
вложенные таблицы, колонтитулы, цвет фона абзацев и текста, межсимвольный интервал.

Детерминизм: в документе нет случайных значений и текущего времени (свойства файла фиксированы),
идентификаторы нумерации присваиваются по порядку обхода.

Безопасность: лимиты на число файлов и распакованный размер (zip-бомба), DOCTYPE и сущности отвергаются,
парсер lxml без разрешения сущностей, без сети и DTD. Любой отказ: NormalizeRejected (WARNING в журнал).
"""
import datetime
import io
import re
import zipfile

from lxml import etree
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from app.utils.normalize_input import NormalizeRejected

MIMETYPE = b"application/vnd.oasis.opendocument.text"
MAX_FILES = 500
MAX_TOTAL_UNPACKED = 100 * 1024 * 1024
MAX_XML = 20 * 1024 * 1024
MAX_NODES = 400000

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
}
_REV = {v: k for k, v in NS.items()}


def _q(prefix, local):
    return "{%s}%s" % (NS[prefix], local)


def _local(el):
    return el.tag.split("}", 1)[1] if isinstance(el.tag, str) and "}" in el.tag else str(el.tag)


def _pfx(el):
    t = el.tag
    if not isinstance(t, str) or "}" not in t:
        return ""
    return _REV.get(t[1:].split("}", 1)[0], "")


def _is(el, prefix, local):
    return isinstance(el.tag, str) and el.tag == _q(prefix, local)


# ---------------------------------------------------------------------------
# Чтение архива и XML
# ---------------------------------------------------------------------------

def _read_limited(z, name, limit):
    with z.open(name) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise NormalizeRejected("xml_too_large")
    return data


def _parse_xml(data):
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise NormalizeRejected("doctype_or_entity")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
                             huge_tree=False, remove_comments=True, remove_pis=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        raise NormalizeRejected("bad_xml")
    n = 0
    for _ in root.iter():
        n += 1
        if n > MAX_NODES:
            raise NormalizeRejected("too_many_nodes")
    return root


def _open_odt(file_bytes):
    bio = io.BytesIO(file_bytes)
    if not zipfile.is_zipfile(bio):
        raise NormalizeRejected("not_zip")
    try:
        z = zipfile.ZipFile(bio)
        infos = z.infolist()
    except zipfile.BadZipFile:
        raise NormalizeRejected("bad_zip")
    if len(infos) > MAX_FILES:
        raise NormalizeRejected("too_many_files")
    if sum(i.file_size for i in infos) > MAX_TOTAL_UNPACKED:
        raise NormalizeRejected("unpacked_too_large")
    names = {i.filename for i in infos}
    if "mimetype" not in names or "content.xml" not in names:
        raise NormalizeRejected("no_mimetype_or_content")
    if _read_limited(z, "mimetype", 200).strip() != MIMETYPE:
        raise NormalizeRejected("not_odt_text")
    content = _parse_xml(_read_limited(z, "content.xml", MAX_XML))
    styles = _parse_xml(_read_limited(z, "styles.xml", MAX_XML)) if "styles.xml" in names else None
    return content, styles


# ---------------------------------------------------------------------------
# Единицы и значения
# ---------------------------------------------------------------------------

_LEN = re.compile(r"^\s*(-?\d+(?:\.\d+)?|-?\.\d+)\s*(in|cm|mm|pt|pc|px|%)?\s*$")


def _pt(value, base=None):
    """Длина в пунктах (float) или None. Проценты считаются от base."""
    if value is None:
        return None
    m = _LEN.match(value)
    if not m:
        return None
    v = float(m.group(1))
    u = m.group(2) or "pt"
    if u == "%":
        return None if base is None else base * v / 100.0
    return v * {"in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4, "pt": 1.0, "pc": 12.0, "px": 0.75}[u]


def _tw(value):
    """Длина в твипах (int) или None."""
    p = _pt(value)
    return None if p is None else int(round(p * 20))


def _hex(color):
    if not color:
        return None
    m = re.match(r"^#([0-9A-Fa-f]{6})$", color.strip())
    return m.group(1).upper() if m else None


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_RTL_RE = re.compile("[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f]")
_WS = re.compile(r"[ \t\r\n]+")


# ---------------------------------------------------------------------------
# Стили
# ---------------------------------------------------------------------------

_PROP_KINDS = ("text-properties", "paragraph-properties", "table-properties", "table-column-properties",
               "table-row-properties", "table-cell-properties")


def _merge_into(dst, props_el):
    """Слить атрибуты одного style:*-properties в словарь «префикс:имя» -> значение (проценты размера шрифта сразу
    пересчитываются в пункты от текущего значения). tab-stops складываются в dst['_tabs'] и заменяют унаследованные."""
    for attr, val in props_el.attrib.items():
        if not attr.startswith("{"):
            continue
        ns, loc = attr[1:].split("}", 1)
        pf = _REV.get(ns)
        if not pf:
            continue
        key = "%s:%s" % (pf, loc)
        if loc.startswith("font-size") and val.strip().endswith("%"):
            base = _pt(dst.get(key)) or _pt(dst.get("fo:font-size")) or 11.0
            p = _pt(val, base)
            val = "%.2fpt" % p if p is not None else dst.get(key, "11pt")
        dst[key] = val
    # цвет: более поздний стиль перекрывает более ранний (fo:color отменяет «цвет окна», «цвет окна» отменяет fo:color)
    own = props_el.attrib
    if _q("fo", "color") in own:
        dst.pop("style:use-window-font-color", None)
    elif own.get(_q("style", "use-window-font-color")) == "true":
        dst.pop("fo:color", None)
    for ch in props_el:
        if _is(ch, "style", "tab-stops"):
            tabs = []
            for ts in ch:
                if _is(ts, "style", "tab-stop"):
                    tabs.append((ts.get(_q("style", "position")), ts.get(_q("style", "type")) or "left",
                                 ts.get(_q("style", "leader-style")) or "none"))
            dst["_tabs"] = tabs


class _Styles:
    def __init__(self, content, styles):
        self.fonts = {}
        self.styles = {}
        self.defaults = {}
        self.list_styles = {}
        self.layouts = {}
        self.master_layout = None
        self.auto_names = set()
        roots = [r for r in (styles, content) if r is not None]
        for root in roots:
            for ff in root.iter(_q("style", "font-face")):
                fam = ff.get(_q("svg", "font-family")) or ff.get(_q("style", "name"))
                self.fonts[ff.get(_q("style", "name"))] = (fam or "").strip().strip("'\"")
            for st in root.iter(_q("style", "style")):
                self.styles[(st.get(_q("style", "family")), st.get(_q("style", "name")))] = st
            for au in root.iter(_q("office", "automatic-styles")):
                for st in au.iter(_q("style", "style")):
                    self.auto_names.add((st.get(_q("style", "family")), st.get(_q("style", "name"))))
            for df in root.iter(_q("style", "default-style")):
                self.defaults[df.get(_q("style", "family"))] = df
            for ls in root.iter(_q("text", "list-style")):
                self.list_styles[ls.get(_q("style", "name"))] = ls
            for pl in root.iter(_q("style", "page-layout")):
                self.layouts[pl.get(_q("style", "name"))] = pl
        if styles is not None:
            for mp in styles.iter(_q("style", "master-page")):
                self.master_layout = mp.get(_q("style", "page-layout-name"))
                break

    def chain(self, family, name):
        out, seen, n = [], set(), name
        while n and (family, n) not in seen:
            el = self.styles.get((family, n))
            if el is None:
                break
            out.append(el)
            seen.add((family, n))
            n = el.get(_q("style", "parent-style-name"))
        out.reverse()
        return out

    def named_style(self, name, family="paragraph"):
        """Ближайший именованный (не автоматический) стиль в цепочке: ключ «одного стиля» для contextual-spacing."""
        n, seen = name, set()
        while n and n not in seen:
            seen.add(n)
            if (family, n) not in self.auto_names:
                return n
            el = self.styles.get((family, n))
            n = el.get(_q("style", "parent-style-name")) if el is not None else None
        return n or "Standard"

    def props(self, family, name, kind, base=None):
        d = dict(base or {})
        df = self.defaults.get(family)
        if df is not None and not base:
            for pe in df.findall(_q("style", kind)):
                _merge_into(d, pe)
        for st in self.chain(family, name):
            for pe in st.findall(_q("style", kind)):
                _merge_into(d, pe)
        return d

    def font_family(self, tp, which=""):
        """which: '' (латиница), '-asian', '-complex'."""
        fam = tp.get("fo:font-family") if not which else tp.get("style:font-family" + which)
        if not fam:
            name = tp.get("style:font-name" + which)
            if name:
                fam = self.fonts.get(name) or name
        if not fam:
            return None
        # svg:font-family может быть списком «'Georgia', 'DejaVu Serif'»: берём первое имя
        first = re.split(r"\s*,\s*", fam.strip())[0]
        return first.strip().strip("'\"") or None

    def writing_mode_rtl(self, pp):
        wm = pp.get("style:writing-mode")
        if wm in ("rl-tb", "rl", "tb-rl"):
            return True
        if wm in (None, "page"):
            lay = self.layouts.get(self.master_layout)
            if lay is not None:
                pe = lay.find(_q("style", "page-layout-properties"))
                if pe is not None and pe.get(_q("style", "writing-mode")) in ("rl-tb", "rl"):
                    return True
        return False


# ---------------------------------------------------------------------------
# XML-генераторы DOCX (порядок элементов по схеме)
# ---------------------------------------------------------------------------

def _rpr_xml(st, tp, rtl_text=False):
    latin = st.font_family(tp) or "Times New Roman"
    cs = st.font_family(tp, "-complex") or latin
    ea = st.font_family(tp, "-asian") or latin
    x = '<w:rFonts w:ascii="%s" w:hAnsi="%s" w:eastAsia="%s" w:cs="%s"/>' % (_esc(latin), _esc(latin), _esc(ea), _esc(cs))

    def bold(v):
        return v is not None and (v == "bold" or (v.isdigit() and int(v) >= 600))

    b = bold(tp.get("fo:font-weight"))
    bcs = bold(tp.get("style:font-weight-complex")) if tp.get("style:font-weight-complex") is not None else b
    if b:
        x += "<w:b/>"
    if bcs:
        x += "<w:bCs/>"
    it = tp.get("fo:font-style") in ("italic", "oblique")
    itcs = (tp.get("style:font-style-complex") in ("italic", "oblique")) if tp.get("style:font-style-complex") is not None else it
    if it:
        x += "<w:i/>"
    if itcs:
        x += "<w:iCs/>"
    if tp.get("fo:text-transform") == "uppercase":
        x += "<w:caps/>"
    if tp.get("fo:font-variant") == "small-caps":
        x += "<w:smallCaps/>"
    ls = tp.get("style:text-line-through-style")
    if ls and ls != "none":
        x += "<w:strike/>"
    col = _hex(tp.get("fo:color"))
    if col:
        x += '<w:color w:val="%s"/>' % col
    size = _pt(tp.get("fo:font-size")) or 11.0
    sz = max(2, int(round(size * 2)))
    szc = _pt(tp.get("style:font-size-complex"))
    szcs = max(2, int(round(szc * 2))) if szc else sz
    x += '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (sz, szcs)
    us = tp.get("style:text-underline-style")
    if us and us != "none":
        x += '<w:u w:val="single"/>'
    pos = tp.get("style:text-position")
    if pos:
        first = pos.split()[0]
        if first.startswith("super") or (re.match(r"^-?\d+%?$", first) and int(first.rstrip("%")) > 0):
            x += '<w:vertAlign w:val="superscript"/>'
        elif first.startswith("sub") or (re.match(r"^-?\d+%?$", first) and int(first.rstrip("%")) < 0):
            x += '<w:vertAlign w:val="subscript"/>'
    if rtl_text:
        x += "<w:rtl/>"
    return "<w:rPr>%s</w:rPr>" % x


_BORDER_VAL = {"solid": "single", "double": "double", "dotted": "dotted", "dashed": "dashed", "groove": "single",
               "ridge": "single", "inset": "single", "outset": "single"}


def _parse_border(value):
    """'0.74pt solid #2e75b6' -> (sz_eighths, val, color) или None."""
    if not value or value.strip() == "none":
        return None
    parts = value.split()
    width = None
    style = "solid"
    color = "auto"
    for part in parts:
        if _hex(part):
            color = _hex(part)
        elif part in _BORDER_VAL or part == "hidden":
            style = part
        else:
            w = _pt(part)
            if w is not None:
                width = w
    if width is None or width <= 0 or style == "hidden":
        return None
    return max(2, min(96, int(round(width * 8)))), _BORDER_VAL.get(style, "single"), color


def _border_side(props, side):
    return _parse_border(props.get("fo:border-" + side) if props.get("fo:border-" + side) is not None else props.get("fo:border"))


def _pad_side(props, side):
    v = props.get("fo:padding-" + side) if props.get("fo:padding-" + side) is not None else props.get("fo:padding")
    p = _pt(v)
    return 0 if p is None else max(0, min(31, int(round(p))))


def _ppr_xml(st, pp, tp, *, num=None, empty=False, in_cell=False):
    x = ""
    if num:
        x += '<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>' % num
    if pp.get("fo:break-before") == "page":
        pass
    bd = ""
    for side in ("top", "left", "bottom", "right"):
        b = _border_side(pp, side)
        if b:
            bd += '<w:%s w:val="%s" w:sz="%d" w:space="%d" w:color="%s"/>' % (side, b[1], b[0], _pad_side(pp, side), b[2])
    if bd:
        x += "<w:pBdr>%s</w:pBdr>" % bd
    left_tw = 0 if num else (_tw(pp.get("fo:margin-left")) or 0)
    tabs = pp.get("_tabs") or []
    tx = ""
    for pos, typ, leader in tabs:
        p = _tw(pos)
        if p is None:
            continue
        val = {"right": "right", "center": "center"}.get(typ, "left")
        ld = {"dotted": "dot", "dash": "hyphen", "solid": "underscore"}.get(leader)
        tx += '<w:tab w:val="%s"%s w:pos="%d"/>' % (val, (' w:leader="%s"' % ld) if ld else "", p + left_tw)
    if tx:
        x += "<w:tabs>%s</w:tabs>" % tx
    rtl = st.writing_mode_rtl(pp)
    if rtl:
        x += "<w:bidi/>"
    before = _tw(pp.get("fo:margin-top")) or 0
    after = _tw(pp.get("fo:margin-bottom")) or 0
    lh = pp.get("fo:line-height")
    line, rule = 240, "auto"
    if lh:
        if lh.strip().endswith("%"):
            line = max(20, int(round(240 * float(lh.strip()[:-1]) / 100.0)))
        else:
            t = _tw(lh)
            if t:
                line, rule = t, "exact"
    elif pp.get("style:line-height-at-least"):
        t = _tw(pp.get("style:line-height-at-least"))
        if t:
            line, rule = t, "atLeast"
    x += '<w:spacing w:before="%d" w:after="%d" w:line="%d" w:lineRule="%s"/>' % (max(0, before), max(0, after), line, rule)
    if not num:
        ind = ""
        r = _tw(pp.get("fo:margin-right"))
        ti = _tw(pp.get("fo:text-indent"))
        if left_tw:
            ind += ' w:left="%d"' % left_tw
        if r:
            ind += ' w:right="%d"' % r
        if ti:
            ind += (' w:firstLine="%d"' % ti) if ti > 0 else (' w:hanging="%d"' % -ti)
        if ind:
            x += "<w:ind%s/>" % ind
    ta = pp.get("fo:text-align")
    jc = None
    if ta == "center":
        jc = "center"
    elif ta == "justify":
        jc = "both"
    elif rtl:
        # LibreOffice читает и пишет start/end физически (start = слева, end = справа) даже в rl-tb, а в DOCX у bidi-абзаца
        # без jc текст у правого края (начало), а jc=right означает конец, то есть левый край (как в корпусе иврита)
        if ta in ("start", "left"):
            jc = "right"
    elif ta in ("end", "right"):
        jc = "right"
    if jc:
        x += '<w:jc w:val="%s"/>' % jc
    if empty:
        size = _pt(tp.get("fo:font-size")) or 11.0
        hp = max(2, int(round(size * 2)))
        x += '<w:rPr><w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr>' % (hp, hp)
    return "<w:pPr>%s</w:pPr>" % x if x else ""


# ---------------------------------------------------------------------------
# Списки -> numbering.xml
# ---------------------------------------------------------------------------

_NUMFMT = {"1": "decimal", "a": "lowerLetter", "A": "upperLetter", "i": "lowerRoman", "I": "upperRoman"}


class _Numbering:
    def __init__(self, st):
        self.st = st
        self.abstract = {}      # имя стиля списка -> abstractNumId
        self.abstract_xml = []
        self.nums = []          # (numId, abstractId, [(ilvl, start)] переопределения)
        self.base_num = {}      # имя стиля -> numId без переопределения
        self.last_num = {}      # имя стиля -> numId последнего списка
        self.next_abs = 100
        self.next_num = 100

    def _abstract_id(self, style_name):
        if style_name in self.abstract:
            return self.abstract[style_name]
        aid = self.next_abs
        self.next_abs += 1
        self.abstract[style_name] = aid
        ls = self.st.list_styles.get(style_name)
        lvls = ""
        if ls is not None:
            for lv in ls:
                tag = _local(lv)
                if tag not in ("list-level-style-bullet", "list-level-style-number", "list-level-style-image"):
                    continue
                n = int(lv.get(_q("text", "level")) or 1)
                lvls += self._lvl_xml(lv, tag, n)
        self.abstract_xml.append('<w:abstractNum %s w:abstractNumId="%d"><w:multiLevelType w:val="multilevel"/>%s</w:abstractNum>'
                                 % (nsdecls("w"), aid, lvls))
        return aid

    @staticmethod
    def level_is_bullet(st, style_name, depth):
        ls = st.list_styles.get(style_name)
        if ls is None:
            return True
        for lv in ls:
            if _local(lv).startswith("list-level-style") and int(lv.get(_q("text", "level")) or 1) == depth + 1:
                return _local(lv) != "list-level-style-number"
        return True

    def _lvl_xml(self, lv, tag, n):
        ilvl = n - 1
        left, hang = 0, 0
        lp = lv.find(_q("style", "list-level-properties"))
        if lp is not None:
            la = lp.find(_q("style", "list-level-label-alignment"))
            if la is not None:
                left = _tw(la.get(_q("fo", "margin-left"))) or 0
                ti = _tw(la.get(_q("fo", "text-indent"))) or 0
                hang = -ti if ti < 0 else 0
            else:
                sb = _tw(lp.get(_q("text", "space-before"))) or 0
                mw = _tw(lp.get(_q("text", "min-label-width"))) or 0
                left, hang = sb + mw, mw
        ind = '<w:pPr><w:ind w:left="%d" w:hanging="%d"/></w:pPr>' % (left, hang)
        start = lv.get(_q("text", "start-value")) or "1"
        if tag == "list-level-style-number":
            fmt = _NUMFMT.get(lv.get(_q("style", "num-format")) or "", "decimal" if lv.get(_q("style", "num-format")) else "none")
            fl = lv.get(_q("loext", "num-list-format"))
            if fl:
                text = re.sub(r"%(\d+)%", r"%\1", fl)
            else:
                text = (lv.get(_q("style", "num-prefix")) or "") + "%" + str(n) + (lv.get(_q("style", "num-suffix")) or "")
            return ('<w:lvl w:ilvl="%d"><w:start w:val="%s"/><w:numFmt w:val="%s"/><w:lvlText w:val="%s"/><w:lvlJc w:val="left"/>%s</w:lvl>'
                    % (ilvl, _esc(start), fmt, _esc(text), ind))
        ch = lv.get(_q("text", "bullet-char")) or "\u2022"
        if tag == "list-level-style-image" or ord(ch[0]) >= 0xF000 or ch in ("\u25cf", "\u2022", "\u25aa", "\u25a0", "\uf0b7", "\u2023"):
            ch = "\u2022"
        rpr = '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial" w:hint="default"/></w:rPr>'
        return ('<w:lvl w:ilvl="%d"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="%s"/><w:lvlJc w:val="left"/>%s%s</w:lvl>'
                % (ilvl, _esc(ch), ind, rpr))

    def num_for(self, style_name, depth, continue_numbering, start_value, bullet):
        """numId для списка (новый или продолжение)."""
        aid = self._abstract_id(style_name)
        key = style_name if bullet else (style_name, depth)       # маркеры: один num на стиль, нумерация: по уровням
        if (bullet or continue_numbering) and key in self.last_num:
            return self.last_num[key]
        nid = self._new_num(aid, [] if bullet else [(depth, start_value or 1)])
        self.last_num[key] = nid
        return nid

    def _new_num(self, aid, overrides):
        nid = self.next_num
        self.next_num += 1
        self.nums.append((nid, aid, overrides))
        return nid

    def install(self, doc):
        if not self.nums:
            return
        numbering = doc.part.numbering_part.element
        first_num = numbering.find(qn("w:num"))
        for ax in self.abstract_xml:
            el = parse_xml(ax)
            if first_num is not None:
                first_num.addprevious(el)
            else:
                numbering.append(el)
        for nid, aid, ov in self.nums:
            o = "".join('<w:lvlOverride w:ilvl="%d"><w:startOverride w:val="%d"/></w:lvlOverride>' % (lvl, sv) for lvl, sv in ov)
            numbering.append(parse_xml('<w:num %s w:numId="%d"><w:abstractNumId w:val="%d"/>%s</w:num>' % (nsdecls("w"), nid, aid, o)))


# ---------------------------------------------------------------------------
# Конвертер
# ---------------------------------------------------------------------------

class _PX(str):
    """Абзац (XML-строка) с метаданными для эмуляции contextual-spacing (ODT сравнивает именованный стиль абзаца)."""
    style_key = None
    ctx = False


def _apply_contextual(out):
    """Между соседними абзацами одного именованного стиля с contextual-spacing интервал убирается (как в LibreOffice)."""
    res = list(out)
    for i, item in enumerate(res):
        if not isinstance(item, _PX) or not item.ctx:
            continue
        txt = str(item)
        if i > 0 and isinstance(res[i - 1], _PX) and res[i - 1].style_key == item.style_key:
            txt = re.sub(r'w:before="\d+"', 'w:before="0"', txt, count=1)
        if i + 1 < len(res) and isinstance(res[i + 1], _PX) and res[i + 1].style_key == item.style_key:
            txt = re.sub(r'w:after="\d+"', 'w:after="0"', txt, count=1)
        if txt != str(item):
            new = _PX(txt)
            new.style_key, new.ctx = item.style_key, item.ctx
            res[i] = new
    return res


class _Converter:
    def __init__(self, content, styles):
        self.st = _Styles(content, styles)
        self.content = content
        self.num = _Numbering(self.st)
        self.page_text_w = 9638

    def named_style(self, name):
        return self.st.named_style(name)

    # --- фрагменты текста ---
    def _walk(self, el, tp, items, state):
        def add_text(s, props):
            if not s:
                return
            s = _WS.sub(" ", s)
            if state["last_space"] and s.startswith(" "):
                s = s[1:]
            if not state["started"]:
                s = s.lstrip(" ")
            if not s:
                return
            state["started"] = True
            state["last_space"] = s.endswith(" ")
            items.append(("t", s, props))

        add_text(el.text, tp)
        for ch in el:
            if not isinstance(ch.tag, str):
                add_text(ch.tail, tp)
                continue
            name = _local(ch) if _pfx(ch) == "text" else ""
            if name == "span":
                sn = ch.get(_q("text", "style-name"))
                sub = self.st.props("text", sn, "text-properties", base=tp) if sn else tp
                self._walk(ch, sub, items, state)
            elif name in ("a", "ruby", "ruby-base", "meta", "meta-field", "bookmark-ref", "reference-ref"):
                self._walk(ch, tp, items, state)
            elif name == "s":
                n = int(ch.get(_q("text", "c")) or 1)
                state["started"] = True
                state["last_space"] = False
                items.append(("t", " " * max(1, min(n, 200)), tp))
            elif name == "tab":
                state["started"] = True
                state["last_space"] = False
                items.append(("tab", None, tp))
            elif name == "line-break":
                state["started"] = True
                state["last_space"] = False
                items.append(("br", None, tp))
            add_text(ch.tail, tp)

    def _runs_xml(self, p_el, tp, rtl_para):
        items, state = [], {"started": False, "last_space": False}
        self._walk(p_el, tp, items, state)
        # убрать хвостовой пробел абзаца
        while items and items[-1][0] == "t" and items[-1][1].endswith(" ") and items[-1][1].strip(" ") == "":
            items.pop()
        if items and items[-1][0] == "t" and items[-1][1].endswith(" "):
            items[-1] = ("t", items[-1][1].rstrip(" "), items[-1][2])
        runs = []     # [rpr_xml, [parts]]
        for kind, payload, props in items:
            if kind == "t":
                rpr = _rpr_xml(self.st, props, rtl_text=bool(_RTL_RE.search(payload)))
                part = '<w:t xml:space="preserve">%s</w:t>' % _esc(payload)
            elif kind == "tab":
                rpr, part = _rpr_xml(self.st, props), "<w:tab/>"
            else:
                rpr, part = _rpr_xml(self.st, props), "<w:br/>"
            if runs and runs[-1][0] == rpr:
                if kind == "t" and runs[-1][1] and runs[-1][1][-1].startswith("<w:t "):
                    prev = runs[-1][1][-1]
                    runs[-1][1][-1] = prev[:-len("</w:t>")] + _esc(payload) + "</w:t>"
                else:
                    runs[-1][1].append(part)
            else:
                runs.append([rpr, [part]])
        has_text = any(k == "t" and p.strip() for k, p, _ in items)
        return "".join("<w:r>%s%s</w:r>" % (r, "".join(parts)) for r, parts in runs), has_text

    def _paragraph(self, p_el, num=None):
        sn = p_el.get(_q("text", "style-name"))
        pp = self.st.props("paragraph", sn, "paragraph-properties")
        tp = self.st.props("paragraph", sn, "text-properties")
        runs, has_text = self._runs_xml(p_el, tp, self.st.writing_mode_rtl(pp))
        ppr = _ppr_xml(self.st, pp, tp, num=num, empty=not runs)
        if pp.get("fo:break-before") == "page":
            ppr = ppr.replace("<w:pPr>", "<w:pPr><w:pageBreakBefore/>", 1) if ppr else "<w:pPr><w:pageBreakBefore/></w:pPr>"
        px = _PX('<w:p %s>%s%s</w:p>' % (nsdecls("w"), ppr, runs))
        px.ctx = pp.get("style:contextual-spacing") == "true"
        px.style_key = self.named_style(sn)
        return px, has_text

    # --- списки ---
    def _list(self, l_el, depth, out, style_name=None, stats=None):
        sn = l_el.get(_q("text", "style-name")) or style_name
        cont = (l_el.get(_q("text", "continue-numbering")) == "true")
        first = True
        for item in l_el:
            if not isinstance(item.tag, str) or _pfx(item) != "text" or _local(item) not in ("list-item", "list-header"):
                continue
            start = item.get(_q("text", "start-value"))
            for ch in item:
                if not isinstance(ch.tag, str):
                    continue
                if _is(ch, "text", "p") or _is(ch, "text", "h"):
                    bullet = _Numbering.level_is_bullet(self.st, sn, depth)
                    nid = self.num.num_for(sn or "_", depth, cont or not first, int(start) if start and start.isdigit() else None, bullet)
                    xml, ht = self._paragraph(ch, num=(min(depth, 8), nid))
                    out.append(xml)
                    if stats is not None:
                        stats["text"] = stats["text"] or ht
                    first = False
                elif _is(ch, "text", "list"):
                    self._list(ch, depth + 1, out, sn, stats)

    # --- таблицы ---
    def _table(self, t_el, out, stats):
        sn = t_el.get(_q("table", "style-name"))
        tprops = self.st.props("table", sn, "table-properties")
        cols = []
        rows = []

        def collect(parent):
            for ch in parent:
                if not isinstance(ch.tag, str) or _pfx(ch) != "table":
                    continue
                n = _local(ch)
                if n == "table-column":
                    rep = int(ch.get(_q("table", "number-columns-repeated")) or 1)
                    cp = self.st.props("table-column", ch.get(_q("table", "style-name")), "table-column-properties")
                    w = _tw(cp.get("style:column-width"))
                    cols.extend([w] * min(rep, 100))
                elif n in ("table-columns", "table-header-columns"):
                    collect(ch)
                elif n == "table-row":
                    rows.append(ch)
                elif n in ("table-rows", "table-header-rows", "table-row-group"):
                    collect(ch)
        collect(t_el)
        if not rows or not cols:
            return
        known = [c for c in cols if c]
        if len(known) != len(cols):
            fill = int((sum(known) if known else self.page_text_w) / max(1, len(known) or 1)) if known else int(self.page_text_w / len(cols))
            cols = [c or fill for c in cols]
        total = sum(cols)
        tw = _tw(tprops.get("style:width")) or total
        rtl = tprops.get("style:writing-mode") in ("rl-tb", "rl")
        jc = ""
        al = tprops.get("table:align")
        if al == "center":
            jc = '<w:jc w:val="center"/>'
        elif rtl:
            # у bidi-таблицы начало справа: jc=right это левый край. Для таблицы на всю ширину jc не нужен
            if al == "left" and tw < self.page_text_w - 20:
                jc = '<w:jc w:val="right"/>'
        elif al == "right":
            jc = '<w:jc w:val="right"/>'
        ml = _tw(tprops.get("fo:margin-left"))
        # DOCX считает отступ таблицы до текста первой ячейки (LibreOffice вычитает левое поле ячейки), ODT до края таблицы
        pad = self._first_cell_padding(rows)
        if rtl:
            # bidi-таблица: текст первой ячейки у поля при отступе 0 (граница выступает на поле ячейки);
            # сдвиг текста относительно поля (ml + поле) переводим в tblInd от правого края
            shift = (ml or 0) + pad
            ml = -shift if abs(shift) > 5 else None
        elif ml is not None or pad:
            ml = (ml or 0) + pad
        tpr = ("<w:bidiVisual/>" if rtl else "") + '<w:tblW w:w="%d" w:type="dxa"/>' % tw + jc
        if ml:
            tpr += '<w:tblInd w:w="%d" w:type="dxa"/>' % ml
        tpr += '<w:tblLayout w:type="fixed"/>'
        grid = "".join('<w:gridCol w:w="%d"/>' % c for c in cols)
        pending = {}      # колонка -> (осталось строк)
        row_xml = []
        for row in rows:
            cx = ""
            ci = 0
            for cell in row:
                if not isinstance(cell.tag, str) or _pfx(cell) != "table":
                    continue
                kind = _local(cell)
                rep = int(cell.get(_q("table", "number-columns-repeated")) or 1)
                if kind == "covered-table-cell":
                    for _ in range(min(rep, 100)):
                        if pending.get(ci, 0) > 0:
                            span_w = cols[ci] if ci < len(cols) else cols[-1]
                            cx += ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/><w:vMerge/></w:tcPr><w:p/></w:tc>' % span_w)
                            pending[ci] -= 1
                        ci += 1
                    continue
                if kind != "table-cell":
                    continue
                for _ in range(min(rep, 100)):
                    span = int(cell.get(_q("table", "number-columns-spanned")) or 1)
                    rspan = int(cell.get(_q("table", "number-rows-spanned")) or 1)
                    width = sum(cols[ci:ci + span]) if ci < len(cols) else cols[-1]
                    cx += self._cell(cell, width, span, rspan, stats)
                    if rspan > 1:
                        pending[ci] = rspan - 1
                    ci += span
            row_xml.append("<w:tr>%s</w:tr>" % cx)
        # LibreOffice склеивает подряд идущие таблицы DOCX в одну многострочную; разбор ролей Creative и «таблица на строку»
        # в корпусе DOCX рассчитаны на однострочные таблицы, поэтому без вертикальных объединений режем таблицу по строкам
        merged_rows = any("<w:vMerge" in r for r in row_xml)
        groups = [row_xml] if merged_rows else [[r] for r in row_xml]
        for g in groups:
            out.append('<w:tbl %s><w:tblPr>%s</w:tblPr><w:tblGrid>%s</w:tblGrid>%s</w:tbl>' % (nsdecls("w"), tpr, grid, "".join(g)))

    def _first_cell_padding(self, rows):
        for row in rows:
            for cell in row:
                if isinstance(cell.tag, str) and _local(cell) == "table-cell":
                    cp = self.st.props("table-cell", cell.get(_q("table", "style-name")), "table-cell-properties")
                    v = cp.get("fo:padding-left") if cp.get("fo:padding-left") is not None else cp.get("fo:padding")
                    return _tw(v) or 0
        return 0

    def _cell(self, cell, width, span, rspan, stats):
        cp = self.st.props("table-cell", cell.get(_q("table", "style-name")), "table-cell-properties")
        pr = '<w:tcW w:w="%d" w:type="dxa"/>' % width
        if span > 1:
            pr += '<w:gridSpan w:val="%d"/>' % span
        if rspan > 1:
            pr += '<w:vMerge w:val="restart"/>'
        bd = ""
        for side in ("top", "left", "bottom", "right"):
            b = _border_side(cp, side)
            if b:
                bd += '<w:%s w:val="%s" w:sz="%d" w:space="0" w:color="%s"/>' % (side, b[1], b[0], b[2])
        if bd:
            pr += "<w:tcBorders>%s</w:tcBorders>" % bd
        bg = _hex(cp.get("fo:background-color"))
        if bg:
            pr += '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % bg
        mar = ""
        for side, tag in (("top", "top"), ("left", "left"), ("bottom", "bottom"), ("right", "right")):
            v = cp.get("fo:padding-" + side) if cp.get("fo:padding-" + side) is not None else cp.get("fo:padding")
            t = _tw(v)
            if t is not None:
                mar += '<w:%s w:w="%d" w:type="dxa"/>' % (tag, t)
        if mar:
            pr += "<w:tcMar>%s</w:tcMar>" % mar
        va = cp.get("style:vertical-align")
        if va in ("middle", "bottom"):
            pr += '<w:vAlign w:val="%s"/>' % ("center" if va == "middle" else "bottom")
        inner = []
        self._blocks(cell, inner, stats, in_cell=True)
        inner = _apply_contextual(inner)
        body = "".join(inner) or "<w:p/>"
        return "<w:tc><w:tcPr>%s</w:tcPr>%s</w:tc>" % (pr, body)

    # --- блоки ---
    def _blocks(self, parent, out, stats, in_cell=False):
        for ch in parent:
            if not isinstance(ch.tag, str):
                continue
            pf, n = _pfx(ch), _local(ch)
            if pf == "text" and n in ("p", "h"):
                xml, ht = self._paragraph(ch)
                out.append(xml)
                stats["text"] = stats["text"] or ht
            elif pf == "text" and n == "list":
                self._list(ch, 0, out, None, stats)
            elif pf == "table" and n == "table":
                if in_cell:
                    continue                         # вложенные таблицы игнорируются
                self._table(ch, out, stats)
            elif pf == "text" and n in ("section", "index-body", "table-of-content", "alphabetical-index", "illustration-index"):
                self._blocks(ch, out, stats, in_cell)

    def _sect_xml(self):
        lay = self.st.layouts.get(self.st.master_layout)
        w, h, tp, bt, lf, rt = 11906, 16838, 1134, 1134, 1134, 1134
        if lay is not None:
            pe = lay.find(_q("style", "page-layout-properties"))
            if pe is not None:
                w = _tw(pe.get(_q("fo", "page-width"))) or w
                h = _tw(pe.get(_q("fo", "page-height"))) or h
                tp = _tw(pe.get(_q("fo", "margin-top"))) or tp
                bt = _tw(pe.get(_q("fo", "margin-bottom"))) or bt
                lf = _tw(pe.get(_q("fo", "margin-left"))) or lf
                rt = _tw(pe.get(_q("fo", "margin-right"))) or rt
        self.page_text_w = max(1000, w - lf - rt)
        orient = ' w:orient="landscape"' if w > h else ""
        return ('<w:sectPr %s><w:pgSz w:w="%d" w:h="%d"%s/><w:pgMar w:top="%d" w:right="%d" w:bottom="%d" w:left="%d" '
                'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>' % (nsdecls("w"), w, h, orient, tp, rt, bt, lf))

    def convert(self):
        body_el = self.content.find(_q("office", "body"))
        text_el = body_el.find(_q("office", "text")) if body_el is not None else None
        if text_el is None:
            raise NormalizeRejected("no_text_body")
        sect = self._sect_xml()
        out, stats = [], {"text": False}
        self._blocks(text_el, out, stats)
        out = _apply_contextual(out)
        if not stats["text"]:
            raise NormalizeRejected("no_text")
        doc = Document()
        cp = doc.core_properties
        fixed = datetime.datetime(2020, 1, 1, 0, 0, 0)
        cp.author, cp.last_modified_by, cp.title = "ResumeAI", "ResumeAI", ""
        cp.created = cp.modified = fixed
        body = doc.element.body
        for child in list(body):
            body.remove(child)
        for xml in out:
            body.append(parse_xml(xml))
        body.append(parse_xml(sect))
        self.num.install(doc)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()


def odt_to_docx(file_bytes):
    """ODT (bytes) -> DOCX (bytes). NormalizeRejected при неподдерживаемом или повреждённом входе."""
    content, styles = _open_odt(file_bytes)
    return _Converter(content, styles).convert()
