# app/utils/normalize_input.py
"""
Cycle N1a: нормализация входного файла в DOCX на сервере.

Идея серии N: любой входной файл превращается в DOCX, а дальше работает готовая цепочка DOCX
(Precise, Creative, экспорт DOCX/ODT/RTF/PDF). Здесь общий механизм и вход TXT;
входы ODT, RTF, PDF добавят следующие циклы одной строкой в таблицу ADAPTERS.

    normalize_to_docx(file_bytes, filename) -> bytes или None

* docx возвращается как есть;
* txt: адаптер txt_to_docx (шаблон по умолчанию + разбор ролей из creative_docx_builder);
* остальные форматы пока None (поведение для них прежнее);
* любое исключение внутри адаптера логируется (тип и стек, БЕЗ текста резюме и текста исключения)
  и даёт None, маршрут продолжает работать по старому пути.

Детерминизм: на одних и тех же байтах результат даёт один и тот же список абзацев и таблиц
(нет случайных значений и текущего времени внутри документа): item_ids в Precise считаются по
нормализованному DOCX при /api/improve, а на экспорте нормализация выполняется заново.
"""
import datetime
import io
import logging
import os
import re
import traceback

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from app.utils.creative_docx_builder import (
    build_creative_docx, _norm_heading, _is_contact, _DATE_ONLY, _RANGE, _HEADINGS, _SKILL_HEADINGS)

logger = logging.getLogger(__name__)


class NormalizeRejected(Exception):
    """Ожидаемый отказ адаптера (неподдерживаемый или повреждённый вход): в журнал пишется WARNING, а не ERROR.
    Текст исключения в журнал не попадает, поэтому в сообщение кладут только короткий код причины."""


# ---------------------------------------------------------------------------
# Шаблон по умолчанию для TXT (образцы всех ролей), LTR и RTL
# ---------------------------------------------------------------------------

_TEXT_W = 9638                 # ширина текста A4 с полями 2 см, твипы (17 см)
_NAVY = "1F3864"
_BLUE = "1F4E79"
_GRAY = "595959"
_BULLET_NUM_ID = 90
_BULLET_ABS_ID = 90


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rpr(size, bold=False, color=None, italic=False, rtl=False):
    """rPr в порядке схемы: rFonts, b, bCs, i, color, sz, szCs, rtl."""
    x = '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Arial"/>'
    if bold:
        x += "<w:b/><w:bCs/>"
    if italic:
        x += "<w:i/>"
    if color:
        x += '<w:color w:val="%s"/>' % color
    hp = int(round(size * 2))
    x += '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (hp, hp)
    if rtl:
        x += "<w:rtl/>"
    return "<w:rPr>%s</w:rPr>" % x


def _run(text, size, bold=False, color=None, italic=False, rtl=False):
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (_rpr(size, bold, color, italic, rtl), _esc(text))


def _ppr(rtl=False, jc=None, num=False, border=None, tab_right=None, before=None, after=None, ind=None):
    """pPr в порядке схемы: numPr, pBdr, tabs, bidi, spacing, ind, jc."""
    x = ""
    if num:
        x += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="%d"/></w:numPr>' % _BULLET_NUM_ID
    if border:
        x += '<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="1" w:color="%s"/></w:pBdr>' % border
    if tab_right:
        x += '<w:tabs><w:tab w:val="right" w:pos="%d"/></w:tabs>' % tab_right
    if rtl:
        x += "<w:bidi/>"
    if before is not None or after is not None:
        x += '<w:spacing w:before="%d" w:after="%d"/>' % (before or 0, after or 0)
    if ind:
        x += ind
    if jc:
        x += '<w:jc w:val="%s"/>' % jc
    return "<w:pPr>%s</w:pPr>" % x if x else ""


def _p(ppr, runs):
    return "<w:p %s>%s%s</w:p>" % (nsdecls("w"), ppr, runs)


