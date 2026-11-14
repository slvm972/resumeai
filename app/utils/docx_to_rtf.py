"""
Cycle X3: конвертер DOCX -> RTF с сохранением оформления (парный к docx_to_odt из X1).

Переносит: страницу и поля; у абзаца выравнивание, отступы, интервалы, междустрочный,
границы (линия под заголовком), заливку, табы (в т.ч. правые для дат), RTL;
у фрагментов шрифт, размер, жирный, курсив, подчёркивание, зачёркивание, цвет, капитель,
верхний/нижний индекс; списки (маркер или номер печатается в начале абзаца);
таблицы (\\trowd/\\cellx), рамки ячеек, объединение по горизонтали, вертикальное выравнивание.

Кодировка как в _generate_rtf: заголовок \\ansicpg1252\\uc1\\deff0, весь не-ASCII текст идёт
через \\uN? (значения выше 32767 отрицательные, символы выше U+FFFF как пара суррогатов).

Не переносится: картинки и фигуры, вложенные таблицы, колонтитулы, сноски, гиперссылки
(остаётся только текст), вертикальное объединение ячеек, цвета темы, условное форматирование
табличных стилей.

Главная функция: docx_bytes_to_rtf(docx_bytes) -> bytes. Исключения не глушит.
Внутри переиспользует разбор стилей из app.utils.docx_to_odt (_Converter): менять его
методы разбора нужно с оглядкой на этот модуль.
"""
import re

from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from app.utils.docx_to_odt import (_Converter, _RTL_RE, _val, _on, _guess_generic, _NUMFMT,
                                   _style_chain_props)

_RTL_CHARS = _RTL_RE
_BULLET_TEXT = {"\uf0b7": "\u2022", "\uf0a7": "\u25aa", "\uf0d8": "\u27a4", "\uf076": "\u2756",
                "o": "\u25e6", "\u00b7": "\u2022", "\uf0fc": "\u2713"}


def _esc(s):
    """Экранировать текст: спецсимволы RTF, табы/переносы, не-ASCII через \\uN?."""
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == "{":
            out.append("\\{")
        elif ch == "}":
            out.append("\\}")
        elif ch == "\t":
            out.append("\\tab ")
        elif ch == "\n":
            out.append("\\line ")
        elif ch == "\r":
            continue
        elif o > 127:
            if o > 0xFFFF:
                o -= 0x10000
                units = (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF))
            else:
                units = (o,)
            for u in units:
                out.append("\\u%d?" % (u - 65536 if u > 32767 else u))
        elif o < 32:
            continue
        else:
            out.append(ch)
    return "".join(out)


def _roman(n, upper):
    vals = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
    r = ""
    for v, s in vals:
        while n >= v:
            r += s
            n -= v
    return r.upper() if upper else r


def _number_label(fmt, n):
    if fmt == "lowerLetter":
        return chr(ord("a") + (n - 1) % 26)
    if fmt == "upperLetter":
        return chr(ord("A") + (n - 1) % 26)
    if fmt == "lowerRoman":
        return _roman(n, False)
    if fmt == "upperRoman":
        return _roman(n, True)
    return str(n)


def _split_scripts(s):
    """Разбить текст на сегменты (is_rtl, text): нейтральные символы остаются в текущем сегменте."""
    segs = []
    cur_rtl = None
    buf = []
    first_strong = None
    for ch in s:
        if _RTL_CHARS.match(ch):
            first_strong = True
            break
        if ch.isalpha():
            first_strong = False
            break
    cur_rtl = bool(first_strong)
    for ch in s:
        if _RTL_CHARS.match(ch):
            kind = True
        elif ch.isalpha():
            kind = False
        else:
            kind = cur_rtl
        if kind != cur_rtl and buf:
            segs.append((cur_rtl, "".join(buf)))
            buf = []
        cur_rtl = kind
        buf.append(ch)
    if buf:
        segs.append((cur_rtl, "".join(buf)))
    return segs


