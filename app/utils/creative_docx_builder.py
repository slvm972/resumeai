"""
Cycle C1: сборка НОВОГО DOCX из исходного DOCX и текста Creative.

Зачем: Creative отдаёт обычный текст без меток и item_ids=[] (может добавлять разделы, склеивать и
сокращать строки), поэтому править абзацы оригинала по item_ids нельзя. Здесь оригинал служит шаблоном:
из него берутся образцы оформления по ролям (имя, подзаголовок, контакты, заголовок раздела, строка
должности, дата, маркер, строка навыков, обычный текст, разделители), а новый документ собирается
из клонов этих образцов с подстановкой текста Creative.

Публичный интерфейс:
  parse_creative_text(text)                 -> список блоков-словарей с ролями
  build_creative_docx(original_bytes, text) -> bytes (DOCX)
  analyze_original(original_bytes)          -> отчёт о ролях элементов оригинала (для проверки/статистики)
  CreativeDocxError(code)                   -> понятная ошибка (коды: empty_text, garbage_text,
                                               bad_original, text_mismatch)

Роли блоков: NAME, TITLE, CONTACT, HEADING, JOB, DATE, BULLET, SKILL_ROW, BODY, BLANK.
Без LLM и без сети: правила выведены из реальных текстов Creative (en/ru/uk). Для иврита и арабского
парсер не ломается (минимум BODY/HEADING по структуре).

Запрет «не придумывать и не терять текст»: после сборки сравнивается последовательность текстов
нового DOCX с блоками (без служебных «- », «•» и пробелов); при расхождении выбрасывается
CreativeDocxError('text_mismatch'), вызывающий код должен откатиться на прежний путь.
"""
import copy
import io
import re

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

# ---------------------------------------------------------------------------
# Ошибка
# ---------------------------------------------------------------------------


class CreativeDocxError(ValueError):
    """Понятная ошибка сборки. code: empty_text | garbage_text | bad_original | text_mismatch."""

    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code


# ---------------------------------------------------------------------------
# Словари и регулярные выражения
# ---------------------------------------------------------------------------

_YEAR = r"(?:19|20)\d{2}"
_PRESENT = (r"(?:present|now|current|today|ongoing|по\s+настоящее\s+время|по\s+н\.\s?в\.|наст\.\s?время|"
            r"настоящее\s+время|теперішній\s+час|теперішній|сьогодні|нині|дотепер|досі|по\s+сей\s+день)")
_MONTH = r"(?:[A-Za-zА-Яа-яІіЇїЄє]{3,9}\.?\s+)?(?:\d{1,2}[./])?"
_RANGE = r"(?:%s%s)\s*[-\u2013\u2014]\s*(?:%s%s|%s)" % (_MONTH, _YEAR, _MONTH, _YEAR, _PRESENT)
_DATE_ONLY = re.compile(r"^\s*(?:%s|%s)\s*$" % (_RANGE, _YEAR), re.I)
_DATE_INLINE = re.compile(r"^(?P<head>.+?)\s*(?P<sep>[,;|\u00b7]?)\s*(?P<date>%s)\s*$" % _RANGE, re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s().\-]{7,}\d")
_BULLET = re.compile(r"^\s*([-\u2022*\u00b7])\s+(\S.*)$")
_LABEL_VALUE = re.compile(r"^(?P<label>[^:\n]{1,40}:)\s+(?P<value>\S.*)$")
_LABEL_ONLY = re.compile(r"^(?P<label>[^:\n]{1,40}:)\s*$")
_DASH_SEP = re.compile(r"\s[\u2014\u2013]\s")

_HEADINGS = {
    # en
    "summary", "professional summary", "profile", "professional profile", "about me", "objective",
    "career objective", "experience", "work experience", "professional experience", "employment history",
    "career highlights", "education", "skills", "key skills", "technical skills", "core skills",
    "languages", "certifications", "certificates", "courses", "projects", "awards", "publications",
    "interests", "hobbies", "references", "contact", "contacts", "additional information", "volunteering",
    "achievements",
    # ru
    "профиль", "профессиональный профиль", "о себе", "цель", "резюме", "опыт работы", "образование",
    "навыки", "ключевые навыки", "профессиональные навыки", "языки", "сертификаты", "курсы", "проекты",
    "достижения", "дополнительно", "дополнительная информация", "контакты", "интересы", "рекомендации",
    # uk
    "профіль", "професійний профіль", "про себе", "мета", "досвід роботи", "освіта", "навички",
    "ключові навички", "мови", "сертифікати", "проєкти", "досягнення", "додатково", "інтереси",
    # he
    "תקציר", "פרופיל", "ניסיון תעסוקתי", "ניסיון מקצועי", "השכלה", "כישורים", "מיומנויות", "שפות",
    "תעודות", "קורסים", "פרויקטים", "הישגים", "פרטי קשר", "תחומי עניין", "המלצות",
}
_SKILL_HEADINGS = {
    "skills", "key skills", "technical skills", "core skills", "languages", "навыки", "ключевые навыки",
    "профессиональные навыки", "языки", "навички", "ключові навички", "мови",
    "כישורים", "מיומנויות", "שפות",
}