def _job_table(left, right):
    """Однострочная таблица «должность | годы» для RTL (как в корпусе иврита): годы прижаты к левому краю."""
    c0 = _p(_ppr(rtl=True, before=0, after=0), _run(left, 11, True, None, False, True))
    c1 = _p(_ppr(rtl=True, jc="right", before=0, after=0), _run(right, 10, False, "666666", False, True))
    return (
        '<w:tbl %s><w:tblPr><w:bidiVisual/><w:tblW w:w="%d" w:type="dxa"/><w:tblLayout w:type="fixed"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="7338"/><w:gridCol w:w="2300"/></w:tblGrid>'
        '<w:tr><w:tc><w:tcPr><w:tcW w:w="7338" w:type="dxa"/></w:tcPr>%s</w:tc>'
        '<w:tc><w:tcPr><w:tcW w:w="2300" w:type="dxa"/></w:tcPr>%s</w:tc></w:tr></w:tbl>'
    ) % (nsdecls("w"), _TEXT_W, c0, c1)


def _add_bullet_numbering(doc):
    """Определение маркера «•» с фиксированными id (без случайных значений)."""
    numbering = doc.part.numbering_part.element
    abstract = parse_xml(
        '<w:abstractNum %s w:abstractNumId="%d"><w:multiLevelType w:val="singleLevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\u2022"/>'
        '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="260"/></w:pPr>'
        '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial" w:hint="default"/></w:rPr></w:lvl>'
        '</w:abstractNum>' % (nsdecls("w"), _BULLET_ABS_ID))
    num = parse_xml('<w:num %s w:numId="%d"><w:abstractNumId w:val="%d"/></w:num>'
                    % (nsdecls("w"), _BULLET_NUM_ID, _BULLET_ABS_ID))
    first_num = numbering.find(qn("w:num"))
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        numbering.append(abstract)
    numbering.append(num)


def build_default_template(rtl=False):
    """Небольшой DOCX с образцами ролей: имя по центру 20 pt жирное тёмно-синее, контакты по центру 9,5 pt серые,
    заголовок раздела жирный синий с линией, строка должности с датой (правый таб или bidi-таблица для RTL),
    маркеры, обычный абзац, строка навыка «метка: значение», отдельная дата."""
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = 11906 * 635, 16838 * 635       # A4 в EMU
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = 1134 * 635   # 2 см
    cp = doc.core_properties
    fixed = datetime.datetime(2020, 1, 1, 0, 0, 0)
    cp.author, cp.last_modified_by, cp.title = "ResumeAI", "ResumeAI", ""
    cp.created = cp.modified = fixed
    _add_bullet_numbering(doc)

    body = doc.element.body
    sect = body.find(qn("w:sectPr"))
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)

    def add(xml):
        el = parse_xml(xml)
        sect.addprevious(el)

    R = rtl
    add(_p(_ppr(R, "center", before=0, after=60), _run("Full Name", 20, True, _NAVY, False, R)))
    if not R:
        add(_p(_ppr(R, "center", before=0, after=60), _run("Professional Title", 11, False, _GRAY, False, R)))
    add(_p(_ppr(R, "center", before=0, after=120),
           _run("name@example.com | +1 555 010 0100 | linkedin.com/in/name | City", 9.5, False, _GRAY, False, R)))
    add(_p(_ppr(R, border=_BLUE, before=240, after=80), _run("Summary", 12, True, _BLUE, False, R)))
    add(_p(_ppr(R, before=0, after=80), _run("Sample paragraph with a short description of the profile.", 10.5, False, None, False, R)))
    add(_p(_ppr(R, border=_BLUE, before=240, after=80), _run("Experience", 12, True, _BLUE, False, R)))
    if R:
        add(_job_table("Position, Company", "2020 - 2024"))
    else:
        add(_p(_ppr(R, tab_right=_TEXT_W, before=120, after=40), _run("Position, Company\t2020 \u2013 2024", 11, True, None, False, R)))
    add(_p(_ppr(R, jc="right" if not R else None, before=0, after=40), _run("2020 \u2013 2024", 10, False, "666666", True, R)))
    add(_p(_ppr(R, num=True, before=0, after=40), _run("Sample achievement written as a list item", 10.5, False, None, False, R)))
    add(_p(_ppr(R, border=_BLUE, before=240, after=80), _run("Skills", 12, True, _BLUE, False, R)))
    add(_p(_ppr(R, before=0, after=40), _run("Label: ", 10.5, True, None, False, R) + _run("value, value, value", 10.5, False, None, False, R)))

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