class _RtfConverter(_Converter):
    def __init__(self, docx_bytes):
        super().__init__(docx_bytes)
        self._rfonts = {}      # имя -> индекс
        self._rcolors = {}     # hex -> индекс (с 1)
        self._counters = {}    # (numId, ilvl) -> текущий номер

    # ---------------- реестры ----------------

    def _font_idx(self, name):
        if name not in self._rfonts:
            self._rfonts[name] = len(self._rfonts)
        return self._rfonts[name]

    def _color_idx(self, hexv):
        if hexv not in self._rcolors:
            self._rcolors[hexv] = len(self._rcolors) + 1
        return self._rcolors[hexv]

    def _color_ctl(self, ctl, hexv):
        return "\\%s%d" % (ctl, self._color_idx(hexv.upper())) if hexv else ""

    # ---------------- границы ----------------

    def _brd(self, el):
        """(тип, толщина в twips, hex, отступ в twips) или None."""
        if el is None:
            return None
        v = (el.get(qn("w:val")) or "").lower()
        if v in ("nil", "none", ""):
            return None
        sz = el.get(qn("w:sz"))
        width = min(75, max(5, int(int(sz) * 2.5))) if sz else 10
        color = el.get(qn("w:color")) or "000000"
        if color.lower() == "auto" or not re.match(r"^[0-9A-Fa-f]{6}$", color):
            color = "000000"
        kind = {"double": "brdrdb", "dotted": "brdrdot", "dashed": "brdrdash"}.get(v, "brdrs")
        space = el.get(qn("w:space"))
        return kind, width, color.upper(), int(float(space) * 20) if space else 0

    def _brd_ctl(self, side_ctl, b):
        kind, width, color, space = b
        s = "\\%s\\%s\\brdrw%d\\brdrcf%d" % (side_ctl, kind, width, self._color_idx(color))
        if space and side_ctl.startswith("brdr"):
            s += "\\brsp%d" % space
        return s

    # ---------------- фрагменты ----------------

    def _run_rtf(self, props, text):
        (font, cs_font, size, size_cs, bold, bold_cs, italic, italic_cs,
         underline, color, strike, caps, small, va) = props
        out = []
        for is_rtl, seg in _split_scripts(text):
            ctl = []
            if is_rtl:
                ctl.append("\\rtlch\\fcs1\\af%d\\afs%d" % (self._font_idx(cs_font), int(size_cs * 2)))
                ctl.append("\\ab" if bold_cs else "\\ab0")
                ctl.append("\\ai" if italic_cs else "\\ai0")
            else:
                ctl.append("\\ltrch\\fcs0\\f%d\\fs%d" % (self._font_idx(font), int(size * 2)))
                ctl.append("\\b" if bold else "\\b0")
                ctl.append("\\i" if italic else "\\i0")
            ctl.append("\\ul" if underline else "\\ulnone")
            if color:
                ctl.append(self._color_ctl("cf", color))
            if strike:
                ctl.append("\\strike")
            if caps:
                ctl.append("\\caps")
            elif small:
                ctl.append("\\scaps")
            if va == "superscript":
                ctl.append("\\super")
            elif va == "subscript":
                ctl.append("\\sub")
            out.append("{" + "".join(ctl) + " " + _esc(seg) + "}")
        return "".join(out)

    def _run_text(self, run, para_style):
        txt = run.text
        if not txt:
            return ""
        rpr = run._r.rPr
        rstyle = run.style if rpr is not None and rpr.find(qn("w:rStyle")) is not None else None
        props = self._run_props(rstyle, para_style, rpr)
        return self._run_rtf(props, txt)

    # ---------------- абзацы ----------------

    def _para_ctl(self, ctx, is_rtl, in_list, list_ind):
        c = []
        if is_rtl:
            c.append("\\rtlpar")
            amap = {"left": "qr", "start": "qr", "right": "ql", "end": "ql", "center": "qc",
                    "both": "qj", "distribute": "qj"}
            c.append("\\" + amap.get(ctx["jc"], "qr"))
        else:
            c.append("\\ltrpar")
            amap = {"left": "ql", "start": "ql", "right": "qr", "end": "qr", "center": "qc",
                    "both": "qj", "distribute": "qj"}
            c.append("\\" + amap.get(ctx["jc"], "ql"))
        # отступы: для RTL-абзаца \\lin/\\rin считаются от начала/конца строки (как w:left/w:right в Word)
        li, ri = ("lin", "rin") if is_rtl else ("li", "ri")
        if in_list:
            left, hang = list_ind
            c.append("\\%s%d\\fi%d" % (li, left, -hang))
        else:
            if ctx["left"] is not None:
                c.append("\\%s%d" % (li, ctx["left"]))
            if ctx["right"] is not None:
                c.append("\\%s%d" % (ri, ctx["right"]))
            if ctx["hanging"]:
                c.append("\\fi%d" % -ctx["hanging"])
            elif ctx["first"]:
                c.append("\\fi%d" % ctx["first"])
        if ctx["before"] is not None:
            c.append("\\sb%d" % int(ctx["before"]))
        if ctx["after"] is not None:
            c.append("\\sa%d" % int(ctx["after"]))
        if ctx["line"] is not None:
            line = int(ctx["line"])
            if ctx["rule"] == "auto":
                c.append("\\sl%d\\slmult1" % line)
            elif ctx["rule"] == "exact":
                c.append("\\sl%d\\slmult0" % -line)
            else:
                c.append("\\sl%d\\slmult0" % line)
        if ctx["page_break"]:
            c.append("\\pagebb")
        for side, key in (("top", "brdrt"), ("left", "brdrl"), ("bottom", "brdrb"), ("right", "brdrr")):
            raw = ctx["borders_raw"].get(side)
            b = self._brd(raw)
            if b:
                c.append(self._brd_ctl(key, b))
        if ctx["fill"]:
            c.append("\\cbpat%d" % self._color_idx(ctx["fill"]))
        tmap = {"left": "", "right": "\\tqr", "center": "\\tqc", "decimal": "\\tqdec", "start": "", "end": "\\tqr"}
        lmap = {"dot": "\\tldot", "hyphen": "\\tlhyph", "underscore": "\\tlul"}
        for kind, pos, leader in ctx["tabs"]:
            if kind in ("clear", "bar") or pos is None:
                continue
            c.append("%s%s\\tx%d" % (lmap.get(leader, ""), tmap.get(kind or "left", ""), int(pos)))
        return "".join(c)

    def _label(self, num_id, ilvl, info):
        """Текст метки списка: маркер или номер."""
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

    def _paragraph(self, para, end="\\par", in_table=False):
        ctx = self._para_ctx(para)
        # сырые границы для RTF (в ctx они уже превращены в строки ODT)
        raw = {}
        chain = [para._p.pPr]
        chain += _style_chain_props(para.style, "pPr") + self._tbl_ppr + [self._defaults_ppr]
        for side in ("top", "bottom", "left", "right"):
            for props in chain:
                if props is None:
                    continue
                bd = props.find(qn("w:pBdr"))
                if bd is not None and bd.find(qn("w:" + side)) is not None:
                    raw[side] = bd.find(qn("w:" + side))
                    break
        ctx["borders_raw"] = raw
        is_rtl = self._is_rtl(para, ctx)
        num_id, ilvl = ctx["num_id"], ctx["ilvl"]
        info = self._numbering_info(num_id, ilvl) if num_id else None
        in_list = info is not None and info[0] != "none"
        list_ind = None
        if in_list:
            left = ctx["left"] if ctx["ind_direct"] and ctx["left"] is not None else info[2]
            hang = ctx["hanging"] if ctx["ind_direct"] and ctx["hanging"] is not None else info[3]
            list_ind = (left, hang)
        base = self._run_props(None, para.style, None)
        head = "\\pard" + ("\\intbl" if in_table else "") + self._para_ctl(ctx, is_rtl, in_list, list_ind)
        if in_list:
            head += "\\tx%d" % list_ind[0]
        head += "\\f%d\\fs%d" % (self._font_idx(base[0]), int(base[2] * 2))
        body = []
        if in_list:
            lab = self._label(num_id, ilvl, info)
            body.append(self._run_rtf(base, lab) + "\\tab ")
        for item in para.iter_inner_content():
            if isinstance(item, Hyperlink):
                for r in item.runs:
                    body.append(self._run_text(r, para.style))
            elif isinstance(item, Run):
                body.append(self._run_text(item, para.style))
        return head + " " + "".join(body) + end

    # ---------------- таблицы ----------------

    def _table(self, tbl):
        grid = [int(g.get(qn("w:w")) or 0) for g in tbl._tbl.tblGrid.findall(qn("w:gridCol"))] or [2000]
        ncols = len(grid)
        rows = tbl._tbl.findall(qn("w:tr"))
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
        gap = 108
        mar = None
        for props in tbl_chain:
            if props is not None and props.find(qn("w:tblCellMar")) is not None:
                mar = props.find(qn("w:tblCellMar"))
                break
        if mar is not None:
            e = mar.find(qn("w:left")) if mar.find(qn("w:left")) is not None else mar.find(qn("w:start"))
            if e is not None and e.get(qn("w:w")):
                gap = int(e.get(qn("w:w")))

        out = []
        nrows = len(rows)
        for r, tr in enumerate(rows):
            row = ["\\trowd\\trgaph%d" % gap]
            if bidi_tbl:
                row.append("\\rtlrow")
            if jc == "center":
                row.append("\\trqc")
            elif jc in ("right", "end"):
                row.append("\\trqr")
            else:
                row.append("\\trleft%d" % ind_w)
            cells = []
            c = 0
            edge = 0
            for tc in tr.findall(qn("w:tc")):
                tcpr = tc.find(qn("w:tcPr"))
                span = 1
                if tcpr is not None and tcpr.find(qn("w:gridSpan")) is not None:
                    span = max(1, int(_val(tcpr.find(qn("w:gridSpan"))) or 1))
                span = min(span, max(1, ncols - c))
                borders = tcpr.find(qn("w:tcBorders")) if tcpr is not None else None
                cdef = []
                for side, key, inside in (("top", "clbrdrt", "insideH"), ("left", "clbrdrl", "insideV"),
                                          ("bottom", "clbrdrb", "insideH"), ("right", "clbrdrr", "insideV")):
                    if borders is not None and borders.find(qn("w:" + side)) is not None:
                        el = borders.find(qn("w:" + side))
                    else:
                        at_edge = {"top": r == 0, "bottom": r == nrows - 1, "left": c == 0,
                                   "right": c + span >= ncols}[side]
                        el = tbl_borders.get(side if at_edge else inside)
                        if el is None:
                            el = tbl_borders.get(inside)
                    b = self._brd(el)
                    if b:
                        cdef.append(self._brd_ctl(key, b))
                va = _val(tcpr.find(qn("w:vAlign"))) if tcpr is not None and tcpr.find(qn("w:vAlign")) is not None else None
                if va == "center":
                    cdef.append("\\clvertalc")
                elif va == "bottom":
                    cdef.append("\\clvertalb")
                shd = tcpr.find(qn("w:shd")) if tcpr is not None else None
                fill = _val(shd, "w:fill") if shd is not None else None
                if fill and re.match(r"^[0-9A-Fa-f]{6}$", fill):
                    cdef.append("\\clcbpat%d" % self._color_idx(fill.upper()))
                edge += sum(grid[c:c + span])
                cdef.append("\\cellx%d" % (ind_w + edge))
                row.append("".join(cdef))
                paras = [Paragraph(ch, tbl) for ch in tc.iterchildren() if ch.tag == qn("w:p")]
                if not paras:
                    cells.append("\\pard\\intbl\\cell")
                else:
                    parts = []
                    for i, p in enumerate(paras):
                        last = i == len(paras) - 1
                        parts.append(self._paragraph(p, end="\\cell" if last else "\\par", in_table=True))
                    cells.append("\n".join(parts))
                c += span
            out.append("".join(row) + "\n" + "\n".join(cells) + "\n\\row")
        self._tbl_ppr, self._tbl_rpr = [], []
        return "\n".join(out)

    # ---------------- сборка ----------------

    def convert_rtf(self):
        body = []
        prev_table = False
        for item in self.doc.iter_inner_content():
            if isinstance(item, Table):
                if prev_table:
                    body.append("\\pard\\par")  # разделитель, иначе соседние таблицы сольются
                try:
                    body.append(self._table(item))
                    prev_table = True
                except Exception:
                    self._tbl_ppr, self._tbl_rpr = [], []
                    continue
            elif isinstance(item, Paragraph):
                body.append(self._paragraph(item))
                prev_table = False

        sec = self.doc.sections[0]
        page = ""
        try:
            page = "\\paperw%d\\paperh%d\\margl%d\\margr%d\\margt%d\\margb%d" % (
                sec.page_width // 635, sec.page_height // 635, sec.left_margin // 635,
                sec.right_margin // 635, sec.top_margin // 635, sec.bottom_margin // 635)
            if sec.page_width > sec.page_height:
                page += "\\landscape"
        except Exception:
            page = ""
        if not self._rfonts:
            self._font_idx("Calibri")
        fonts = []
        for name, idx in sorted(self._rfonts.items(), key=lambda kv: kv[1]):
            fam = {"roman": "froman", "modern": "fmodern"}.get(_guess_generic(name), "fswiss")
            fonts.append("{\\f%d\\%s\\fcharset0 %s;}" % (idx, fam, _esc(name)))
        colors = "".join("\\red%d\\green%d\\blue%d;" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                         for h, _ in sorted(self._rcolors.items(), key=lambda kv: kv[1]))
        rtf = ("{\\rtf1\\ansi\\ansicpg1252\\uc1\\deff0" + page +
               "{\\fonttbl" + "".join(fonts) + "}" +
               "{\\colortbl;" + colors + "}\n" +
               "\n".join(body) + "\n}")
        return rtf.encode("ascii")


def docx_bytes_to_rtf(docx_bytes):
    """DOCX (bytes) -> RTF (bytes, ASCII) с сохранением оформления."""
    return _RtfConverter(docx_bytes).convert_rtf()
