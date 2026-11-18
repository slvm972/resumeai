#!/usr/bin/env python3
"""
Cycle M1: матрица проверки сохранения оформления «входной формат x выходной формат x режим».

Запуск:  python tools/format_matrix.py            -> таблицы в консоль и в matrix_report.md
         python tools/format_matrix.py --rebuild-corpus   пересоздать tests/data/matrix/ (нужен soffice)
         python tools/format_matrix.py --keep DIR         сохранить все выходные файлы в DIR

Что делает: для каждого из 5 исходных DOCX (tests/data/matrix) и каждого входного формата (DOCX, ODT, RTF,
PDF, TXT) в двух режимах (Precise, Creative) вызывает РЕАЛЬНЫЕ маршруты экспорта /api/improve/{docx,odt,rtf,pdf}
через Flask test client так, как это делает index.html (original_file/item_ids по ТЕКУЩИМ условиям страницы),
без сети и без LLM (Precise: /api/improve с подменённой моделью, которая слегка правит слова; Creative: готовый
текст из tests/data/creative_c1/creative_texts.json или синтетический по тем же правилам). Выходные файлы
DOCX/ODT/RTF рендерятся через soffice в PDF, из PDF читаются метрики M1..M10 (pdfminer); таблицы читаются
из самого файла (DOCX: python-docx, ODT: XML, RTF: \trowd). Эталон: профиль оформления исходного DOCX
(python-docx) и его отрисовка (corpus <name>.pdf, сделан soffice). Продукт (app/, index.html) не меняется.
"""
import argparse
import collections
import difflib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data", "matrix")
sys.path.insert(0, ROOT)
os.environ.setdefault("FLASK_ENV", "testing")

DOCS = ["designer", "ua", "engineer", "tabdates", "hebrew_rtl"]
INPUTS = ["docx", "odt", "rtf", "pdf", "txt"]
OUTPUTS = ["docx", "odt", "rtf", "pdf"]
MODES = ["precise", "creative"]
METRICS = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"]
METRIC_NAMES = {
    "M1": "текст не потерян", "M2": "имя по центру", "M3": "размер имени", "M4": "цвет и жирность заголовков",
    "M5": "линия под заголовком", "M6": "маркеры", "M7": "даты справа", "M8": "таблицы", "M9": "шрифтовой класс",
    "M10": "число страниц",
}

# ---------------------------------------------------------------------------
# Корпус
# ---------------------------------------------------------------------------

import io as _io
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def _border(p, color):
    pPr = p._p.get_or_add_pPr(); b = OxmlElement('w:pBdr'); e = OxmlElement('w:bottom')
    for k, v in (('val','single'),('sz','8'),('space','1'),('color',color)): e.set(qn('w:'+k), v)
    b.append(e); pPr.append(b)

def _font(run, name, size=None, bold=None, color=None, italic=None):
    run.font.name = name
    rpr = run._r.get_or_add_rPr(); rf = rpr.find(qn('w:rFonts'))
    for a in ('ascii','hAnsi','cs'): rf.set(qn('w:'+a), name)
    if size:
        run.font.size = Pt(size)
        sz = OxmlElement('w:szCs'); sz.set(qn('w:val'), str(int(size * 2))); rpr.append(sz)   # размер для иврита
    if bold is not None:
        run.bold = bold
        if bold: rpr.append(OxmlElement('w:bCs'))                                            # жирный для иврита
    if italic is not None: run.italic = italic
    if color: run.font.color.rgb = RGBColor.from_string(color)

def make_tabdates():
    """(а) serif, имя слева, цветные заголовки с линией, даты через правый таб, маркеры списка."""
    F = 'Georgia'
    d = Document(); s = d.sections[0]; s.left_margin = s.right_margin = Cm(2.2)
    p = d.add_paragraph(); _font(p.add_run('Daniel Foster'), F, 24, True, '1B5E20')
    p = d.add_paragraph(); _font(p.add_run('Operations Manager'), F, 12, False, '555555', True)
    p = d.add_paragraph(); _font(p.add_run('daniel.foster@example.com | +1 312 555 0147 | Chicago, IL'), F, 10)
    def heading(t):
        p = d.add_paragraph(); p.paragraph_format.space_before = Pt(10); _font(p.add_run(t), F, 13, True, '1B5E20'); _border(p, '1B5E20')
    def job(t, dt):
        p = d.add_paragraph(); p.paragraph_format.tab_stops.add_tab_stop(Cm(16.6), WD_TAB_ALIGNMENT.RIGHT)
        _font(p.add_run(t), F, 11, True); _font(p.add_run('\t' + dt), F, 10, False, '555555')
    def bullet(t):
        p = d.add_paragraph(style='List Bullet'); _font(p.add_run(t), F, 10.5)
    def body(t):
        p = d.add_paragraph(); _font(p.add_run(t), F, 10.5)
    heading('Summary')
    body('Operations manager with 10 years of experience improving warehouse throughput and reducing delivery times across three regional sites.')
    heading('Experience')
    job('Operations Manager, Northwind Logistics, Chicago', '2018 - 2024')
    bullet('Led a team of 24 warehouse staff across two shifts')
    bullet('Reduced average delivery time from 5 days to 3 days')
    bullet('Introduced weekly safety audits that cut incidents by 40%')
    job('Shift Supervisor, Lakeside Distribution, Chicago', '2014 - 2018')
    bullet('Scheduled and coordinated 12 drivers on daily routes')
    bullet('Maintained inventory accuracy above 98%')
    heading('Education')
    job('B.S. in Supply Chain Management, Purdue University', '2010 - 2014')
    heading('Skills')
    body('Inventory management, Route planning, Team leadership, SAP, Excel')
    b = io.BytesIO(); d.save(b); return b.getvalue()