def _is_contact(s):
    return bool(_EMAIL.search(s) or (("|" in s) and _PHONE.search(s)) or re.search(r"(linkedin|github)\.com", s, re.I))


def _norm_heading(s):
    return re.sub(r"\s+", " ", s.strip().rstrip(":").strip()).lower()


def _split_inline_date(s):
    """('Sales Associate, FreshMart', ', ', '2020 - 2024') для строки с датой в конце или None."""
    m = _DATE_INLINE.match(s)
    if not m or not m.group("head").strip():
        return None
    return m.group("head").strip().rstrip(",;|").strip(), m.group("sep") or "", m.group("date").strip()


# ---------------------------------------------------------------------------
# Парсер текста Creative
# ---------------------------------------------------------------------------


def parse_creative_text(text, extra_headings=None, extra_skill_headings=None):
    """Разобрать текст Creative в блоки. Блок: {'role', 'text', ...}.

    JOB может содержать 'date' (дата в конце строки отделена) и 'raw' (исходная строка);
    SKILL_ROW содержит 'label' и 'value'; DATE идёт отдельным блоком, если стоит отдельной строкой.
    BLANK — разделитель (подряд идущие пустые строки схлопываются, ведущие и хвостовые отбрасываются).
    """
    if text is None:
        return []
    # Cycle N1a: необязательные дополнительные заголовки (нормализованные через _norm_heading), по умолчанию пусто:
    # поведение для текстов Creative не меняется.
    extra_h = frozenset(extra_headings or ())
    extra_s = frozenset(extra_skill_headings or ())
    raw_lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [l.rstrip() for l in raw_lines]
    n = len(lines)

    def next_nonblank(i):
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        return j if j < n else None

    blocks = []
    seen = 0            # сколько непустых строк уже разобрано
    prev_blank = True
    section = ""        # 'skills' | 'other'
    i = 0
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            if blocks and blocks[-1]["role"] != "BLANK":
                blocks.append({"role": "BLANK", "text": ""})
            prev_blank = True
            i += 1
            continue

        nxt_i = next_nonblank(i)
        nxt = lines[nxt_i].strip() if nxt_i is not None else None
        immediate_next_nonblank = (i + 1 < n and bool(lines[i + 1].strip()))
        block = None

        m_b = _BULLET.match(raw)
        if seen == 0:
            block = {"role": "NAME", "text": s}
        elif m_b:
            block = {"role": "BULLET", "text": m_b.group(2).strip()}
        elif seen <= 3 and _is_contact(s):
            block = {"role": "CONTACT", "text": s}
        elif seen == 1 and nxt is not None and _is_contact(nxt) and len(s) <= 90 and not _is_contact(s):
            block = {"role": "TITLE", "text": s}
        else:
            low = _norm_heading(s)
            structural = (prev_blank and len(s) <= 40 and len(s.split()) <= 5 and immediate_next_nonblank
                          and not re.search(r"[\d,@|:.\u2014\u2013]", s))
            label_like = section == "skills" and s.endswith(":") and nxt is not None   # «Языки:» внутри навыков
            if (low in _HEADINGS or low in extra_h or low in extra_s or structural) and not label_like:
                block = {"role": "HEADING", "text": s}
                section = "skills" if (low in _SKILL_HEADINGS or low in extra_s) else "other"
            elif _DATE_ONLY.match(s):
                block = {"role": "DATE", "text": s}
            else:
                next_is_date = nxt is not None and bool(_DATE_ONLY.match(nxt))
                inline = _split_inline_date(s) if len(s) <= 160 and not s.endswith(".") else None
                lv = _LABEL_VALUE.match(s) if section == "skills" else None
                lo = _LABEL_ONLY.match(s) if section == "skills" else None
                if lv:
                    block = {"role": "SKILL_ROW", "text": s, "label": lv.group("label"), "value": lv.group("value")}
                elif (lo and nxt is not None and immediate_next_nonblank and not _BULLET.match(nxt)
                      and not _LABEL_ONLY.match(nxt) and _norm_heading(nxt) not in _HEADINGS):
                    # «Метка:» на отдельной строке, значение на следующей (вывод Precise)
                    block = {"role": "SKILL_ROW", "text": s + " " + nxt, "label": lo.group("label"), "value": nxt}
                    i += 1
                elif section != "skills" and inline and len(s) <= 160:
                    head, sep, date = inline
                    block = {"role": "JOB", "text": head, "date": date, "raw": s, "sep": sep}
                elif next_is_date and len(s) <= 160 and not s.endswith(".") and section != "skills":
                    block = {"role": "JOB", "text": s, "date": None, "raw": s, "sep": ""}
                elif (section != "skills" and _DASH_SEP.search(s) and len(s) <= 140 and not s.endswith(".")):
                    block = {"role": "JOB", "text": s, "date": None, "raw": s, "sep": ""}
                else:
                    block = {"role": "BODY", "text": s}
        blocks.append(block)
        seen += 1
        prev_blank = False
        i += 1

    while blocks and blocks[-1]["role"] == "BLANK":
        blocks.pop()
    return blocks