_TEMPLATES = {}


def default_template(rtl=False):
    key = bool(rtl)
    if key not in _TEMPLATES:
        _TEMPLATES[key] = build_default_template(key)
    return _TEMPLATES[key]


# ---------------------------------------------------------------------------
# Подготовка сырого TXT к разбору ролей
# ---------------------------------------------------------------------------

# словарь разделов сверх базового (ru, uk, en, he): формы, которые встречаются в сыром TXT
_EXTRA_HEADINGS = {
    "experience", "work history", "professional background", "employment", "contact information",
    "personal details", "personal information", "technical skills", "skills and tools", "education and training",
    "опыт", "опыт работы", "профессиональный опыт", "трудовая деятельность", "места работы", "образование",
    "навыки", "навыки и умения", "технические навыки", "языки", "знание языков", "контакты",
    "контактная информация", "личные данные", "о себе", "обо мне", "курсы и сертификаты", "дополнительные сведения",
    "досвід", "досвід роботи", "професійний досвід", "освіта", "навички", "навички та вміння", "технічні навички",
    "мови", "знання мов", "контакти", "контактна інформація", "особисті дані", "про себе", "сертифікати та курси",
    "ניסיון", "ניסיון תעסוקתי", "ניסיון מקצועי", "השכלה", "כישורים", "מיומנויות", "שפות", "פרטי קשר",
    "פרטי התקשרות", "תקציר", "פרופיל", "הכשרה",
}
_EXTRA_SKILL_HEADINGS = {
    "technical skills", "skills and tools", "навыки и умения", "технические навыки", "знание языков",
    "навички та вміння", "технічні навички", "знання мов", "כישורים", "מיומנויות", "שפות",
}

_RE_BULLET = re.compile(r"^(?P<ind>\s*)(?:(?P<sym>[\u2022\u00b7*\u25cf\u25aa\u25e6\u2023])\s*|(?P<dash>[-\u2013\u2014])\s+|"
                        r"(?P<num>\d{1,2}[.)])\s+)(?P<rest>\S.*)$")
_RE_LETTER = re.compile(r"[^\W\d_]")
# строка вида «Должность, Компания, Город<таб или пробелы>2018 - 2024»: без явного разделителя разбор Creative
# принимает слово перед датой («Chicago») за месяц и забирает его в дату; ставим « , » (запятую) перед датой; «|» нельзя: строка с «|» и датой в первых строках читается как контакты
_RE_TRAILING_DATE = re.compile(r"^(?P<head>.*\S)[ \t]+(?P<date>%s)$" % _RANGE, re.I)
_RE_BAD_HEADING_CHARS = re.compile(r"[\d,@|.\u2014\u2013;]")


def _is_upper_heading(s):
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and s == s.upper() and s != s.lower() and len(letters) >= 3


def prepare_raw_text(text):
    """Привести сырой TXT к виду, который разбирает parse_creative_text.

    Возвращает (чистый_текст, заголовки, заголовки_навыков):
    * переводы строк \\r\\n и \\r превращаются в \\n, BOM убирается;
    * маркеры «•», «*», «·», «–», «-», «1.» приводятся к «• »;
    * заголовки (строка из словаря разделов, целиком в верхнем регистре, либо оканчивающаяся на «:») отдаются
      множеством, а не переписываются: текст строки не меняется.
    """
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [l.rstrip() for l in text.split("\n")]
    out = []
    extra_h = set()
    extra_s = set()
    nonblank = 0
    in_skills = False
    for line in lines:
        if not line.strip():
            out.append("")
            continue
        m = _RE_BULLET.match(line)
        if m and nonblank > 0:
            rest = m.group("rest").strip()
            ok = True
            if m.group("dash") or m.group("num"):
                ok = bool(_RE_LETTER.search(rest[:3])) and not _DATE_ONLY.match(rest)
            if ok:
                out.append("\u2022 " + rest)
                nonblank += 1
                continue
        s = line.strip()
        md = _RE_TRAILING_DATE.match(s) if len(s) <= 160 and not s.endswith(".") else None
        if md and not md.group("head").endswith((",", ";", "|")) and len(md.group("head").split()) >= 1 \
                and not _DATE_ONLY.match(s):
            line = md.group("head") + ", " + md.group("date")
            s = line.strip()
        low = _norm_heading(s)
        is_dict = low in _HEADINGS or low in _EXTRA_HEADINGS
        short = len(s) <= 40 and len(s.split()) <= 5
        generic = False
        if nonblank >= 2 and short and not is_dict and not _is_contact(s) and not _RE_BAD_HEADING_CHARS.search(s.rstrip(":")):
            if _is_upper_heading(s):
                generic = True
            elif s.endswith(":") and not in_skills:
                generic = True
        if is_dict or generic:
            extra_h.add(low)
            in_skills = low in _SKILL_HEADINGS or low in _EXTRA_SKILL_HEADINGS
            if in_skills:
                extra_s.add(low)
        out.append(line)
        nonblank += 1
    return "\n".join(out), extra_h, extra_s