def make_hebrew():
    """(б) иврит RTL, sans, таблица «должность | годы», маркеры, цветные заголовки."""
    F = 'Arial'
    d = Document(); s = d.sections[0]
    def rtl(p, jc=None):
        pPr = p._p.get_or_add_pPr(); pPr.append(OxmlElement('w:bidi'))
        if jc: p.alignment = jc
    def run(p, t, size, bold=None, color=None):
        r = p.add_run(t); _font(r, F, size, bold, color); r._r.get_or_add_rPr().append(OxmlElement('w:rtl')); return r
    p = d.add_paragraph(); rtl(p, WD_ALIGN_PARAGRAPH.CENTER); run(p, 'יוסי כהן', 24, True, '1F4E79')
    p = d.add_paragraph(); rtl(p, WD_ALIGN_PARAGRAPH.CENTER); run(p, 'yossi.cohen@example.com | 050-1234567 | תל אביב', 10)
    def heading(t):
        p = d.add_paragraph(); rtl(p); run(p, t, 14, True, '1F4E79'); _border(p, '1F4E79')
    def job_table(t, dt):
        tb = d.add_table(rows=1, cols=2); tb._tbl.tblPr.append(OxmlElement('w:bidiVisual'))
        c0, c1 = tb.rows[0].cells
        p0 = c0.paragraphs[0]; rtl(p0); run(p0, t, 11, True)
        p1 = c1.paragraphs[0]; rtl(p1, WD_ALIGN_PARAGRAPH.RIGHT); run(p1, dt, 10, False, '666666')
    def bullet(t):
        p = d.add_paragraph(style='List Bullet'); rtl(p); run(p, t, 10.5)
    def body(t):
        p = d.add_paragraph(); rtl(p); run(p, t, 10.5)
    heading('תקציר')
    body('מהנדס תוכנה עם ניסיון של שמונה שנים בפיתוח מערכות ווב ובניהול צוותים קטנים.')
    heading('ניסיון תעסוקתי')
    job_table('מהנדס תוכנה בכיר, חברת טכנולוגיות בע"מ', '2019 - 2024')
    bullet('הובלת צוות של חמישה מפתחים')
    bullet('שיפור זמני התגובה של המערכת ב-40%')
    job_table('מפתח תוכנה, סטארטאפ ישראלי', '2015 - 2019')
    bullet('פיתוח ממשקי API למערכת תשלומים')
    heading('השכלה')
    job_table('תואר ראשון במדעי המחשב, האוניברסיטה העברית', '2011 - 2015')
    heading('כישורים')
    body('Python, JavaScript, SQL, Docker, ניהול פרויקטים')
    b = io.BytesIO(); d.save(b); return b.getvalue()