def _pieces(block):
    """Тексты, из которых должен состоять документ для блока (для проверки «ничего не потеряно»)."""
    r = block["role"]
    if r == "BLANK":
        return []
    if r == "SKILL_ROW":
        return [block["label"], block["value"]]
    if r == "JOB":
        return [block["text"]] + ([block["date"]] if block.get("date") else [])
    return [block["text"]]


# ---------------------------------------------------------------------------
# Работа с XML абзацев и таблиц
# ---------------------------------------------------------------------------


def _ppr_has(p, tag):
    ppr = p.find(qn("w:pPr"))
    return ppr is not None and ppr.find(qn(tag)) is not None


def _par_text(p_el, doc_part=None):
    return Paragraph(p_el, doc_part).text


def _style_id(p_el):
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None and ppr.find(qn("w:pStyle")) is not None:
        return ppr.find(qn("w:pStyle")).get(qn("w:val")) or ""
    return ""


def _jc(p_el):
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None and ppr.find(qn("w:jc")) is not None:
        return ppr.find(qn("w:jc")).get(qn("w:val"))
    return None


def _first_run_bold(p_el):
    for r in p_el.iter(qn("w:r")):
        rpr = r.find(qn("w:rPr"))
        if rpr is not None and rpr.find(qn("w:b")) is not None:
            v = rpr.find(qn("w:b")).get(qn("w:val"))
            return v is None or v.lower() not in ("0", "false", "off")
        return False
    return False


def _fill_paragraph(p_el, texts):
    """Подставить текст в клон абзаца: сохраняются pPr и форматирование первого run (для второго
    значения берётся форматирование второго run, если он был). Остальное содержимое удаляется."""
    runs = [r for r in p_el.iter(qn("w:r"))]
    rprs = []
    for r in runs:
        rpr = r.find(qn("w:rPr"))
        rprs.append(copy.deepcopy(rpr) if rpr is not None else None)
    for child in list(p_el):
        if child.tag != qn("w:pPr"):
            p_el.remove(child)
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None and ppr.find(qn("w:sectPr")) is not None:
        ppr.remove(ppr.find(qn("w:sectPr")))
    holder = Paragraph(p_el, None)
    for idx, t in enumerate(texts):
        r_el = OxmlElement("w:r")
        rpr = rprs[idx] if idx < len(rprs) else (rprs[0] if rprs else None)
        if rpr is not None:
            r_el.append(copy.deepcopy(rpr))
        p_el.append(r_el)
        Run(r_el, holder).text = t


def _fill_cell(tc_el, text):
    """Оставить в ячейке первый абзац с текстом; лишние абзацы удалить, чтобы не осталось старого текста."""
    ps = tc_el.findall(qn("w:p"))
    if not ps:
        p = OxmlElement("w:p")
        tc_el.append(p)
        ps = [p]
    for extra in ps[1:]:
        tc_el.remove(extra)
    _fill_paragraph(ps[0], [text])