def _is_rtl_text(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    rtl = sum(1 for c in letters if "\u0590" <= c <= "\u05ff" or "\u0600" <= c <= "\u06ff")
    return rtl / len(letters) > 0.3


# ---------------------------------------------------------------------------
# Адаптеры и диспетчер
# ---------------------------------------------------------------------------

def txt_to_docx(file_bytes):
    """TXT (UTF-8, с BOM или без) -> DOCX по шаблону по умолчанию. Исключение при любой проблеме."""
    try:
        text = file_bytes.decode("utf-8-sig")      # другие кодировки не поддерживаются (cp1251, utf-16): отказ
    except UnicodeDecodeError:
        raise NormalizeRejected("not_utf8")
    if "\x00" in text:
        raise NormalizeRejected("binary_data")
    clean, extra_h, extra_s = prepare_raw_text(text)
    template = default_template(_is_rtl_text(clean))
    return build_creative_docx(template, clean, extra_h, extra_s)


def _passthrough(file_bytes):
    return file_bytes


def _odt_adapter(file_bytes):
    """ODT -> DOCX (app/utils/odt_to_docx.py); импорт отложен, чтобы не было циклической зависимости."""
    from app.utils.odt_to_docx import odt_to_docx
    return odt_to_docx(file_bytes)


# Таблица адаптеров: {расширение без точки: функция(bytes) -> bytes DOCX}. N2-N4 добавляют odt, pdf, rtf.
ADAPTERS = {
    "docx": _passthrough,
    "txt": txt_to_docx,
    "odt": _odt_adapter,
}


def _detect_ext(file_bytes, filename):
    name = (filename or "").strip().lower()
    if "." in name:
        ext = name.rsplit(".", 1)[1]
        if ext:
            return ext
    head = file_bytes[:8]
    if head.startswith(b"PK"):
        return "docx"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"%PDF"):
        return "pdf"
    if b"\x00" not in file_bytes[:2048]:
        try:
            file_bytes[:4096].decode("utf-8-sig")
            return "txt"
        except UnicodeDecodeError:
            return None
    return None


def normalize_to_docx(file_bytes, filename):
    """Байты DOCX для любого поддерживаемого входа или None (формат не поддержан или адаптер упал)."""
    if not file_bytes:
        return None
    ext = _detect_ext(file_bytes, filename)
    adapter = ADAPTERS.get(ext)
    if adapter is None:
        return None
    try:
        result = adapter(file_bytes)
        return result if result else None
    except NormalizeRejected as exc:
        logger.warning("normalize_to_docx rejected: adapter=%s reason=%s", ext, exc)
        return None
    except Exception as exc:
        # в журнал: тип исключения и стек вызовов, без текста исключения (он может содержать текст резюме)
        frames = " <- ".join("%s:%d in %s" % (os.path.basename(f.filename), f.lineno, f.name)
                             for f in reversed(traceback.extract_tb(exc.__traceback__)))
        logger.error("normalize_to_docx failed: adapter=%s type=%s stack=%s", ext, type(exc).__name__, frames)
        return None
