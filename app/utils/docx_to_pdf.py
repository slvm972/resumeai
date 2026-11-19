"""
Cycle X4: конвертер DOCX -> PDF с сохранением оформления (парный к docx_to_odt X1 и docx_to_rtf X3).

Чистый Python на reportlab (canvas) без внешних программ. Это небольшой собственный движок вёрстки:
абзацы разбиваются на строки по словам, таблицы считаются по сетке, страницы переключаются сами.

Переносит: размер страницы и поля; шрифт (serif/sans), размер, жирный, курсив, подчёркивание,
зачёркивание, цвет, верхний/нижний индекс; выравнивание (влево, по центру, вправо, по ширине), отступы,
интервалы, междустрочный, верхнюю и нижнюю границы абзаца (линия под заголовком), заливку, табы
(правые для дат), списки (маркер/номер с висячим отступом), таблицы (сетка, рамки ячеек, заливка,
gridSpan, вертикальное выравнивание), разрыв страницы, перенос на следующие страницы.

Шрифты: латиница и кириллица, Fira Sans (sans) и Liberation Serif (serif) с начертаниями
Regular/Bold/Italic/BoldItalic из static/fonts. Иврит, арабский и китайский рисуются так же, как в
_generate_pdf (missing_routes4): Alef, NotoSansArabic (+arabic_reshaper), WenQuanYi и bidi.get_display;
у них нет Bold/Italic-файлов, жирный имитируется обводкой, курсив не передаётся.

RTL-строки рисуются целиком одним шрифтом (как в _generate_pdf): внутри такого абзаца цвет и
размер берутся от первого фрагмента, подчёркивание и жирный от него же.

Не переносится: картинки, вложенные таблицы, колонтитулы, сноски, гиперссылки (остаётся текст),
левая/правая границы абзаца, вертикальное объединение ячеек, цвета темы, номера страниц,
выравнивание по ширине в RTL, перенос строк внутри текста после табуляции (кроме списков).

Главная функция: docx_bytes_to_pdf(docx_bytes) -> bytes. Исключения не глушит.
Разбор стилей переиспользует app.utils.docx_to_odt (_Converter) и мелкие функции docx_to_rtf.
"""
import io
import os
import re

from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph

from app.utils.docx_to_odt import (_Converter, _val, _on, _NUMFMT, _style_chain_props)
from app.utils.docx_to_rtf import _number_label, _BULLET_TEXT

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "static", "fonts")
_SERIF_HINT = ("times", "cambria", "georgia", "garamond", "palatino", "book antiqua", "constantia",
               "baskerville", "caladea", "liberation serif", "tinos", "serif")
_HEB = re.compile("[\u0590-\u05ff]")
_ARA = re.compile("[\u0600-\u06ff\u0750-\u077f]")
_CJK = re.compile("[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")
_TOKEN = re.compile("\t|\n|[ \u00a0]+|[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]|[^\t\n \u00a0\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]+")

# Символы, которых нет в cmap используемых шрифтов (проверено по реальным файлам static/fonts):
# в Fira Sans нет узких и пунктуационных пробелов U+2002..U+2006, U+2009, U+200A, U+202F, дефисов U+2010/U+2011,
# U+2032, U+2023, U+25E6, U+25AA, U+27A4, U+2713; в Liberation Serif нет U+2011, U+27A4, U+2713; нигде нет U+2060.
# Без замены в PDF видны пустые рамки (.notdef). Обычные кавычки, тире и U+00A0 в шрифтах есть, их не трогаем.
_CHAR_MAP = {}
for _cp in (0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2008, 0x2009, 0x200A, 0x202F):
    _CHAR_MAP[_cp] = " "
_CHAR_MAP.update({0x2010: "-", 0x2011: "-", 0x2060: "", 0xFEFF: "", 0x2032: "'", 0x2023: "\u2022",
                  0x25E6: "\u2022", 0x25AA: "\u2022", 0x27A4: ">", 0x2713: "\u2022"})


def _clean(text):
    """Заменить символы, отсутствующие в шрифтах PDF, на безопасные (остальной текст не меняется)."""
    return text.translate(_CHAR_MAP) if text else text