def _one_row_table(tbl_el, row_idx=0):
    t = copy.deepcopy(tbl_el)
    rows = t.findall(qn("w:tr"))
    for i, r in enumerate(rows):
        if i != row_idx:
            t.remove(r)
    return t


def _cells(tbl_el):
    return tbl_el.find(qn("w:tr")).findall(qn("w:tc"))


def _cell_texts(tr_el):
    out = []
    for tc in tr_el.findall(qn("w:tc")):
        out.append("\n".join(Paragraph(p, None).text for p in tc.findall(qn("w:p"))).strip())
    return out


# ---------------------------------------------------------------------------
# Анализ оригинала и образцы
# ---------------------------------------------------------------------------


def _first_run_size(p_el):
    """Размер шрифта первого фрагмента с текстом в полупунктах (только прямое форматирование) или None."""
    for r in p_el.iter(qn("w:r")):
        if not "".join(t.text or "" for t in r.iter(qn("w:t"))).strip():
            continue
        rpr = r.find(qn("w:rPr"))
        if rpr is not None and rpr.find(qn("w:sz")) is not None:
            try:
                return int(rpr.find(qn("w:sz")).get(qn("w:val")))
            except (TypeError, ValueError):
                return None
        return None
    return None


def _body_size(elems):
    """Типичный размер основного текста (медиана размеров длинных абзацев) или None."""
    sizes = []
    for e in elems:
        if e.tag == qn("w:p") and len(_par_text(e).strip()) > 40:
            sz = _first_run_size(e)
            if sz:
                sizes.append(sz)
    sizes.sort()
    return sizes[len(sizes) // 2] if sizes else None


def _looks_like_heading(el, s, body_sz):
    """Cycle P2: заголовок раздела по ОФОРМЛЕНИЮ образца, а не по словарю слов (иврит, арабский и др.).

    Короткая строка без знаков предложения и двоеточия на конце, у которой есть нижняя линия абзаца
    либо жирный шрифт заметно крупнее основного текста (на 2 пт и больше).
    """
    if len(s) > 60 or len(s.split()) > 6 or s.endswith(":") or re.search(r"[.!?;@|]\s*$", s):
        return False
    if _is_contact(s) or _DATE_ONLY.match(s) or _BULLET.match(s):
        return False
    ppr = el.find(qn("w:pPr"))
    bdr = ppr.find(qn("w:pBdr")) if ppr is not None else None
    if bdr is not None and bdr.find(qn("w:bottom")) is not None:
        return True
    sz = _first_run_size(el)
    return bool(_first_run_bold(el) and sz and body_sz and sz >= body_sz + 4)


def _analyze(doc):
    """Вернуть (samples, report). report: список (kind, текст, роль) в порядке документа."""
    body = doc.element.body
    samples = {}
    report = []
    elems = [e for e in body.iterchildren() if e.tag in (qn("w:p"), qn("w:tbl"))]
    n_nonblank = 0
    section = ""
    seen_heading = False
    body_sz = _body_size(elems)

    def put(role, el, **extra):
        if role not in samples:
            samples[role] = dict(el=copy.deepcopy(el), **extra)

    for idx, el in enumerate(elems):
        if el.tag == qn("w:tbl"):
            rows = el.findall(qn("w:tr"))
            texts = _cell_texts(rows[0]) if rows else []
            if len(texts) == 2 and _DATE_ONLY.match(texts[1] or "-"):
                put("JOB_TABLE", _one_row_table(el), kind="tbl")
                report.append(("tbl", " | ".join(texts), "JOB"))
            elif len(texts) == 2 and texts[0].rstrip().endswith(":"):
                put("SKILL_TABLE", _one_row_table(el), kind="tbl")
                report.append(("tbl", " | ".join(texts), "SKILL_ROW"))
            else:
                report.append(("tbl", " | ".join(texts), "TABLE_OTHER"))
            n_nonblank += 1
            continue

        text = _par_text(el)
        s = text.strip()
        if not s:
            if _ppr_has(el, "w:pBdr"):
                put("SEP_SECTION", el)
                report.append(("p", "", "SEP_SECTION"))
            else:
                put("SEP_PLAIN", el)
                report.append(("p", "", "SEP_PLAIN"))
            continue

        sid = _style_id(el)
        low = _norm_heading(s)
        role = None
        extra = {}
        m_b = _BULLET.match(text)
        if n_nonblank == 0:
            role = "NAME"
        elif _ppr_has(el, "w:numPr") or m_b:
            role = "BULLET"
            if m_b and not _ppr_has(el, "w:numPr"):
                extra["prefix"] = m_b.group(1)
        elif n_nonblank <= 3 and _is_contact(s):
            role = "CONTACT"
        elif (n_nonblank == 1 and not seen_heading and _jc(el) == "center" and not _is_contact(s) and len(s) <= 90):
            role = "TITLE"
        elif sid.lower().startswith("heading") or low in _HEADINGS or _looks_like_heading(el, s, body_sz):
            role = "HEADING"
            seen_heading = True
            section = "skills" if low in _SKILL_HEADINGS else "other"
        elif _DATE_ONLY.match(s):
            role = "DATE"
        elif "\t" in text and _DATE_INLINE.match(text.replace("\t", " ")) and _DATE_ONLY.match(text.split("\t")[-1]):
            role = "JOB_TAB"
        else:
            nxt = None
            for e2 in elems[idx + 1:]:
                if e2.tag == qn("w:p") and _par_text(e2).strip():
                    nxt = _par_text(e2).strip()
                    break
                if e2.tag == qn("w:tbl"):
                    break
            if nxt is not None and _DATE_ONLY.match(nxt) and _first_run_bold(el):
                role = "JOB_PARA"
            elif section == "skills" and _LABEL_VALUE.match(s):
                role = "SKILL_PARA"
            else:
                role = "BODY"
        n_nonblank += 1
        put(role, el, kind="p", **extra)
        report.append(("p", s, {"JOB_TAB": "JOB", "JOB_PARA": "JOB", "SKILL_PARA": "SKILL_ROW"}.get(role, role)))
    return samples, report


def _load_original(original_bytes):
    try:
        return Document(io.BytesIO(original_bytes))
    except Exception as exc:
        raise CreativeDocxError("bad_original", "Original DOCX cannot be read") from exc


def analyze_original(original_bytes):
    """Роли элементов оригинала: [(kind, text, role), ...] и набор найденных образцов."""
    doc = _load_original(original_bytes)
    samples, report = _analyze(doc)
    return {"elements": report, "samples": sorted(samples.keys())}


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------

_FALLBACK = {
    "NAME": ("NAME", "BODY"),
    "TITLE": ("TITLE", "CONTACT", "BODY"),
    "CONTACT": ("CONTACT", "BODY"),
    "HEADING": ("HEADING", "BODY"),
    "BODY": ("BODY",),
    "DATE": ("DATE", "BODY"),
    "BULLET": ("BULLET", "BODY"),
}


def _new_plain_paragraph():
    return OxmlElement("w:p")


def _norm(s, loose=False):
    """Для сравнения: без пробелов и маркеров списка (и, в мягком режиме, без разделителей ,;|)."""
    pat = r"[\s\u2022\u00b7*\-\u2013\u2014\u00a0\u202f" + (",;|" if loose else "") + "]+"
    return re.sub(pat, "", s)


def _doc_texts(doc):
    out = []
    for item in doc.iter_inner_content():
        if isinstance(item, Table):
            for row in item.rows:
                for cell in row.cells:
                    t = "".join(p.text for p in cell.paragraphs)
                    if t.strip():
                        out.append(t)
        else:
            if item.text.strip():
                out.append(item.text)
    return out


def build_creative_docx(original_docx_bytes, creative_text, extra_headings=None, extra_skill_headings=None):
    """Собрать новый DOCX: оригинал как шаблон, текст из creative_text. Возвращает bytes."""
    if creative_text is None or not str(creative_text).strip():
        raise CreativeDocxError("empty_text", "Creative text is empty")
    if sum(ch.isalnum() for ch in str(creative_text)) < 5:
        raise CreativeDocxError("garbage_text", "Creative text has no usable content")
    blocks = parse_creative_text(creative_text, extra_headings, extra_skill_headings)
    if not [b for b in blocks if b["role"] != "BLANK"]:
        raise CreativeDocxError("garbage_text", "Creative text has no usable content")

    doc = _load_original(original_docx_bytes)
    samples, _report = _analyze(doc)
    body = doc.element.body
    sect = body.find(qn("w:sectPr"))
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)

    intended = []   # какой текст мы намеревались положить в документ (для проверки подстановки)

    def add(el):
        if sect is not None:
            sect.addprevious(el)
        else:
            body.append(el)

    def pick_par(role):
        for r in _FALLBACK.get(role, (role, "BODY")):
            s = samples.get(r)
            if s is not None and s["el"].tag == qn("w:p"):
                return copy.deepcopy(s["el"]), r
        for s in samples.values():
            if s["el"].tag == qn("w:p") and _par_text(s["el"]).strip():
                return copy.deepcopy(s["el"]), "ANY"
        return _new_plain_paragraph(), "NONE"

    def par(role, text, prefix=None):
        el, used = pick_par(role)
        pre = ""
        if role == "BULLET":
            sample = samples.get("BULLET")
            if sample is None:
                pre = "\u2022 "
            elif sample.get("prefix"):
                pre = sample["prefix"] + " "
        _fill_paragraph(el, [pre + text])
        intended.append(text)
        add(el)

    def two_cell_table(sample_key, left, right):
        t = copy.deepcopy(samples[sample_key]["el"])
        cells = _cells(t)
        _fill_cell(cells[0], left)
        _fill_cell(cells[1], right)
        intended.extend([left, right])
        add(t)

    def job(text, date):
        if "JOB_TABLE" in samples:
            two_cell_table("JOB_TABLE", text, date or "")
        elif "JOB_TAB" in samples:
            el = copy.deepcopy(samples["JOB_TAB"]["el"])
            _fill_paragraph(el, [text + ("\t" + date if date else "")])
            intended.extend([text] + ([date] if date else []))
            add(el)
        elif "JOB_PARA" in samples:
            el = copy.deepcopy(samples["JOB_PARA"]["el"])
            _fill_paragraph(el, [text])
            intended.append(text)
            add(el)
            if date:
                par("DATE", date)
        else:
            par("BODY", text + (", " + date if date else ""))

    i = 0
    nb = len(blocks)
    while i < nb:
        b = blocks[i]
        role = b["role"]
        nxt = blocks[i + 1] if i + 1 < nb else None
        if role == "BLANK":
            after = nxt["role"] if nxt else None
            key = "SEP_SECTION" if after == "HEADING" else "SEP_PLAIN"
            if key in samples:
                add(copy.deepcopy(samples[key]["el"]))
        elif role in ("NAME", "TITLE", "CONTACT", "HEADING", "BODY", "BULLET"):
            par(role, b["text"])
        elif role == "DATE":
            par("DATE", b["text"])
        elif role == "JOB":
            date = b.get("date")
            if not date and nxt is not None and nxt["role"] == "DATE" and (
                    "JOB_TABLE" in samples or "JOB_TAB" in samples):
                date = nxt["text"]
                i += 1                       # DATE поглощена строкой должности
            elif not date and nxt is not None and nxt["role"] == "DATE" and "JOB_PARA" in samples:
                job(b["text"], None)
                par("DATE", nxt["text"])
                i += 2
                continue
            if "JOB_TABLE" not in samples and "JOB_TAB" not in samples and "JOB_PARA" not in samples:
                par("BODY", b["raw"] if not b.get("date") else b["raw"])   # образца строки должности нет
            else:
                job(b["text"], date)
        elif role == "SKILL_ROW":
            if "SKILL_TABLE" in samples:
                two_cell_table("SKILL_TABLE", b["label"], b["value"])
            elif "SKILL_PARA" in samples:
                el = copy.deepcopy(samples["SKILL_PARA"]["el"])
                nruns = len(list(el.iter(qn("w:r"))))
                _fill_paragraph(el, [b["label"] + " ", b["value"]] if nruns >= 2 else [b["text"]])
                intended.extend([b["label"], b["value"]])
                add(el)
            else:
                par("BODY", b["text"])
        i += 1

    out = io.BytesIO()
    doc.save(out)
    result = out.getvalue()

    # Проверка «не придумывать и не терять»:
    # 1) текст собранного документа == то, что намеревались положить (подстановка не исказила и не добавила);
    # 2) намеченное == тексты блоков (с точностью до разделителей ,;| между названием и датой).
    actual = _norm("".join(_doc_texts(Document(io.BytesIO(result)))))
    if _norm("".join(intended)) != actual:
        raise CreativeDocxError("text_mismatch", "Built document text differs from the intended text")
    if _norm("".join(intended), True) != _norm("".join(p for b in blocks for p in _pieces(b)), True):
        raise CreativeDocxError("text_mismatch", "Built document text differs from Creative text")
    return result