def rebuild_corpus():
    """Пересоздать корпус: 3 DOCX из creative_c1 + 2 синтетических, из каждого odt/rtf/pdf/txt через soffice."""
    os.makedirs(DATA, exist_ok=True)
    c1 = os.path.join(ROOT, "tests", "data", "creative_c1")
    for src, dst in (("resume_english_designer.docx", "designer.docx"), ("resume_ua.docx", "ua.docx"),
                     ("resume_russian_engineer.docx", "engineer.docx")):
        shutil.copy(os.path.join(c1, src), os.path.join(DATA, dst))
    open(os.path.join(DATA, "tabdates.docx"), "wb").write(make_tabdates())
    open(os.path.join(DATA, "hebrew_rtl.docx"), "wb").write(make_hebrew())
    for doc in DOCS:
        path = os.path.join(DATA, doc + ".docx")
        for fmt in ("odt", "rtf", "pdf", "txt:Text (encoded):UTF8"):
            subprocess.run(["soffice", "--headless", "--convert-to", fmt, "--outdir", DATA, path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        txt = os.path.join(DATA, doc + ".txt")
        raw = open(txt, "rb").read()
        if raw.startswith(b"\xef\xbb\xbf"):      # без BOM, как обычный пользовательский txt
            open(txt, "wb").write(raw[3:])


# ---------------------------------------------------------------------------
# Эталон: профиль оформления исходного DOCX
# ---------------------------------------------------------------------------

_DATE_RANGE = (r"(?:19|20)\d{2}\s*[-\u2013\u2014]\s*(?:(?:19|20)\d{2}|present|current|"
               r"\u0442\u0435\u043f\u0435\u0440\u0456\u0448\u043d\u0456\u0439 \u0447\u0430\u0441|"
               r"\u043d\u0430\u0441\u0442\u043e\u044f\u0449\u0435\u0435 \u0432\u0440\u0435\u043c\u044f)")
_DATE_ONLY = re.compile(r"^\s*" + _DATE_RANGE + r"\s*$", re.I)
_DATE_ANY = re.compile(_DATE_RANGE, re.I)
_HEB = re.compile("[\u0590-\u05ff]")
_SERIF = re.compile(r"(times|serif|georgia|garamond|cambria|caladea|palatino|tinos|bookman|roman)", re.I)


def _para_props(p):
    ppr = p._p.pPr
    return ppr


def docx_facts(raw):
    """Структурные факты об оформлении исходного DOCX (python-docx)."""
    from docx.table import Table
    d = Document(io.BytesIO(raw))
    facts = {"name": None, "headings": [], "dates": [], "bullets": [], "tables": 0, "table_rows": 0,
             "rtl": False, "table_borders": False, "name_centered": False}
    first = True
    all_text = []
    for item in d.iter_inner_content():
        if isinstance(item, Table):
            facts["tables"] += 1
            facts["table_rows"] += len(item.rows)
            tb = item._tbl.tblPr
            if (item.style is not None and "grid" in (item.style.name or "").lower()) or \
                    (tb is not None and tb.find(qn("w:tblBorders")) is not None):
                facts["table_borders"] = True
            for row in item.rows:
                for cell in row.cells:
                    t = cell.text.strip()
                    all_text.append(t)
                    if _DATE_ONLY.match(t):
                        facts["dates"].append(t)
            continue
        t = item.text.strip()
        all_text.append(t)
        if not t:
            continue
        ppr = item._p.pPr
        sid = ""
        if ppr is not None and ppr.find(qn("w:pStyle")) is not None:
            sid = ppr.find(qn("w:pStyle")).get(qn("w:val")) or ""
        if first:
            facts["name"] = t
            jc = ppr.find(qn("w:jc")).get(qn("w:val")) if ppr is not None and ppr.find(qn("w:jc")) is not None else None
            facts["name_centered"] = jc == "center"
            first = False
            continue
        runs = [r for r in item.runs if r.text.strip()]
        all_bold = bool(runs) and all(r.bold for r in runs)
        size = max([r.font.size.pt for r in runs if r.font.size] or [0])
        is_bullet = ((ppr is not None and ppr.find(qn("w:numPr")) is not None) or t[:1] in "\u2022-\u2013*"
                     or ((item.style is not None) and (item.style.name or "").lower().startswith("list")))
        if sid.lower().startswith("heading") or (all_bold and size >= 12.5 and len(t) <= 40):
            facts["headings"].append(t)
        elif is_bullet:
            facts["bullets"].append(t.lstrip("\u2022-\u2013* ").strip())
        elif _DATE_ONLY.match(t):
            facts["dates"].append(t)
        elif "\t" in t and _DATE_ANY.search(t.split("\t")[-1]):
            facts["dates"].append(_DATE_ANY.search(t.split("\t")[-1]).group(0))
        else:
            m = re.search(_DATE_RANGE + r"\s*$", t, re.I)
            if m and len(t) > len(m.group(0)):
                facts["dates"].append(m.group(0))
    facts["rtl"] = bool(_HEB.search(" ".join(all_text)))
    return facts


# ---------------------------------------------------------------------------
# Чтение PDF: символы, строки, линии
# ---------------------------------------------------------------------------

BULLET_CHARS = set("\u2022\u25cf\u25aa\u25e6\u00b7\u2023\u2219\u25cb\u25a0\u25a1\u25c6\u25ba\u27a4\u2713")


def _is_bullet_char(ch):
    return ch in BULLET_CHARS or (len(ch) == 1 and 0xE000 <= ord(ch) <= 0xF8FF)


def _norm_color(cs_name, col):
    try:
        if isinstance(col, (int, float)):
            col = (col,)
        col = tuple(col)
        if len(col) == 3:
            return tuple(int(round(float(c) * 255)) for c in col)
        if len(col) == 1:
            g = int(round(float(col[0]) * 255))
            return (g, g, g)
        if len(col) == 4:
            c, m, y, k = [float(v) for v in col]
            return tuple(int(round(255 * (1 - min(1, x + k)))) for x in (c, m, y))
    except Exception:
        pass
    return (0, 0, 0)


class Pdf:
    """Разобранный PDF: pages, W, chars, lines (визуальные строки), shapes (линии и прямоугольники)."""

    def __init__(self, path):
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTChar, LTLine, LTRect, LTCurve
        self.pages = 0
        self.W = 595.0
        chars, shapes = [], []

        def walk(el, pno):
            if isinstance(el, LTChar):
                gs = el.graphicstate
                chars.append({"ch": el.get_text(), "x0": el.x0, "x1": el.x1, "base": el.matrix[5], "size": el.size,
                              "font": el.fontname, "color": _norm_color(getattr(gs, "ncs", None) and gs.ncs.name,
                                                                       getattr(gs, "ncolor", None)), "page": pno})
            elif isinstance(el, (LTLine, LTRect, LTCurve)):
                shapes.append({"x0": el.x0, "x1": el.x1, "y0": el.y0, "y1": el.y1, "page": pno})
            elif hasattr(el, "__iter__"):
                for c in el:
                    walk(c, pno)
        for pno, layout in enumerate(extract_pages(path, laparams=None)):
            self.pages += 1
            self.W = layout.width
            walk(layout, pno)
        self.chars, self.shapes = chars, shapes
        self.lines = self._build_lines(chars)
        counter = collections.Counter()
        for c in chars:
            if c["ch"].strip():
                counter[round(c["size"], 1)] += 1
        self.body_size = counter.most_common(1)[0][0] if counter else 11.0
        fonts = collections.Counter()
        for c in chars:
            if c["ch"].strip():
                fonts[re.sub(r"^[A-Z]{6}\+", "", c["font"])] += 1
        self.body_font = fonts.most_common(1)[0][0] if fonts else ""
        self.text = "\n".join(l["text"] for l in self.lines)

    @staticmethod
    def _build_lines(chars):
        groups = collections.defaultdict(list)
        for c in chars:
            groups[(c["page"], round(c["base"] / 2.5))].append(c)
        lines = []
        for (page, _k), cs in sorted(groups.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
            cs.sort(key=lambda c: c["x0"])
            text, nmap, prev = [], [], None
            for c in cs:
                if prev is not None and c["ch"].strip() and prev["ch"].strip() and c["x0"] - prev["x1"] > 0.2 * c["size"]:
                    text.append(" ")
                text.append(c["ch"])
                if c["ch"].isalnum():
                    nmap.append((c["ch"].casefold(), c))
                prev = c
            lines.append({"page": page, "chars": cs, "text": "".join(text),
                          "norm": "".join(n for n, _ in nmap), "nmap": nmap,
                          "base": cs[0]["base"]})
        return lines


def _norm(s):
    return "".join(ch.casefold() for ch in s if ch.isalnum())


def _variants(text, limit):
    """Варианты искомой строки. Для текста без RTL-символов только логический порядок (иначе ложные совпадения);
    для RTL: логический, визуальный (bidi), обратный порядок слов (даты в RTL), обратная строка."""
    target = _norm(text)[:limit]
    if not _HEB.search(text) and not re.search("[\u0600-\u06ff]", text):
        return [target] if len(target) >= 2 else []
    out = [target]
    try:
        from bidi.algorithm import get_display
        out.append(_norm(get_display(text))[:limit])
        out.append(_norm(get_display(text))[-limit:])
    except Exception:
        pass
    out.append(target[::-1])
    out.append(_norm(" ".join(reversed(text.split())))[:limit])
    return [v for v in out if len(v) >= 2]


def locate(pdf, text, limit=30, whole_line=False):
    """Найти текст в визуальных строках PDF: {line, chars} (chars - символы найденного фрагмента) или None.
    whole_line=True (имя, заголовки): совпадение принимается, только если строка почти целиком состоит из этого
    текста, иначе короткое слово заголовка находится внутри абзаца («experience» в описании)."""
    if len(_norm(text)) < 2:
        return None
    for line in pdf.lines:
        for v in _variants(text, limit):
            pos = line["norm"].find(v)
            if pos >= 0:
                if whole_line and len(line["norm"]) > len(v) * 1.3 + 6:
                    continue
                return {"line": line, "chars": [c for _, c in line["nmap"][pos:pos + len(v)]]}
    return None


def hrule_near(pdf, loc):
    base = loc["line"]["base"]
    size = max(c["size"] for c in loc["chars"])
    page = loc["line"]["page"]
    for s in pdf.shapes:
        if s["page"] != page:
            continue
        w, h = s["x1"] - s["x0"], s["y1"] - s["y0"]
        if w >= 0.25 * pdf.W and h <= 2.5 and (base - 1.0 * size - 9) <= s["y0"] <= (base + 1.5 * size + 9):
            return True
    return False


def is_bold(font):
    return bool(re.search(r"(bold|black|heavy|semibold|demi)", font, re.I))


def font_class(font):
    return "serif" if _SERIF.search(font) and not re.search(r"sans", font, re.I) else "sans"


# ---------------------------------------------------------------------------
# Эталонный профиль (факты python-docx + отрисовка эталона)
# ---------------------------------------------------------------------------

def reference_profile(doc):
    raw = open(os.path.join(DATA, doc + ".docx"), "rb").read()
    f = docx_facts(raw)
    pdf = Pdf(os.path.join(DATA, doc + ".pdf"))
    R = {"facts": f, "pages": pdf.pages, "body_size": pdf.body_size, "font_class": font_class(pdf.body_font),
         "rtl": f["rtl"]}
    loc = locate(pdf, f["name"] or "", whole_line=True)
    R["name_size_ratio"] = (max(c["size"] for c in loc["chars"]) / pdf.body_size) if loc else None
    R["name_centered"] = bool(f["name_centered"])
    cols, bolds, rules = [], [], []
    for h in f["headings"]:
        l = locate(pdf, h, whole_line=True)
        if l:
            cols.append(l["chars"][0]["color"])
            bolds.append(is_bold(l["chars"][0]["font"]))
            rules.append(hrule_near(pdf, l))
    R["heading_color"] = collections.Counter(cols).most_common(1)[0][0] if cols else None
    R["heading_bold"] = bool(bolds) and sum(bolds) >= len(bolds) / 2
    R["rule_frac"] = (sum(rules) / len(rules)) if rules else 0.0
    xr, xl = [], []
    for d in f["dates"]:
        l = locate(pdf, d, 40)
        if l:
            xr.append(max(c["x1"] for c in l["chars"]) / pdf.W)
            xl.append(min(c["x0"] for c in l["chars"]) / pdf.W)
    R["date_right"] = (sum(xr) / len(xr)) if xr else None
    # для RTL «конец строки» это левый край: даты прижаты к левому краю страницы
    R["date_left"] = (sum(xl) / len(xl)) if (xl and f["rtl"]) else None
    R["shapes"] = len(pdf.shapes)
    return R


# ---------------------------------------------------------------------------
# Ожидаемые элементы (из текста, поданного на экспорт)
# ---------------------------------------------------------------------------

def expected_from(mode, R, text_for_export, display_text):
    """Ожидаемые элементы и текст для M1. Precise: структура из эталона (заголовки заморожены, правятся слова);
    Creative: из блоков, которые разобрал parse_creative_text (роли заданы самим текстом Creative)."""
    f = R["facts"]
    if mode == "precise":
        lines = [l for l in display_text.split("\n") if l.strip()]
        return {"name": lines[0] if lines else f["name"], "headings": f["headings"], "dates": f["dates"],
                "bullets": f["bullets"], "text": display_text}
    from app.utils.creative_docx_builder import parse_creative_text
    blocks = parse_creative_text(text_for_export)
    dates = [b["text"] for b in blocks if b["role"] == "DATE"] + [b["date"] for b in blocks if b.get("date")]
    plain = "\n".join(re.sub(r"^\s*[-\u2022*\u00b7]\s+", "", l) for l in text_for_export.split("\n"))
    return {"name": next((b["text"] for b in blocks if b["role"] == "NAME"), f["name"]),
            "headings": [b["text"] for b in blocks if b["role"] == "HEADING"],
            "dates": dates, "bullets": [b["text"] for b in blocks if b["role"] == "BULLET"], "text": plain}


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------

def _tokens(s):
    return re.findall(r"\w+", s.casefold())


def _native_table_rows(out_fmt, raw):
    try:
        if out_fmt == "docx":
            d = Document(io.BytesIO(raw))
            return sum(len(t.rows) for t in d.tables)
        if out_fmt == "odt":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                return z.read("content.xml").decode("utf-8", "ignore").count("<table:table-row")
        if out_fmt == "rtf":
            return raw.count(b"\\trowd")
    except Exception:
        return None
    return None


def measure(pdf, R, E, out_fmt, out_raw):
    """Вернуть {M: (True/False/None, подробность)}. None = неприменимо или не измеримо надёжно."""
    res = {}
    f = R["facts"]
    rtl = R["rtl"]
    # M1 текст не потерян (слова в порядке; для RTL порядок не проверяется: в PDF иврит в визуальном порядке)
    exp, got = _tokens(E["text"]), _tokens(pdf.text)
    if "item_" in pdf.text.casefold():
        res["M1"] = (False, "в файле остались служебные метки ###ITEM_")
    elif not exp:
        res["M1"] = (None, "нет ожидаемого текста")
    else:
        cnt = collections.Counter(got + ([g[::-1] for g in got] if rtl else []))
        need = collections.Counter(exp)
        cover = sum(min(n, cnt[t]) for t, n in need.items()) / len(exp)       # все ли слова есть
        if rtl:
            ratio, order = cover, 1.0
            det = "слов найдено %.0f%% (порядок для RTL не проверяется)" % (100 * cover)
        else:
            sm = difflib.SequenceMatcher(None, exp, got, autojunk=False)
            order = sum(b.size for b in sm.get_matching_blocks()) / len(exp)  # в том же ли порядке
            ratio = cover
            det = "слов найдено %.0f%%, в порядке %.0f%%" % (100 * cover, 100 * order)
        # порядок смягчён до 90%: текст Precise из DOCX идёт «абзацы, потом таблицы», а в документе таблицы стоят на своих местах
        res["M1"] = (ratio >= 0.97 and order >= 0.90, det)
    # M2 имя по центру
    nl = locate(pdf, E["name"] or "", whole_line=True)
    if not R["name_centered"]:
        res["M2"] = (None, "в эталоне имя не по центру")
    elif not nl:
        res["M2"] = (False, "имя не найдено в файле")
    else:
        c = (min(x["x0"] for x in nl["chars"]) + max(x["x1"] for x in nl["chars"])) / 2
        off = abs(c - pdf.W / 2) / pdf.W
        res["M2"] = (off <= 0.04, "центр имени отклонён на %.0f%% ширины страницы" % (100 * off))
    # M3 размер имени относительно основного текста
    if R["name_size_ratio"] is None:
        res["M3"] = (None, "имя не найдено в эталоне")
    elif not nl:
        res["M3"] = (False, "имя не найдено в файле")
    else:
        ratio = max(x["size"] for x in nl["chars"]) / pdf.body_size
        res["M3"] = (ratio >= 0.8 * R["name_size_ratio"], "кегль имени/основной %.2f, эталон %.2f" % (ratio, R["name_size_ratio"]))
    # M4 цвет и жирность заголовков
    locs = [l for l in (locate(pdf, h, whole_line=True) for h in E["headings"]) if l]
    if R["heading_color"] is None:
        res["M4"] = (None, "заголовки не найдены в эталоне")
    elif not locs:
        res["M4"] = (False, "заголовки не найдены в файле")
    else:
        ok = 0
        bad = []
        for l in locs:
            col = l["chars"][0]["color"]
            col_ok = all(abs(a - b) <= 24 for a, b in zip(col, R["heading_color"]))
            bold_ok = True
            if R["heading_bold"] and not (out_fmt == "pdf" and rtl):    # faux-bold иврита в PDF не читается
                bold_ok = is_bold(l["chars"][0]["font"])
            if col_ok and bold_ok:
                ok += 1
            else:
                bad.append("цвет %s%s" % (col, "" if bold_ok else ", не жирный"))
        res["M4"] = (ok >= 0.8 * len(locs), "в порядке %d из %d%s" % (ok, len(locs), ("; напр. " + bad[0] + ", эталон %s" % (R["heading_color"],)) if bad else ""))
    # M5 линия под заголовком
    if R["rule_frac"] < 0.5:
        res["M5"] = (None, "в эталоне линий нет")
    elif not locs:
        res["M5"] = (False, "заголовки не найдены в файле")
    else:
        frac = sum(hrule_near(pdf, l) for l in locs) / len(locs)
        res["M5"] = (frac >= 0.7 * R["rule_frac"], "линия у %.0f%% заголовков (эталон %.0f%%)" % (100 * frac, 100 * R["rule_frac"]))
    # M6 маркеры
    bl = [l for l in (locate(pdf, b, 22) for b in E["bullets"]) if l]
    if not f["bullets"]:
        res["M6"] = (None, "в эталоне маркеров нет")
    elif not E["bullets"]:
        res["M6"] = (None, "нет пунктов в тексте")
    elif not bl:
        res["M6"] = (False, "пункты не найдены в файле")
    else:
        have = 0
        for l in bl:
            first = l["chars"][0]
            last = l["chars"][-1]
            near = [c for c in l["line"]["chars"] if _is_bullet_char(c["ch"]) and
                    ((first["x0"] - 80 <= c["x0"] <= first["x0"] + 1) or (last["x1"] - 1 <= c["x1"] <= last["x1"] + 80))]
            have += bool(near)
        res["M6"] = (have >= 0.9 * len(bl), "маркер у %d из %d пунктов" % (have, len(bl)))
    # M7 даты справа
    dl = [l for l in (locate(pdf, d, 40) for d in E["dates"]) if l]
    right_ref = R["date_right"] is not None and R["date_right"] >= 0.7
    left_ref = R["date_left"] is not None and R["date_left"] <= 0.35 and rtl
    if not (right_ref or left_ref):
        res["M7"] = (None, "в эталоне дат у края строки нет")
    elif not dl:
        res["M7"] = (False, "даты не найдены в файле")
    elif right_ref:
        xs = [max(c["x1"] for c in l["chars"]) / pdf.W for l in dl]
        ok = sum(1 for x in xs if x >= R["date_right"] - 0.06)
        res["M7"] = (ok >= 0.8 * len(xs), "справа %d из %d дат (x=%.2f ширины, эталон %.2f)" % (ok, len(xs), sum(xs) / len(xs), R["date_right"]))
    else:
        xs = [min(c["x0"] for c in l["chars"]) / pdf.W for l in dl]
        ok = sum(1 for x in xs if x <= R["date_left"] + 0.06)
        res["M7"] = (ok >= 0.8 * len(xs), "у конца RTL-строки (слева) %d из %d дат (x=%.2f, эталон %.2f)" % (ok, len(xs), sum(xs) / len(xs), R["date_left"]))
    # M8 таблицы
    if f["tables"] == 0:
        res["M8"] = (None, "в эталоне таблиц нет")
    else:
        rows = _native_table_rows(out_fmt, out_raw) if out_fmt != "pdf" else None
        if out_fmt == "pdf":
            if f["table_borders"]:
                res["M8"] = (len(pdf.shapes) >= 0.5 * R["shapes"], "линий/рамок %d (эталон %d)" % (len(pdf.shapes), R["shapes"]))
            else:
                res["M8"] = (None, "таблицы без рамок: в PDF надёжно не измерить")
        elif rows is None:
            res["M8"] = (None, "файл не прочитан")
        else:
            res["M8"] = (rows >= 0.5 * f["table_rows"], "строк таблиц %d (эталон %d)" % (rows, f["table_rows"]))
    # M9 шрифтовой класс
    fc = font_class(pdf.body_font)
    res["M9"] = (fc == R["font_class"], "%s (%s), эталон %s" % (fc, pdf.body_font[:28], R["font_class"]))
    # M10 страницы
    res["M10"] = (pdf.pages <= R["pages"] + 1, "страниц %d, эталон %d" % (pdf.pages, R["pages"]))
    return res


def score(res):
    app = [v[0] for v in res.values() if v[0] is not None]
    return (100.0 * sum(1 for a in app if a) / len(app)) if app else None


# ---------------------------------------------------------------------------
# Тексты режимов
# ---------------------------------------------------------------------------

def synthetic_creative_text(doc):
    """Синтетический Creative-текст из исходного DOCX по тем же правилам, что у реального Creative:
    пустая строка перед заголовком и между местами работы, маркеры «- », дата отдельной строкой."""
    from docx.table import Table
    raw = open(os.path.join(DATA, doc + ".docx"), "rb").read()
    f = docx_facts(raw)
    d = Document(io.BytesIO(raw))
    lines = []
    for item in d.iter_inner_content():
        if isinstance(item, Table):
            for row in item.rows:
                cells = [c.text.strip() for c in row.cells]
                if len(cells) == 2 and _DATE_ONLY.match(cells[1]):
                    if not (lines and lines[-1] in f["headings"]):
                        lines.append("")
                    lines += [cells[0], cells[1]]
                else:
                    lines.append(" ".join(cells))
            continue
        t = item.text.strip()
        if not t:
            continue
        if t in f["headings"]:
            lines += ["", t]
        elif "\t" in t and _DATE_ANY.search(t.split("\t")[-1]):
            a, b = t.split("\t")[0], t.split("\t")[-1]
            if not (lines and lines[-1] in f["headings"]):
                lines.append("")
            lines += [a.strip(), b.strip()]
        elif t.lstrip("\u2022-\u2013* ").strip() in f["bullets"]:
            lines.append("- " + t.lstrip("\u2022-\u2013* ").strip())
        else:
            lines.append(t)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def creative_text(doc):
    with open(os.path.join(ROOT, "tests", "data", "creative_c1", "creative_texts.json"), encoding="utf-8") as fh:
        T = json.load(fh)
    key = {"designer": "designer", "ua": "ua", "engineer": "engineer_synthetic"}.get(doc)
    return T[key] if key else synthetic_creative_text(doc)


# ---------------------------------------------------------------------------
# Прогон через маршруты, как делает страница
# ---------------------------------------------------------------------------

def _fake_model(url, headers=None, json=None, timeout=None, **kw):
    """«Модель» Precise: возвращает блоки как есть, слегка меняя слова в хвосте каждого блока."""
    user = json["messages"][-1]["content"]
    m = re.search(r"(###ITEM_.*?)\n\nOUTPUT", user, re.S)
    body = m.group(1) if m else user
    out = []
    for blk in re.split(r"(?=###ITEM_\d+(?::\w+)?###)", body):
        if "\n" in blk and len(blk) > 60:
            head, tail = blk[:50], blk[50:]
            for a, b in ((" and ", " plus "), (" with ", " using "), (" \u0438 ", " \u043f\u043b\u044e\u0441 "), (" \u0442\u0430 ", " \u0456 ")):
                tail = tail.replace(a, b, 1)
            blk = head + tail
        out.append(blk)
    content = "".join(out)

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 1}}
    return R()


def make_client():
    from app import create_app
    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["GROQ_API_KEY"] = "matrix-key-not-used"
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin"] = "admin"
    return c


def precise_generate(client, doc, inp):
    """Как /api/improve в режиме Precise (модель подменена). Возвращает dict или {'error': ...}."""
    from unittest.mock import patch
    raw = open(os.path.join(DATA, doc + "." + inp), "rb").read()
    with patch("requests.post", side_effect=_fake_model):
        r = client.post("/api/improve", data={"file": (io.BytesIO(raw), doc + "." + inp), "creativity_mode": "precise"},
                        content_type="multipart/form-data")
    j = r.get_json(silent=True) or {}
    if r.status_code != 200 or not j.get("improved_resume"):
        return {"error": "improve %s: %s" % (r.status_code, (j.get("error") or "")[:80])}
    return {"improved_resume": j["improved_resume"], "display_text": j["display_text"],
            "item_ids": j.get("item_ids") or [], "text_for_export": j["improved_resume"]}


def export(client, out_fmt, doc, inp, state):
    """Вызвать маршрут экспорта с теми полями, которые отправляет index.html (по текущим условиям страницы)."""
    src_name = doc + "." + inp
    src = open(os.path.join(DATA, src_name), "rb").read()
    is_docx = inp == "docx"
    item_ids = state["item_ids"]
    creative = state["engine"] == "creative_v2"
    data = {"improved_resume": state["improved_resume"]}
    if out_fmt == "docx":
        # downloadImproved (вход .docx): original_file + item_ids, если они есть; иначе downloadDocx: только текст
        if is_docx:
            data["original_file"] = (io.BytesIO(src), src_name)
            if item_ids:
                data["item_ids"] = json.dumps(item_ids)
    else:
        # downloadOdt/Rtf/Pdf: original_file только для .docx, если item_ids непусты (Precise) или engine creative_v2
        if is_docx and item_ids:
            data["original_file"] = (io.BytesIO(src), src_name)
            data["item_ids"] = json.dumps(item_ids)
        elif is_docx and creative:
            data["original_file"] = (io.BytesIO(src), src_name)
    r = client.post("/api/improve/" + out_fmt, data=data, content_type="multipart/form-data")
    return r.status_code, r.data


# ---------------------------------------------------------------------------
# soffice
# ---------------------------------------------------------------------------

def soffice_to_pdf(paths, outdir):
    os.makedirs(outdir, exist_ok=True)
    for i in range(0, len(paths), 25):
        chunk = paths[i:i + 25]
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", outdir] + chunk,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
    for p in paths:      # то, что не сконвертировалось пачкой, пробуем по одному
        pdf = os.path.join(outdir, os.path.splitext(os.path.basename(p))[0] + ".pdf")
        if not os.path.exists(pdf):
            subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", outdir, p],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)


def run_pair(client, doc, inp, out_fmt, mode, work, refs=None, state=None):
    """Один экспорт без пакетной конвертации: для smoke-теста. Возвращает {M: (ok, detail)} или {'error'}."""
    refs = refs if refs is not None else {}
    R = refs.setdefault(doc, reference_profile(doc))
    if state is None:
        if mode == "precise":
            st = precise_generate(client, doc, inp)
            if "error" in st:
                return {"error": st["error"]}
            st["engine"] = "precise"
        else:
            t = creative_text(doc)
            st = {"improved_resume": t, "display_text": t, "item_ids": [], "engine": "creative_v2", "text_for_export": t}
    else:
        st = state
    status, body = export(client, out_fmt, doc, inp, st)
    if status != 200:
        return {"error": "экспорт %s: HTTP %s" % (out_fmt, status)}
    path = os.path.join(work, "%s__%s__%s__%s.%s" % (mode, doc, inp, out_fmt, out_fmt))
    open(path, "wb").write(body)
    if out_fmt != "pdf":
        soffice_to_pdf([path], os.path.join(work, "pdf"))
        pdf_path = os.path.join(work, "pdf", os.path.splitext(os.path.basename(path))[0] + ".pdf")
    else:
        pdf_path = path
    if not os.path.exists(pdf_path):
        return {"error": "soffice не отрисовал файл"}
    E = expected_from(mode, R, st["text_for_export"], st["display_text"])
    return measure(Pdf(pdf_path), R, E, out_fmt, body)


# ---------------------------------------------------------------------------
# Основной прогон и отчёт
# ---------------------------------------------------------------------------

def run_all(work):
    client = make_client()
    refs = {d: reference_profile(d) for d in DOCS}
    jobs = []     # (mode, doc, inp, out, path, status/err, body)
    states = {}
    errors = {}
    for mode in MODES:
        for doc in DOCS:
            for inp in INPUTS:
                if mode == "precise":
                    st = precise_generate(client, doc, inp)
                    if "error" in st:
                        errors[(mode, doc, inp)] = st["error"]
                        continue
                    st["engine"] = "precise"
                else:
                    t = creative_text(doc)
                    st = {"improved_resume": t, "display_text": t, "item_ids": [], "engine": "creative_v2", "text_for_export": t}
                states[(mode, doc, inp)] = st
                for out in OUTPUTS:
                    status, body = export(client, out, doc, inp, st)
                    key = (mode, doc, inp, out)
                    if status != 200:
                        errors[key] = "HTTP %s" % status
                        continue
                    path = os.path.join(work, "%s__%s__%s__%s.%s" % (mode, doc, inp, out, out))
                    open(path, "wb").write(body)
                    jobs.append((key, path, body))
    convert = [p for (k, p, b) in jobs if k[3] != "pdf"]
    soffice_to_pdf(convert, os.path.join(work, "pdf"))
    results = {}
    for (key, path, body) in jobs:
        mode, doc, inp, out = key
        pdf_path = path if out == "pdf" else os.path.join(work, "pdf", os.path.splitext(os.path.basename(path))[0] + ".pdf")
        if not os.path.exists(pdf_path):
            errors[key] = "soffice не отрисовал файл"
            continue
        st = states[(mode, doc, inp)]
        E = expected_from(mode, refs[doc], st["text_for_export"], st["display_text"])
        try:
            results[key] = measure(Pdf(pdf_path), refs[doc], E, out, body)
        except Exception as exc:       # ошибка измерения не должна ронять матрицу
            errors[key] = "измерение: %s" % type(exc).__name__
    return results, errors, refs


def calibration(work, refs):
    """Контроль самих метрик: корпусные odt/rtf/pdf сделаны soffice из эталонного DOCX, оформление там сохранено."""
    out = {}
    pdfdir = os.path.join(work, "cal")
    os.makedirs(pdfdir, exist_ok=True)
    conv = []
    for doc in DOCS:
        for ext in ("docx", "odt", "rtf"):
            dst = os.path.join(pdfdir, "%s__%s.%s" % (doc, ext, ext))
            shutil.copy(os.path.join(DATA, doc + "." + ext), dst)
            conv.append(dst)
    soffice_to_pdf(conv, os.path.join(pdfdir, "pdf"))
    for doc in DOCS:
        R = refs[doc]
        f = R["facts"]
        E = {"name": f["name"], "headings": f["headings"], "dates": f["dates"], "bullets": f["bullets"],
             "text": " ".join(p.text for p in Document(os.path.join(DATA, doc + ".docx")).paragraphs)}
        for ext in ("docx", "odt", "rtf", "pdf"):
            if ext == "pdf":
                pp = os.path.join(DATA, doc + ".pdf")
            else:
                pp = os.path.join(pdfdir, "pdf", "%s__%s.pdf" % (doc, ext))
            if os.path.exists(pp):
                raw = open(os.path.join(DATA, doc + "." + ext), "rb").read()
                out[(doc, ext)] = measure(Pdf(pp), R, E, ext, raw)
    return out


def _cell(vals):
    vals = [v for v in vals if v is not None]
    return ("%3.0f%%" % (sum(vals) / len(vals))) if vals else "  - "


def build_report(results, errors, cal):
    lines = []
    for mode in MODES:
        lines.append("### Режим %s: средний процент выполненных метрик (по 5 резюме)\n" % mode.capitalize())
        lines.append("| вход \\ выход | " + " | ".join(o.upper() for o in OUTPUTS) + " |")
        lines.append("|---|" + "---|" * len(OUTPUTS))
        for inp in INPUTS:
            row = []
            for out in OUTPUTS:
                sc = [score(results[(mode, d, inp, out)]) for d in DOCS if (mode, d, inp, out) in results]
                n_err = sum(1 for d in DOCS if (mode, d, inp, out) in errors or (mode, d, inp) in errors)
                c = _cell(sc)
                row.append(c + (" (ошибок: %d)" % n_err if n_err else ""))
            lines.append("| %s | %s |" % (inp.upper(), " | ".join(row)))
        lines.append("")
    # провалы
    lines.append("## Провалы по ячейкам\n")
    for mode in MODES:
        for inp in INPUTS:
            for out in OUTPUTS:
                fails = collections.defaultdict(list)
                for d in DOCS:
                    res = results.get((mode, d, inp, out))
                    if not res:
                        continue
                    for m in METRICS:
                        if m in res and res[m][0] is False:
                            fails[m].append((d, res[m][1]))
                if fails:
                    lines.append("**%s, %s -> %s**" % (mode.capitalize(), inp.upper(), out.upper()))
                    for m, lst in fails.items():
                        docs = ", ".join(d for d, _ in lst)
                        lines.append("- %s %s не выполнена у: %s. Пример: %s" % (m, METRIC_NAMES[m], docs, lst[0][1]))
                    lines.append("")
    if errors:
        lines.append("## Ошибки прогона\n")
        for k, v in sorted(errors.items(), key=lambda kv: str(kv[0])):
            lines.append("- %s: %s" % (" / ".join(k), v))
        lines.append("")
    # калибровка
    lines.append("## Калибровка метрик (корпусные файлы, сделанные soffice из эталонного DOCX)\n")
    lines.append("| файл | " + " | ".join(DOCS) + " |")
    lines.append("|---|" + "---|" * len(DOCS))
    for ext in ("docx", "odt", "rtf", "pdf"):
        row = []
        for d in DOCS:
            res = cal.get((d, ext))
            row.append(_cell([score(res)]) if res else "n/a")
        lines.append("| %s | %s |" % (ext.upper(), " | ".join(row)))
    lines.append("")
    bad = collections.defaultdict(list)
    for (d, ext), res in sorted(cal.items()):
        for m in METRICS:
            if m in res and res[m][0] is False:
                bad[m].append("%s.%s (%s)" % (d, ext, res[m][1]))
    lines.append("Метрики, которые не прошли даже на эталонных конвертациях (шум измерения):")
    if bad:
        for m, lst in bad.items():
            lines.append("- %s %s: %s" % (m, METRIC_NAMES[m], "; ".join(lst[:4])))
    else:
        lines.append("- нет")
    # доля неприменимых
    na = collections.Counter()
    tot = collections.Counter()
    for key, res in results.items():
        for m, v in res.items():
            tot[m] += 1
            na[m] += v[0] is None
    lines.append("\nДоля «неприменимо или не измерено» по метрикам (по всем ячейкам матрицы):")
    lines.append(", ".join("%s %.0f%%" % (m, 100.0 * na[m] / tot[m]) for m in METRICS if tot[m]))
    return "\n".join(lines) + "\n"


RELIABILITY = """
## Насколько надёжно измеряется каждая метрика

Все выходные файлы кроме PDF читаются через отрисовку в LibreOffice (soffice -> PDF -> pdfminer): это то, как файл
видит LibreOffice, Word может отрисовать иначе. Калибровка выше показывает шум самих метрик.

- M1 текст: надёжна для LTR (слова найдены + порядок >= 90%; порядок смягчён, потому что текст Precise из DOCX идёт
  «абзацы, потом таблицы»). Для иврита порядок не проверяется (в PDF визуальный порядок символов).
- M2 имя по центру, M3 размер имени, M10 страницы: надёжны (положение и кегль символов). Имя ищется как отдельная строка.
- M4 цвет заголовков надёжна; жирность определяется по названию шрифта: у иврита, арабского и китайского в PDF продукта
  жирный имитируется обводкой и не читается (для RTL-PDF проверяется только цвет). RTF с ивритом после отрисовки
  LibreOffice может терять цвет (видно в калибровке): для RTF+иврит метка неустойчива.
- M5 линия под заголовком: эвристика (тонкая горизонтальная фигура рядом с заголовком), надёжна для рамок и линий,
  не отличает линию от других тонких фигур.
- M6 маркеры: ищется символ маркера рядом с началом пункта (набор значков, символы из области частного использования);
  нумерованные списки как маркеры не считаются.
- M7 даты у края: по положению символов даты относительно эталона (для RTL у левого края). Даты внутри длинной строки
  измеряются по самим символам даты.
- M8 таблицы: в DOCX/ODT/RTF читаются из файла (строки таблиц, порог 50% эталона), надёжно. В PDF только если у таблиц
  эталона есть рамки (по числу линий, это грубо и считает линии заголовков); таблицы без рамок в PDF не измеримы.
- M9 шрифтовой класс: по названию основного шрифта (serif/sans), надёжно для латиницы и кириллицы; для иврита зависит от
  подстановки шрифта в LibreOffice.
"""

FINDINGS = """
## Комментарий автора к провалам (на момент прогона M1; не пересчитывается автоматически)

Гипотезы с доказательствами из прогона, ничего не исправлялось:

1. Вход DOCX, выходы ODT/RTF/PDF: M4 (цвет заголовков) и M9 (шрифт у ua) не проходят у резюме, где стиль заголовка
   объявлен в styles.xml дважды (оригиналы designer/engineer: два определения Heading1, в первом цвет 2E74B5, во втором
   1F4E79 + Arial + интервалы). LibreOffice берёт последнее, конвертеры продукта (python-docx) берут первое.
2. Вход DOCX -> ODT, резюме tabdates (правый таб): конвертер падает с ошибкой «<style:tab-stops> is not allowed in
   <style:style>» (в журнале), маршрут откатывается на простой ODT из текста: поэтому у tabdates пропали имя, линии и даты.
3. Вход DOCX -> PDF, иврит: у RTL-абзацев не рисуются маркеры списка, а таблица не зеркалится (даты справа, а не слева).
4. Вход ODT, RTF, PDF, TXT: original_file не отправляется (страница шлёт его только для .docx), выходные файлы строятся
   из текста со стандартными стилями: не выполняются M2-M5, M7, M8 почти везде.
5. Creative, вход DOCX, иврит: сборщик не распознаёт ивритские заголовки как образец HEADING (нет словаря иврита и стиля
   Heading), поэтому заголовки и основной текст получают чужие образцы (кегль имени/основного 1.71 против 2.29, линия
   у 50% заголовков).
6. Вход RTF на иврите: извлечение текста даёт «י?ו?ס?י?» (escape-последовательности \\uN? читаются как текст с «?»).
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-corpus", action="store_true")
    ap.add_argument("--keep", help="папка для сохранения всех выходных файлов")
    ap.add_argument("--report", default=os.path.join(ROOT, "matrix_report.md"))
    args = ap.parse_args()
    if args.rebuild_corpus:
        rebuild_corpus()
        print("Корпус пересоздан:", DATA)
        return
    work = args.keep or tempfile.mkdtemp(prefix="format_matrix_")
    os.makedirs(work, exist_ok=True)
    results, errors, refs = run_all(work)
    cal = calibration(work, refs)
    report = build_report(results, errors, cal)
    print(report)
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write("# Матрица сохранения оформления\n\nСгенерировано: python tools/format_matrix.py\n\n" + report
                 + RELIABILITY + FINDINGS)
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