_registered = False


def _register_fonts():
    """Зарегистрировать латинские/кириллические семейства; Alef/Arabic/CJK через механизм _generate_pdf."""
    global _registered
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from app import missing_routes4 as mr
    mr._ensure_pdf_font_registered()
    if _registered:
        return
    for fam, base in (("X4Sans", "FiraSans"), ("X4Serif", "LiberationSerif")):
        for suffix, fname in (("", "Regular"), ("-B", "Bold"), ("-I", "Italic"), ("-BI", "BoldItalic")):
            pdfmetrics.registerFont(TTFont(fam + suffix, os.path.join(_FONT_DIR, "%s-%s.ttf" % (base, fname))))
    _registered = True


def _latin_font(name, bold, italic):
    low = (name or "").lower()
    serif = "sans" not in low and any(h in low for h in _SERIF_HINT)
    return ("X4Serif" if serif else "X4Sans") + ("-" + ("B" if bold else "") + ("I" if italic else "")
                                                 if (bold or italic) else "")


def _hexcolor(h):
    from reportlab.lib.colors import HexColor
    return HexColor("#" + (h or "000000"))


class _PdfConverter(_Converter):
    def __init__(self, docx_bytes):
        super().__init__(docx_bytes)
        _register_fonts()
        from reportlab.pdfbase import pdfmetrics
        from app import missing_routes4 as mr
        self.pm = pdfmetrics
        self.F_HEB = mr._PDF_FONT_NAME
        self.F_ARA = mr._PDF_FONT_NAME_ARABIC
        self.F_CJK = mr._PDF_FONT_NAME_CJK
        self._counters = {}
        sec = self.doc.sections[0]
        self.page_w = sec.page_width / 12700.0
        self.page_h = sec.page_height / 12700.0
        self.m_left = sec.left_margin / 12700.0
        self.m_right = sec.right_margin / 12700.0
        self.m_top = sec.top_margin / 12700.0
        self.m_bottom = sec.bottom_margin / 12700.0
        self.text_w = self.page_w - self.m_left - self.m_right

    # ---------------- вспомогательное ----------------

    def _sw(self, text, font, size):
        return self.pm.stringWidth(text, font, size)

    def _covers(self, font, text):
        """Все ли символы текста есть в шрифте (по cmap зарегистрированного TTF)."""
        try:
            cmap = self.pm.getFont(font).face.charToGlyph
        except Exception:
            return True
        return all(ord(ch) in cmap for ch in text if ch not in "\t\n ")

    def _asc_desc(self, font, size):
        return self.pm.getAscent(font, size), -self.pm.getDescent(font, size)

    @staticmethod
    def _tw(v):
        return int(v) / 20.0

    def _label(self, num_id, ilvl, info):
        fmt, txt = info[0], info[1]
        if fmt in ("bullet", "none") or fmt not in _NUMFMT:
            ch = _BULLET_TEXT.get(txt, txt) or "\u2022"
            if ord(ch[0]) >= 0xE000:
                ch = "\u2022"
            return ch[0]
        key = (num_id, ilvl)
        n = self._counters.get(key, 0) + 1
        self._counters[key] = n
        for k in [k for k in self._counters if k[0] == num_id and k[1] > ilvl]:
            del self._counters[k]
        label = txt or "%1."
        for lv in range(ilvl + 1):
            cur = self._counters.get((num_id, lv), 1)
            sub = self._numbering_info(num_id, lv)
            label = label.replace("%%%d" % (lv + 1), _number_label(sub[0] if sub else fmt, cur))
        return label

    def _brd(self, el):
        """(толщина pt, hex, отступ pt, dash) или None."""
        if el is None:
            return None
        v = (el.get(qn("w:val")) or "").lower()
        if v in ("nil", "none", ""):
            return None
        sz = el.get(qn("w:sz"))
        width = max(0.25, int(sz) / 8.0) if sz else 0.5
        color = el.get(qn("w:color")) or "000000"
        if color.lower() == "auto" or not re.match(r"^[0-9A-Fa-f]{6}$", color):
            color = "000000"
        space = el.get(qn("w:space"))
        dash = (1, 2) if v == "dotted" else ((4, 2) if v == "dashed" else None)
        return width, color.upper(), float(space) if space else 0.0, dash

    # ---------------- токены ----------------

    def _tok(self, kind, text, props, para_runs=None):
        (font, cs_font, size, size_cs, bold, bold_cs, italic, italic_cs,
         underline, color, strike, caps, small, va) = props
        text = _clean(text)
        if caps or small:
            text = text.upper()
        fsize = size * 0.65 if va in ("superscript", "subscript") else size
        fb = False
        if _CJK.search(text):
            f = self.F_CJK
            fb = bold
        else:
            f = _latin_font(font, bold, italic)
            if f.startswith("X4Serif") and not self._covers(f, text):
                alt = "X4Sans" + f[len("X4Serif"):]        # например ₽ есть в Fira Sans, но нет в Liberation Serif
                if self._covers(alt, text):
                    f = alt
        shift = fsize * 0.5 if va == "superscript" else (-fsize * 0.25 if va == "subscript" else 0)
        return {"k": kind, "t": text, "f": f, "s": fsize, "col": color, "ul": underline, "st": strike,
                "fb": fb, "sh": shift, "w": self._sw(text, f, fsize) if kind != "t" else 0.0}

    def _para_tokens(self, para):
        toks = []
        for item in para.iter_inner_content():
            runs = item.runs if isinstance(item, Hyperlink) else [item]
            for r in runs:
                text = _clean(r.text)
                if not text:
                    continue
                rpr = r._r.rPr
                rstyle = r.style if rpr is not None and rpr.find(qn("w:rStyle")) is not None else None
                props = self._run_props(rstyle, para.style, rpr)
                for m in _TOKEN.finditer(text):
                    t = m.group(0)
                    if t == "\t":
                        toks.append(self._tok("t", "", props))
                    elif t == "\n":
                        toks.append(self._tok("n", "", props))
                    elif t[0] in " \u00a0":
                        toks.append(self._tok("s", " " * len(t), props))
                    else:
                        toks.append(self._tok("w", t, props))
        return toks

    # ---------------- раскладка абзаца ----------------

    def _layout(self, para, width, ctx_extra=None):
        ctx = self._para_ctx(para)
        text = para.text
        has_heb = bool(_HEB.search(text))
        has_ara = bool(_ARA.search(text)) and not has_heb
        is_rtl = self._is_rtl(para, ctx)
        num_id, ilvl = ctx["num_id"], ctx["ilvl"]
        info = self._numbering_info(num_id, ilvl) if num_id else None
        in_list = info is not None and info[0] != "none"

        left = self._tw(ctx["left"] or 0)
        right = self._tw(ctx["right"] or 0)
        first = self._tw(ctx["first"] or 0)
        hang = self._tw(ctx["hanging"] or 0)
        label_x = None
        if in_list:
            if ctx["ind_direct"] and ctx["left"] is not None:
                left = self._tw(ctx["left"])
            else:
                left = self._tw(info[2])
            h = self._tw(ctx["hanging"]) if ctx["ind_direct"] and ctx["hanging"] is not None else self._tw(info[3])
            label_x = left - h
            first = 0.0
        elif hang:
            first = -hang

        base = self._run_props(None, para.style, None)
        before = self._tw(ctx["before"] or 0)
        after = self._tw(ctx["after"] or 0)
        line_mult, line_exact, line_min = 1.0, None, None
        if ctx["line"] is not None:
            ln = int(ctx["line"])
            if ctx["rule"] == "auto":
                line_mult = ln / 240.0
            elif ctx["rule"] == "exact":
                line_exact = ln / 20.0
            else:
                line_min = ln / 20.0

        borders = {}
        for side in ("top", "bottom"):
            from app.utils.docx_to_odt import _style_chain_props as scp
            chain = [para._p.pPr] + scp(para.style, "pPr") + self._tbl_ppr + [self._defaults_ppr]
            for props in chain:
                if props is None:
                    continue
                bd = props.find(qn("w:pBdr"))
                if bd is not None and bd.find(qn("w:" + side)) is not None:
                    b = self._brd(bd.find(qn("w:" + side)))
                    if b:
                        borders[side] = b
                    break

        jc = ctx["jc"]
        lines = []

        def make_line(items, asc, desc):
            nat = asc + desc
            if line_exact is not None:
                h = line_exact
                asc2 = asc * (h / nat) if nat else h
            elif line_min is not None:
                h = max(nat * line_mult, line_min)
                asc2 = asc + (h - nat)
            else:
                h = nat * line_mult
                asc2 = asc + (h - nat)
            return {"h": h, "asc": asc2, "items": items}

        if has_heb or has_ara:
            label_tok = self._tok("w", self._label(num_id, ilvl, info), base) if in_list else None
            lines = self._layout_rtl(para, ctx, width, left, right, first, is_rtl, has_heb, jc, make_line,
                                     label_x=label_x, label_tok=label_tok)
        else:
            lines = self._layout_ltr(para, ctx, width, left, right, first, jc, label_x, in_list,
                                     info, num_id, ilvl, base, make_line)
        return {"lines": lines, "before": before, "after": after, "borders": borders,
                "fill": ctx["fill"], "page_break": ctx["page_break"], "left": left, "right": right,
                "is_rtl": is_rtl}

    def _layout_ltr(self, para, ctx, width, left, right, first, jc, label_x, in_list, info, num_id, ilvl,
                    base, make_line):
        toks = self._para_tokens(para)
        avail_rest = width - left - right
        avail_first = avail_rest - first
        # разбиваем по табам: первый сегмент переносим по словам, остальные кладём в его последнюю строку
        segs = [[]]
        for t in toks:
            if t["k"] == "t":
                segs.append([])
            else:
                segs[-1].append(t)
        lines_tok = [[]]
        cw = 0.0
        avail = avail_first
        for tk in segs[0]:
            if tk["k"] == "n":
                lines_tok.append([])
                cw = 0.0
                avail = avail_rest
                continue
            if tk["k"] == "s" and not lines_tok[-1]:
                continue
            if tk["k"] == "w" and cw + tk["w"] > avail and any(x["k"] == "w" for x in lines_tok[-1]):
                while lines_tok[-1] and lines_tok[-1][-1]["k"] == "s":
                    lines_tok[-1].pop()
                lines_tok.append([])
                cw = 0.0
                avail = avail_rest
            lines_tok[-1].append(tk)
            cw += tk["w"]
        for ln in lines_tok:
            while ln and ln[-1]["k"] == "s" and len(segs) == 1:
                ln.pop()

        stops = []
        for kind, pos, leader in ctx["tabs"]:
            if kind in ("clear", "bar") or pos is None:
                continue
            stops.append((self._tw(pos), kind or "left"))
        stops.sort()
        out = []
        n = len(lines_tok)
        label_tok = None
        if in_list:
            lab = self._label(num_id, ilvl, info)
            label_tok = self._tok("w", lab, base)

        for li, ln in enumerate(lines_tok):
            x0 = left + (first if li == 0 else 0.0)
            lw = sum(t["w"] for t in ln)
            asc = desc = 0.0
            for t in ln + ([label_tok] if label_tok and li == 0 else []):
                a, d = self._asc_desc(t["f"], t["s"])
                asc, desc = max(asc, a), max(desc, d)
            if not ln and not (label_tok and li == 0):
                a, d = self._asc_desc(_latin_font(base[0], base[4], base[6]), base[2])
                asc, desc = a, d
            items = []
            is_last = li == n - 1
            has_tab = is_last and len(segs) > 1
            avail_line = width - right - x0
            if not has_tab:
                if jc == "center":
                    x = x0 + max(0.0, (avail_line - lw) / 2)
                elif jc in ("right", "end"):
                    x = x0 + max(0.0, avail_line - lw)
                else:
                    x = x0
                extra = 0.0
                if jc in ("both", "distribute") and not is_last:
                    spaces = [t for t in ln if t["k"] == "s"]
                    if spaces and lw < avail_line:
                        extra = (avail_line - lw) / len(spaces)
                for t in ln:
                    items.append((x, t))
                    x += t["w"] + (extra if t["k"] == "s" else 0.0)
            else:
                x = x0
                for t in ln:
                    items.append((x, t))
                    x += t["w"]
                cursor = x
                for seg in segs[1:]:
                    sg = list(seg)
                    while sg and sg[-1]["k"] == "s":
                        sg.pop()
                    while sg and sg[0]["k"] == "s":
                        sg.pop(0)
                    segw = sum(t["w"] for t in sg)
                    for t in sg:
                        a, d = self._asc_desc(t["f"], t["s"])
                        asc, desc = max(asc, a), max(desc, d)
                    stop = None
                    for pos, kind in stops:
                        if pos > cursor + 0.5:
                            stop = (pos, kind)
                            break
                    if stop is None:
                        last = stops[-1][0] if stops else 0.0
                        nxt = (int(max(cursor, last) // 36) + 1) * 36.0
                        stop = (nxt, "left")
                    pos = min(stop[0], width - right)
                    if stop[1] in ("right", "end", "decimal"):
                        start = max(cursor, pos - segw)
                    elif stop[1] == "center":
                        start = max(cursor, pos - segw / 2)
                    else:
                        start = pos
                    x = start
                    for t in sg:
                        items.append((x, t))
                        x += t["w"]
                    cursor = x
            if label_tok and li == 0:
                items.insert(0, (label_x, label_tok))
            out.append(make_line(items, asc, desc))
        return out

    def _layout_rtl(self, para, ctx, width, left, right, first, is_rtl, has_heb, jc, make_line,
                    label_x=None, label_tok=None):
        from bidi.algorithm import get_display
        import arabic_reshaper
        runs = []
        for item in para.iter_inner_content():
            for r in (item.runs if isinstance(item, Hyperlink) else [item]):
                if r.text:
                    runs.append(r)
        if not runs:
            return [make_line([], 11.0, 3.0)]
        r0 = runs[0]
        rpr = r0._r.rPr
        rstyle = r0.style if rpr is not None and rpr.find(qn("w:rStyle")) is not None else None
        props = self._run_props(rstyle, para.style, rpr)
        size = props[3]
        bold = props[5]
        font = self.F_HEB if has_heb else self.F_ARA
        text = _clean("".join(r.text for r in runs)).replace("\t", " ").replace("\n", " ")
        if is_rtl:
            # старт абзаца справа: левый отступ Word это отступ от правого края
            phys_left, phys_right = right, left
            first_side = "right"
        else:
            phys_left, phys_right = left, right
            first_side = "left"
        avail_rest = width - phys_left - phys_right
        avail_first = avail_rest - first
        words = text.split(" ")
        lines_txt = [""]
        avail = avail_first
        for w in words:
            cand = (lines_txt[-1] + " " + w) if lines_txt[-1] else w
            if lines_txt[-1] and self._sw(cand, font, size) > avail:
                lines_txt.append(w)
                avail = avail_rest
            else:
                lines_txt[-1] = cand
        if is_rtl:
            amap = {"left": "right", "start": "right", "right": "left", "end": "left", "center": "center",
                    "both": "right", "distribute": "right"}
            align = amap.get(jc, "right")
        else:
            align = {"center": "center", "right": "right", "end": "right"}.get(jc, "left")
        a, d = self._asc_desc(font, size)
        out = []
        for li, ln in enumerate(lines_txt):
            shown = ln
            if not has_heb:
                shown = arabic_reshaper.reshape(ln)
            disp = get_display(shown)
            w = self._sw(disp, font, size)
            ind = first if li == 0 else 0.0
            left_edge = phys_left + (ind if first_side == "left" else 0.0)
            right_edge = width - phys_right - (ind if first_side == "right" else 0.0)
            if align == "right":
                x = right_edge - w
            elif align == "center":
                x = left_edge + (right_edge - left_edge - w) / 2
            else:
                x = left_edge
            tok = {"k": "w", "t": disp, "f": font, "s": size, "col": props[9], "ul": props[8], "st": props[10],
                   "fb": bold, "sh": 0.0, "w": w}
            items = [(x, tok)]
            if label_tok is not None and li == 0 and label_x is not None:
                # Cycle P2: маркер/номер списка стоит на стороне начала абзаца: справа у RTL-абзаца
                lx = (width - label_x - label_tok["w"]) if is_rtl else label_x
                items.insert(0, (lx, label_tok))
            out.append(make_line(items, a, d))
        return out

    # ---------------- рисование ----------------

    def _merge_items(self, line):
        """Склеить соседние токены одного стиля в один отрезок текста (для выделения и копирования текста)."""
        runs = []
        for x, t in line["items"]:
            if t["k"] not in ("w", "s"):
                continue
            key = (t["f"], t["s"], t["col"], t["ul"], t["st"], t["fb"], t["sh"])
            text = t["t"]
            if runs and runs[-1]["key"] == key and abs(x - (runs[-1]["x"] + runs[-1]["w"])) < 0.05:
                runs[-1]["t"] += text
                runs[-1]["w"] += t["w"]
            else:
                runs.append({"key": key, "x": x, "t": text, "w": t["w"]})
        return runs

    def _draw_items(self, c, line, x_origin, baseline):
        for run in self._merge_items(line):
            font, size, color, ul, st, fb, sh = run["key"]
            text = run["t"]
            if not text.strip():
                continue
            col = _hexcolor(color)
            c.setFillColor(col)
            xx, yy = x_origin + run["x"], baseline + sh
            if fb:
                tx = c.beginText()
                tx.setTextOrigin(xx, yy)
                tx.setFont(font, size)
                tx.setTextRenderMode(2)
                c.setStrokeColor(col)
                c.setLineWidth(size * 0.03)
                tx.textOut(text)
                tx.setTextRenderMode(0)
                c.drawText(tx)
            else:
                c.setFont(font, size)
                c.drawString(xx, yy, text)
            if ul or st:
                c.setStrokeColor(col)
                c.setLineWidth(max(0.4, size * 0.05))
                if ul:
                    c.line(xx, yy - size * 0.12, xx + run["w"], yy - size * 0.12)
                if st:
                    c.line(xx, yy + size * 0.3, xx + run["w"], yy + size * 0.3)

    def _hline(self, c, x1, x2, y, b):
        width, color, _space, dash = b
        c.setStrokeColor(_hexcolor(color))
        c.setLineWidth(width)
        c.setDash(*dash) if dash else c.setDash()
        c.line(x1, y, x2, y)
        c.setDash()

    def _measure(self, lay):
        h = sum(l["h"] for l in lay["lines"])
        for side in ("top", "bottom"):
            if side in lay["borders"]:
                h += lay["borders"][side][0] + lay["borders"][side][2]
        return h

    # ---------------- таблицы ----------------

    def _table_geometry(self, tbl):
        grid = [self._tw(g.get(qn("w:w")) or 0) for g in tbl._tbl.tblGrid.findall(qn("w:gridCol"))] or [100.0]
        total = sum(grid)
        pr = tbl._tbl.tblPr
        jc = _val(pr.find(qn("w:jc"))) if pr is not None and pr.find(qn("w:jc")) is not None else None
        ind = pr.find(qn("w:tblInd")) if pr is not None else None
        ind_w = self._tw(ind.get(qn("w:w")) or 0) if ind is not None and ind.get(qn("w:type")) in (None, "dxa") else 0.0
        bidi = pr is not None and pr.find(qn("w:bidiVisual")) is not None and _on(pr.find(qn("w:bidiVisual"))) is True
        if jc == "center":
            x0 = self.m_left + (self.text_w - total) / 2
        elif bidi:
            # Cycle P2: в таблице bidiVisual начало это правый край: left/start = справа, right/end = слева
            if jc in ("right", "end"):
                x0 = self.m_left - ind_w
            else:
                x0 = self.m_left + self.text_w - total - ind_w
        elif jc in ("right", "end"):
            x0 = self.m_left + self.text_w - total
        else:
            x0 = self.m_left + ind_w
        return grid, total, x0

    @staticmethod
    def _tbl_bidi(tbl):
        pr = tbl._tbl.tblPr
        return pr is not None and pr.find(qn("w:bidiVisual")) is not None and _on(pr.find(qn("w:bidiVisual"))) is True

    def _pad(self, tbl_chain):
        left = right = 5.4
        top = bottom = 1.0  # небольшой зазор, чтобы текст не касался рамки
        for props in tbl_chain:
            if props is not None and props.find(qn("w:tblCellMar")) is not None:
                mar = props.find(qn("w:tblCellMar"))
                for side in ("left", "right", "top", "bottom"):
                    e = mar.find(qn("w:" + side))
                    if e is None and side in ("left", "right"):
                        e = mar.find(qn("w:" + ("start" if side == "left" else "end")))
                    if e is not None and e.get(qn("w:w")):
                        v = self._tw(e.get(qn("w:w")))
                        if side == "left":
                            left = v
                        elif side == "right":
                            right = v
                        elif side == "top":
                            top = v
                        else:
                            bottom = v
                break
        return left, right, max(top, 1.0), max(bottom, 1.0)  # минимум 1pt, чтобы текст не касался рамки

    # ---------------- сборка документа ----------------

    def convert_pdf(self):
        from reportlab.pdfgen import canvas as pdf_canvas
        buf = io.BytesIO()
        c = pdf_canvas.Canvas(buf, pagesize=(self.page_w, self.page_h))
        state = {"y": self.page_h - self.m_top, "top": self.page_h - self.m_top, "drawn": False}
        bottom = self.m_bottom

        def new_page():
            c.showPage()
            state["y"] = state["top"]
            state["drawn"] = False

        def place_para(para):
            lay = self._layout(para, self.text_w)
            if lay["page_break"] and state["drawn"]:
                new_page()
            at_top = state["y"] >= state["top"] - 0.01
            if not at_top:
                state["y"] -= lay["before"]  # у верхнего края страницы интервал «перед» не добавляем
            if "top" in lay["borders"]:
                b = lay["borders"]["top"]
                self._hline(c, self.m_left + lay["left"], self.m_left + self.text_w - lay["right"],
                            state["y"] - b[0] / 2, b)
                state["y"] -= b[0] + b[2]
            for line in lay["lines"]:
                if state["y"] - line["h"] < bottom and state["drawn"]:
                    new_page()
                if lay["fill"]:
                    c.setFillColor(_hexcolor(lay["fill"]))
                    c.rect(self.m_left + lay["left"], state["y"] - line["h"],
                           self.text_w - lay["left"] - lay["right"], line["h"], stroke=0, fill=1)
                self._draw_items(c, line, self.m_left, state["y"] - line["asc"])
                state["y"] -= line["h"]
                state["drawn"] = True
            if "bottom" in lay["borders"]:
                b = lay["borders"]["bottom"]
                state["y"] -= b[2]
                self._hline(c, self.m_left + lay["left"], self.m_left + self.text_w - lay["right"],
                            state["y"] - b[0] / 2, b)
                state["y"] -= b[0]
            state["y"] -= lay["after"]

        def place_table(tbl):
            grid, total, x0 = self._table_geometry(tbl)
            ncols = len(grid)
            bidi_tbl = self._tbl_bidi(tbl)
            tbl_borders, tbl_chain = self._table_borders(tbl)
            try:
                self._tbl_ppr = _style_chain_props(tbl.style, "pPr")
                self._tbl_rpr = _style_chain_props(tbl.style, "rPr")
            except Exception:
                self._tbl_ppr, self._tbl_rpr = [], []
            pad_l, pad_r, pad_t, pad_b = self._pad(tbl_chain)
            rows = tbl._tbl.findall(qn("w:tr"))
            nrows = len(rows)
            try:
                for r, tr in enumerate(rows):
                    cells = []
                    c_idx = 0
                    row_h = 0.0
                    trh = tr.find(qn("w:trPr") + "/" + qn("w:trHeight")) if tr.find(qn("w:trPr")) is not None else None
                    min_h = self._tw(trh.get(qn("w:val"))) if trh is not None and trh.get(qn("w:val")) else 0.0
                    for tc in tr.findall(qn("w:tc")):
                        tcpr = tc.find(qn("w:tcPr"))
                        span = 1
                        if tcpr is not None and tcpr.find(qn("w:gridSpan")) is not None:
                            span = max(1, int(_val(tcpr.find(qn("w:gridSpan"))) or 1))
                        span = min(span, max(1, ncols - c_idx))
                        cx = x0 + (total - sum(grid[:c_idx + span]) if bidi_tbl else sum(grid[:c_idx]))
                        cw = sum(grid[c_idx:c_idx + span])
                        lays = []
                        for ch in tc.iterchildren():
                            if ch.tag == qn("w:p"):
                                lays.append(self._layout(Paragraph(ch, tbl), cw - pad_l - pad_r))
                        content_h = sum(self._measure(l) + l["before"] + l["after"] for l in lays)
                        row_h = max(row_h, content_h + pad_t + pad_b)
                        cells.append((tcpr, cx, cw, lays, content_h, c_idx, span))
                        c_idx += span
                    row_h = max(row_h, min_h)
                    if state["y"] - row_h < bottom and state["drawn"]:
                        new_page()
                    y_top = state["y"]
                    for tcpr, cx, cw, lays, content_h, ci, span in cells:
                        shd = tcpr.find(qn("w:shd")) if tcpr is not None else None
                        fill = _val(shd, "w:fill") if shd is not None else None
                        if fill and re.match(r"^[0-9A-Fa-f]{6}$", fill):
                            c.setFillColor(_hexcolor(fill.upper()))
                            c.rect(cx, y_top - row_h, cw, row_h, stroke=0, fill=1)
                        va = _val(tcpr.find(qn("w:vAlign"))) if tcpr is not None and tcpr.find(qn("w:vAlign")) is not None else None
                        off = 0.0
                        if va == "center":
                            off = (row_h - pad_t - pad_b - content_h) / 2
                        elif va == "bottom":
                            off = row_h - pad_t - pad_b - content_h
                        y = y_top - pad_t - max(0.0, off)
                        for lay in lays:
                            y -= lay["before"]
                            for line in lay["lines"]:
                                self._draw_items(c, line, cx + pad_l, y - line["asc"])
                                y -= line["h"]
                            y -= lay["after"]
                        borders = tcpr.find(qn("w:tcBorders")) if tcpr is not None else None
                        for side, inside in (("top", "insideH"), ("left", "insideV"),
                                             ("bottom", "insideH"), ("right", "insideV")):
                            if borders is not None and borders.find(qn("w:" + side)) is not None:
                                el = borders.find(qn("w:" + side))
                            else:
                                at_edge = {"top": r == 0, "bottom": r == nrows - 1, "left": ci == 0,
                                           "right": ci + span >= ncols}[side]
                                el = tbl_borders.get(side if at_edge else inside)
                                if el is None:
                                    el = tbl_borders.get(inside)
                            b = self._brd(el)
                            if not b:
                                continue
                            if side == "top":
                                self._hline(c, cx, cx + cw, y_top, b)
                            elif side == "bottom":
                                self._hline(c, cx, cx + cw, y_top - row_h, b)
                            elif side == "left":
                                self._vline(c, cx, y_top, y_top - row_h, b)
                            else:
                                self._vline(c, cx + cw, y_top, y_top - row_h, b)
                    state["y"] = y_top - row_h
                    state["drawn"] = True
            finally:
                self._tbl_ppr, self._tbl_rpr = [], []

        for item in self.doc.iter_inner_content():
            if isinstance(item, Table):
                try:
                    place_table(item)
                except Exception:
                    continue
            elif isinstance(item, Paragraph):
                place_para(item)
        c.showPage()
        c.save()
        return buf.getvalue()

    def _vline(self, c, x, y1, y2, b):
        width, color, _space, dash = b
        c.setStrokeColor(_hexcolor(color))
        c.setLineWidth(width)
        c.setDash(*dash) if dash else c.setDash()
        c.line(x, y1, x, y2)
        c.setDash()


def docx_bytes_to_pdf(docx_bytes):
    """DOCX (bytes) -> PDF (bytes) с сохранением оформления."""
    return _PdfConverter(docx_bytes).convert_pdf()
