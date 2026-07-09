# app/missing_routes4.py
import re, uuid, time

# ---------------------------------------------------------------------------
# Токен-маркеры — UUID-based, LLM не может "исправить" их
# ---------------------------------------------------------------------------

def _make_token():
    """Уникальный hex-токен который LLM не воспримет как осмысленный текст."""
    return "@@@" + uuid.uuid4().hex.upper()[:12] + "@@@"

# Маркер разделитель элементов
def _make_sep():
    return "###ITEM_{}###"


# ---------------------------------------------------------------------------
# Утилиты документа
# ---------------------------------------------------------------------------

def _extract_full_text_from_docx(file_bytes):
    from docx import Document
    import io
    doc = Document(io.BytesIO(file_bytes))
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text.strip())
    return "\n".join(parts)


def _detect_language_simple(text):
    sample = text[:500]
    counts = {
        "Hebrew":  sum(1 for c in sample if "\u05d0" <= c <= "\u05ea"),
        "Russian": sum(1 for c in sample if "\u0400" <= c <= "\u04ff"),
        "Arabic":  sum(1 for c in sample if "\u0600" <= c <= "\u06ff"),
    }
    best = max(counts, key=counts.get)
    return best if counts[best] > 5 else "English"


def _extract_structured(doc):
    """
    Вернуть список {'para': <Paragraph>, 'text': str} для всех
    непустых элементов документа, обходя таблицы по позиции (ri, ci).
    """
    items = []
    for para in doc.paragraphs:
        if para.text.strip():
            items.append({"para": para, "text": para.text.strip()})
    for table in doc.tables:
        seen = set()
        for ri, row in enumerate(table.rows):
            for ci, cell in enumerate(row.cells):
                key = (id(table), ri, ci)
                if key in seen:
                    continue
                seen.add(key)
                for para in cell.paragraphs:
                    if para.text.strip():
                        items.append({"para": para, "text": para.text.strip()})
    return items


def _para_has_complex_formatting(para):
    """
    True если параграф содержит сложное форматирование внутри runs:
    разные bold/italic/color/size/hyperlinks.
    Такие параграфы не трогаем — надёжность важнее агрессивного обновления.
    """
    runs = para.runs
    if len(runs) <= 1:
        return False
    # Проверяем есть ли гиперссылки в XML
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    if para._p.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline"):
        return True
    # Проверяем разнородность форматирования между runs.
    # ВАЖНО: проверяем ВСЕ runs (включая пустые) — пустой run может нести
    # underline/bold и т.п., которое будет молча потеряно при упрощённой замене.
    bolds   = set(bool(r.bold)      for r in runs)
    italics = set(bool(r.italic)    for r in runs)
    underlines = set(bool(r.underline) for r in runs)
    colors  = set(r.font.color.rgb if r.font.color and r.font.color.type else None for r in runs)
    sizes   = set(r.font.size for r in runs)
    # Если есть смешанные значения — форматирование неоднородное
    if len(bolds) > 1 or len(italics) > 1 or len(underlines) > 1:
        return True
    if len(colors) > 1 or len(sizes) > 1:
        return True
    # Если ЛЮБОЙ run имеет underline=True — считаем форматирование значимым
    # и не трогаем параграф (underline часто означает email/ссылку)
    if any(r.underline for r in runs):
        return True
    return False


def _replace_para_text(para, new_text):
    """
    Run-safe замена текста параграфа.
    Стратегия:
    1. Нет runs → para.text (стандартный путь)
    2. Один run → заменяем его текст, форматирование сохраняется
    3. Однородное форматирование (все runs одинаковые) →
       весь текст в первый run, остальные очищаем (форматирование не теряется)
    4. Неоднородное форматирование (bold+normal, hyperlinks, разные цвета) →
       НЕ ТРОГАЕМ, оставляем оригинал. Надёжность важнее обновления.
    """
    runs = para.runs
    if not runs:
        para.text = new_text
        return
    if len(runs) == 1:
        runs[0].text = new_text
        return
    # Проверяем сложность форматирования
    if _para_has_complex_formatting(para):
        # Оставляем оригинал — не рискуем потерять форматирование
        return
    # Однородное форматирование — весь текст в первый run, остальные обнуляем
    runs[0].text = new_text
    for r in runs[1:]:
        r.text = ""


# ---------------------------------------------------------------------------
# Protected Tokens — детерминированная защита ДО AI
# ---------------------------------------------------------------------------

# Паттерны защищаемых сущностей (порядок важен — более специфичные первыми)
_PROTECT_PATTERNS = [
    # URL: http/https/www и LinkedIn/GitHub/GitLab
    (r"https?://[^\s]+", "URL"),
    (r"www\.[A-Za-z0-9\-]+\.[A-Za-z]{2,}[^\s]*", "URL"),
    (r"(?:linkedin\.com|github\.com|gitlab\.com)/[^\s]+", "URL"),
    # Email
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "EMAIL"),
    # Дата рождения дд/мм/гггг или дд.мм.гггг
    (r"\d{2}[/.]\d{2}[/.]\d{4}", "DATE_BIRTH"),
    # Диапазон дат: 2023-2020 / 1997-1991 / - 2001
    (r"(?<!\d)(?:\d{4}\s*[-\u2013]\s*\d{4}|[-\u2013]\s*\d{4}|\d{4}\s*[-\u2013])(?!\d)", "DATE_RANGE"),
    # Числа с % и суффиксами K/M/B — защищаем до ID_NUM
    (r"(?<!\d)\d+(?:[,.]\d+)?\s*(?:%|K\+?|M\+?|B\+?|k\+?|m\+?|\+)(?!\d)", "NUM_METRIC"),
    # ID: 7+ цифр подряд
    (r"\b\d{7,}\b", "ID_NUM"),
    # Сертификаты известных форматов: AZ-104, AWS-SAA-C03, CCNA и т.д.
    (r"\b(?:AWS|AZ|MS|DP|AI|SC|PL|DA|MB|MD|CKA|CKS|CKAD|CCNA|CCNP|CCIE|LPIC|RHCSA|RHCE|GCP|GKE|PCEP|PMI|PMP|ITIL|CEH|OSCP|CompTIA|Security\+|Network\+|A\+|Linux\+)\s*[-:]?\s*[A-Z0-9]{2,}(?:[-][A-Z0-9]+)*\b", "CERT"),
    # MCP ID / SP коды
    (r"[A-Z0-9]{3,}(?:\s+(?:ID|SP|MCP|No|#)\s*[A-Z0-9]+)+", "CERT_CODE"),
    # Телефон: + или скобки или израильский 05X-XXXXXXX
    (r"(?:\+\d{1,3}[\s\-]?)?\(?\d{2,4}\)?[\s\-]\d{3}[\s\-]\d{4,}", "PHONE"),
    # Спецсимвольные технологии — отдельно т.к. \b не работает со спецсимволами
    (r"(?<![A-Za-z])(?:C\+\+|C#|\.NET|ASP\.NET)(?![A-Za-z0-9])", "TECH_SPECIAL"),
    # Конкретные технологии и продукты — точный список без жадного regex
    (
        r"\b(?:"
        r"Microsoft(?:\s+Windows)?|Windows(?:\s+Server)?|Office\s+365|SharePoint|Exchange|"
        r"Azure|Active\s+Directory|"
        r"Linux|Ubuntu|Debian|CentOS|Red\s*Hat|Fedora|"
        r"Cisco|Oracle|SAP|IBM|"
        r"SQL\s+Server|PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|"
        r"React(?:\.js)?|Angular|Vue(?:\.js)?|Node(?:\.js)?|Express(?:\.js)?|"
        r"Docker|Kubernetes|Terraform|Ansible|Jenkins|"
        r"Python|JavaScript|TypeScript|PHP|Ruby|Swift|Kotlin|Golang|Rust|"
        r"ASP\.NET|"
        r"AWS|GCP|VMware|Nginx|Apache|"
        r"GitHub|GitLab|Jira|Confluence|Slack|Figma|Photoshop|Illustrator"
        r")\b",
        "TECH"
    ),
    # Латинские слова 4+ символов — только если это техническая сущность:
    # CamelCase (TypeScript, GitHub, PostgreSQL), или содержит цифру (Python3, v2).
    # Обычные английские слова (Managed, Creative, Senior) НЕ токенизируются —
    # LLM должен видеть их чтобы иметь возможность улучшить текст.
    (r"[A-Za-z]+[0-9][A-Za-z0-9]*|[a-z]+[A-Z][A-Za-z0-9]*|[A-Z][a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}-\d+|\b[A-Z]{2,}(?:/[A-Z]{2,})+\b", "LATIN_WORD"),
]


def _protect_text(text, store):
    """
    Заменить все защищаемые подстроки в тексте на UUID-токены.
    Используем один проход — каждая позиция обрабатывается только один раз,
    исключая наложение паттернов друг на друга.
    """
    # Собираем все совпадения со всех паттернов
    matches = []
    for pattern, kind in _PROTECT_PATTERNS:
        for m in re.finditer(pattern, text):
            matches.append((m.start(), m.end(), m.group(0)))

    if not matches:
        return text

    # Сортируем по позиции, при конфликте берём самое длинное (более специфичное)
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))

    # Убираем перекрывающиеся совпадения — жадный алгоритм
    non_overlapping = []
    last_end = 0
    for start, end, val in matches:
        if start >= last_end:
            non_overlapping.append((start, end, val))
            last_end = end

    # Строим результат
    result = []
    last_end = 0
    for start, end, val in non_overlapping:
        result.append(text[last_end:start])
        tok = _make_token()
        store[tok] = val
        result.append(tok)
        last_end = end
    result.append(text[last_end:])

    return "".join(result)


def _restore_text(text, store):
    """Восстановить все токены обратно на оригинальные значения."""
    for tok, original in store.items():
        text = text.replace(tok, original)
    return text


# ---------------------------------------------------------------------------
# Применение улучшенного текста к DOCX
# ---------------------------------------------------------------------------

def _apply_improved_text_to_docx(original_bytes, improved_text, item_ids):
    """
    Клонировать оригинальный DOCX и заменить тексты.
    Восстановление по идентификаторам ###ITEM_001### — не по индексам.
    """
    import io, re
    from docx import Document

    doc = Document(io.BytesIO(original_bytes))
    orig_items = _extract_structured(doc)

    # Разбираем ответ AI по именованным идентификаторам
    id_to_text = {}
    parts = re.split(r"###ITEM_(\d+)###", improved_text)
    # parts: ['', '001', 'текст1', '002', 'текст2', ...]
    i = 1
    while i + 1 < len(parts):
        item_id = parts[i].zfill(3)
        text = parts[i + 1].strip()
        id_to_text[item_id] = text
        i += 2

    # Применяем — ищем каждый элемент по его ID
    for idx, item in enumerate(orig_items):
        item_id = item_ids[idx] if idx < len(item_ids) else None
        if item_id and item_id in id_to_text:
            new_text = id_to_text[item_id]
            _replace_para_text(item["para"], new_text)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Flask маршруты
# ---------------------------------------------------------------------------


def _extract_facts(text):
    """
    Извлечь из текста факты которые нельзя выдумывать:
    числа, проценты, года, слова с заглавной буквы (имена, компании).
    Возвращает множество строк-фактов.

    АРХИТЕКТУРНОЕ ПРАВИЛО: слово в самом начале текста или сразу после
    '. '/'! '/'? ' (начало нового предложения) НЕ считается фактом, даже
    если оно написано с заглавной буквы — это грамматическое требование
    английского языка (Sentence-initial capitalization), а не индикатор
    имени собственного/компании/технологии. Слова с заглавной буквы в
    середине предложения (Google, Microsoft, React) по-прежнему считаются
    фактами и ловятся.
    """
    facts = set()
    # Числа (включая проценты, суммы)
    for m in re.finditer(r"(?<!\d)\d+(?:[.,]\d+)?\s*(?:%|тыс|млн|k|K)?(?!\d)", text):
        facts.add(m.group(0).strip())

    # Позиции начала предложений: позиция 0 и позиции сразу после ". "/"! "/"? "
    sentence_start_positions = {0}
    for m in re.finditer(r"[.!?]\s+", text):
        sentence_start_positions.add(m.end())

    for m in re.finditer(r"\b[A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё]{2,}\b", text):
        if m.start() in sentence_start_positions:
            continue

        facts.add(m.group(0))
    return facts


_FABRICATED_CLAIM_RE = re.compile(
    r"\bresulting in\b|\bensuring\b|\bleveraging\b|\bfostering\b|"
    r"\bstreamlining\b|\ballowing\b|\bdriving\b|\benhancing\b|"
    r"\bmeet(?:ing)? user needs\b|\bexceed(?:ing)? expectations\b|"
    r"\bstrong network\b|\bdeep understanding\b",
    re.IGNORECASE,
)


def _validate_block(orig_text, new_text):
    """
    Проверить что новый текст не содержит фактов отсутствующих в оригинале,
    а также не содержит фабрикованных причинно-следственных утверждений
    (LLM любит дописывать "resulting in...", "ensuring..." и т.п. — это
    недоказанные claims, которых не было в оригинале).
    Возвращает (is_valid: bool, reason: str).
    """
    if not new_text or not orig_text:
        return True, ""
    orig_facts = _extract_facts(orig_text)
    new_facts  = _extract_facts(new_text)
    invented   = new_facts - orig_facts
    # Фильтруем: цифры 1-4 это вероятно пункты списка, не факты
    invented = {f for f in invented if not re.match(r"^\d$", f)}
    if invented:
        return False, f"Invented facts: {', '.join(sorted(invented)[:5])}"

    fabricated = set(_FABRICATED_CLAIM_RE.findall(new_text)) - set(_FABRICATED_CLAIM_RE.findall(orig_text))
    if fabricated:
        return False, f"Fabricated claim added: {', '.join(sorted(fabricated)[:3])}"

    return True, ""


# ---------------------------------------------------------------------------
# Классификация элементов — что замораживать, что улучшать, что защищать частично
# ---------------------------------------------------------------------------

# Заголовки секций — всегда заморозка
SECTION_HEADERS_SET = {
    "ניסיון תעסוקתי",  # ניסיון תעסוקתי
    "השכלה",         # השכלה
    "שפות",               # שפות
    "יכולת מקצועית",  # יכולת מקצועית
    "כתובת",         # כתובת
    "תאריך לידה",  # תאריך לידה
    "ת.ז",                           # ת.ז
    "עברית",         # עברית
    "רוסית -",       # רוסית -
    "אנגלית",   # אנגלית
    "רוסיט",         # русский заголовок если есть
    "Опыт работы", "Образование", "Навыки", "Языки", "Контакты",
    "Experience", "Education", "Skills", "Languages", "Contacts", "Summary",
    "Professional Summary", "Work Experience", "Career Highlights",
    "Professional Experience", "Employment History", "Contact",
    "Selected Publications", "Grants and Awards", "Academic Appointments",
    "Awards",
}

# Паттерны для классификации элементов
_RE_PHONE    = re.compile(r"(?:\+\d{1,3}[\s\-]?)?\(?\d{2,4}\)?[\s\-]\d{3}[\s\-]\d{4,}|\d{9,}")
_RE_EMAIL    = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_RE_DATE_RANGE = re.compile(r"(?<!\d)(?:\d{4}\s*[-–]\s*\d{4}|[-–]\s*\d{4}|\d{4}\s*[-–])(?!\d)")
_RE_DATE_BIRTH = re.compile(r"\d{2}[/.]\d{2}[/.]\d{4}")
_RE_ID_NUM   = re.compile(r"\d{7,}")
_RE_URL      = re.compile(r"https?://|www\.")
_RE_LANG_LINE = re.compile(  # строка описания языка: "דיבור רמה גבוהה..."
    r"דיבור|קריאה|כתיבה"
)
# Identity-строка вида "Title — Company, Location" или "Degree — University" —
# структурная строка (должность/степень/место), не должна переписываться,
# как и контактный блок. Обнаружено на реальных данных: такие строки
# систематически уходили в improve и портились при retry.
_RE_IDENTITY_DASH = re.compile(r"\s[—–-]\s")
# Короткий label-заголовок таблицы навыков вида "Marketing Tools:", "Design Tools:"
_RE_COLON_LABEL = re.compile(r":\s*$")

# Слова-маркеры которые указывают что текст — описание работы (нужно улучшать)
# Слова-маркеры описаний работы — Hebrew и English глаголы
_RE_IMPROVABLE = re.compile(
    r"אחראי|פיתוח|הטמעה|ניהל|סיפק|בנייה|עבדתי|תכנון|הקמתי|פתרתי|"
    r"למידה|התקנה|תמיכה|הכשרה|הדרכה|עבודה|ייעוץ|ביצוע|שיפור|הובלה|"
    r"managed|developed|implemented|designed|built|maintained|led|created|"
    r"improved|installed|configured|supported|trained|analyzed|optimized|"
    r"saved|achieved|increased|reduced|delivered|launched|drove|grew",
    re.IGNORECASE
)


def _classify_item(text, idx, total_items):
    """
    Простая детерминированная классификация:
    - freeze: section headers, строки только из данных (дата/ID/телефон), описания языков
    - improve: всё остальное (улучшаем с токенами на факты внутри)
    Нет protect-категории — это упрощает логику и уменьшает число замороженных блоков.
    """
    t = text.strip()

    # Section headers — заморозить
    if t in SECTION_HEADERS_SET:
        return "freeze"

    # Очень короткие строки — только данные, нечего улучшать.
    # Исключение: если строка содержит глагол-маркер достижения — это
    # короткое, но реальное достижение (например "Led 5 projects",
    # "Saved $50K") и должно идти на улучшение, а не замораживаться.
    if len(t) < 15 and not _RE_IMPROVABLE.search(t):
        return "freeze"

    # Описание языков (דיבור / קריאה / כתיבה)
    if _RE_LANG_LINE.search(t):
        return "freeze"

    # Identity-строка "Title — Company, Location" / "Degree — University" —
    # структурное поле, не должно переписываться (нет глагола-маркера)
    if _RE_IDENTITY_DASH.search(t) and not _RE_IMPROVABLE.search(t):
        return "freeze"

    # Короткий label заголовок таблицы навыков ("Marketing Tools:", "Skills:")
    if _RE_COLON_LABEL.search(t) and not _RE_IMPROVABLE.search(t):
        return "freeze"

    # Перечисление имён собственных через запятую (инструменты, языки,
    # технологии) без глагола-маркера — "HubSpot, Salesforce, Marketo",
    # "English (Native), German (Conversational)". Нечего улучшать в списке
    # названий — только риск фабрикации при попытке "переписать".
    if not _RE_IMPROVABLE.search(t):
        parts = [p.strip() for p in t.split(",") if p.strip()]
        if len(parts) >= 2:
            all_short = all(len(p.split()) <= 4 for p in parts)
            all_capitalized = all(p[0].isupper() for p in parts if p)
            if all_short and all_capitalized:
                return "freeze"

    # Контактный блок (email + телефон/URL в одной строке) — структурное поле,
    # как и раздел языков, не должно переписываться в prose.
    if _RE_EMAIL.search(t) and (_RE_PHONE.search(t) or _RE_URL.search(t)):
        return "freeze"

    # Строка состоит ТОЛЬКО из данных — нет слов для улучшения
    # Убираем все найденные токены и смотрим что осталось
    store_tmp = {}
    cleaned = _protect_text(t, store_tmp)
    # После защиты остался только текст без данных
    leftover = cleaned
    for tok in store_tmp:
        leftover = leftover.replace(tok, ' ')
    leftover_words = [w for w in leftover.split() if len(w) > 2 and w not in ('ו-', 'של', 'עם', 'את', 'על')]
    if len(leftover_words) == 0 and len(t) < 40 and not _RE_IMPROVABLE.search(t):
        return "freeze"

    # Всё остальное — улучшать
    return "improve"


# ---------------------------------------------------------------------------
# Quality Gate — проверка качества улучшения перед принятием
# ---------------------------------------------------------------------------

def _text_similarity(a, b):
    """
    Простое сходство двух строк на основе общих символьных биграмм.
    Возвращает float 0.0–1.0. Не требует внешних библиотек.
    """
    def bigrams(s):
        s = s.strip().lower()
        return [s[i:i+2] for i in range(len(s)-1)] if len(s) > 1 else [s]
    bg_a = bigrams(a)
    bg_b = set(bigrams(b))
    if not bg_a or not bg_b:
        return 1.0
    matches = sum(1 for bg in bg_a if bg in bg_b)
    return matches / max(len(bg_a), len(bg_b))


def _has_quality_improvement(orig, improved):
    """
    Проверить что улучшение реально качественное:
    - не просто добавлены/убраны пробелы
    - есть реальные изменения в словах
    Returns (is_quality: bool, reason: str)
    """
    # Нормализуем для сравнения
    orig_words = set(orig.strip().split())
    impr_words = set(improved.strip().split())
    # Новые слова которых не было
    new_words = impr_words - orig_words
    # Убранные слова
    removed_words = orig_words - impr_words
    # Хоть что-то изменилось на уровне слов
    has_word_change = bool(new_words or removed_words)
    # Длина изменилась более чем на 10%
    len_change = abs(len(improved) - len(orig)) / max(len(orig), 1)
    has_length_change = len_change > 0.10
    if has_word_change or has_length_change:
        return True, f"changed_words={len(new_words)+len(removed_words)} len_delta={len_change:.1%}"
    return False, "no_word_changes"


def _has_genuine_word_change(orig, improved):
    """
    True если среди изменённых слов есть хотя бы одно, отличающееся не
    только знаками препинания (защита от ложного срабатывания когда
    'word' и 'word,' считаются как new+removed word).
    """
    strip_punct = lambda w: w.strip(".,;:!?—-")
    orig_words = {strip_punct(w) for w in orig.strip().split()}
    impr_words = {strip_punct(w) for w in improved.strip().split()}
    new_words = impr_words - orig_words
    removed_words = orig_words - impr_words
    return bool(new_words or removed_words)


def _quality_gate(orig_text, improved_text, threshold=0.95):
    """
    Quality Gate: проверить нужна ли повторная попытка.
    Returns (accepted: bool, similarity: float, reason: str)

    Порядок проверки: сначала confirmed word-level change (реальные
    изменённые/добавленные/убранные слова) — если он подтверждён, это
    ПРИНИМАЕТСЯ независимо от высокого биграммного сходства (замена
    одного сильного глагола на синоним в короткой фразе даёт sim>95%
    просто из-за общей длины окружающего текста). Только при отсутствии
    подтверждённых словесных изменений применяется порог similarity.
    """
    sim = _text_similarity(orig_text, improved_text)
    quality_ok, quality_reason = _has_quality_improvement(orig_text, improved_text)
    if quality_ok and _has_genuine_word_change(orig_text, improved_text):
        return True, sim, f"accepted similarity={sim:.3f} {quality_reason}"
    if sim > threshold:
        return False, sim, f"similarity={sim:.3f} above threshold={threshold}"
    return False, sim, f"no_quality_improvement ({quality_reason})"

def _extract_retry_after_seconds(error_message, default=2.0, cap=12.0):
    """
    Извлечь время ожидания из сообщения об ошибке Groq вида
    "Please try again in 5.67s." Возвращает секунды, ограниченные [0, cap].
    Если не удалось распарсить — возвращает default.
    """
    m = re.search(r"try again in ([\d.]+)s", error_message or "")
    if not m:
        return default
    try:
        return min(float(m.group(1)), cap)
    except ValueError:
        return default


def _run_improve_pipeline(original_bytes, filename, resume_text_fallback, api_key):
    """
    Общий защищённый pipeline улучшения резюме:
    Protected Tokens -> LLM -> Fact Validation -> Quality Gate -> Retry.

    Параметры:
      original_bytes       — байты загруженного файла (или None)
      filename              — имя файла (для определения .docx) или None
      resume_text_fallback  — текст резюме, если файла нет (JSON-путь)
      api_key               — GROQ_API_KEY

    Возвращает dict:
      {"success": True, ...}  — при успехе, те же поля что раньше отдавал /api/admin/improve
      {"success": False, "error": str, "status": int} — при ошибке
    """
    resume_text = ""
    orig_items = []
    NL = chr(10)

    is_docx = bool(filename and filename.lower().endswith(".docx"))

    if original_bytes is not None:
        if is_docx:
            resume_text = _extract_full_text_from_docx(original_bytes)
        else:
            resume_text = original_bytes.decode("utf-8", errors="ignore")
    else:
        resume_text = (resume_text_fallback or "").strip()

    if not resume_text or len(resume_text) < 20:
        return {"success": False, "error": "Resume text too short", "status": 400}

    if not api_key:
        return {"success": False, "error": "GROQ_API_KEY not configured", "status": 500}

    import requests as req_lib

    detected_lang = _detect_language_simple(resume_text)

    if original_bytes is not None and is_docx:
        from docx import Document
        import io
        doc_tmp = Document(io.BytesIO(original_bytes))
        orig_items = _extract_structured(doc_tmp)
    else:
        orig_items = [{"text": l} for l in resume_text.split(NL) if l.strip()]

    # -----------------------------------------------------------
    # Шаг 1: Protected Tokens — защита ДО AI
    # -----------------------------------------------------------
    store = {}       # token -> original_value
    item_ids = []    # ID каждого элемента (001, 002, ...)
    ai_blocks = []   # блоки для AI с именованными идентификаторами
    strategy_map = {}  # item_id -> реально применённая strategy (freeze/improve/protect)

    n_items = len(orig_items)
    for i, item in enumerate(orig_items):
        item_id = str(i + 1).zfill(3)
        item_ids.append(item_id)
        text = item["text"]

        # Первые 2 элемента — имя и телефон/email — всегда заморозка
        if i <= 1:
            strategy = "freeze"
        else:
            strategy = _classify_item(text, i, n_items)

        strategy_map[item_id] = strategy

        if strategy == "freeze":
            # Заморозить целиком — AI не видит содержимое
            tok = _make_token()
            store[tok] = text
            ai_blocks.append(f"###ITEM_{item_id}###\n{tok}")

        elif strategy == "improve":
            # Улучшать — токены только на факты внутри текста
            if NL in text:
                lines = text.split(NL)
                protected_lines = [_protect_text(l, store) for l in lines]
                ai_blocks.append(f"###ITEM_{item_id}###\n" + NL.join(protected_lines))
            else:
                ai_blocks.append(f"###ITEM_{item_id}###\n" + _protect_text(text, store))

        else:  # protect
            # Данные с контекстом — весь текст через токены
            tok = _make_token()
            store[tok] = text
            ai_blocks.append(f"###ITEM_{item_id}###\n{tok}")

    n = len(ai_blocks)
    ai_input = "\n\n".join(ai_blocks)

    # -----------------------------------------------------------
    # Шаг 2: Запрос к AI
    # -----------------------------------------------------------
    system_prompt = (
        f"You are a professional resume editor.\n\n"
        f"RULES:\n"
        f"1. Write ONLY in {detected_lang}\n"
        f"2. Input has {n} blocks, each starting with ###ITEM_NNN###\n"
        f"3. Return ALL {n} blocks in the SAME order with the SAME ###ITEM_NNN### identifiers\n"
        f"4. Tokens like @@@A1B2C3D4E5F6@@@ are protected values — copy them EXACTLY as-is\n"
        f"5. Improve ONLY: job descriptions and skill descriptions — use stronger, more precise action verbs for what is already described. Do not add new clauses, outcomes, or explanations.\n"
        f"6. Keep unchanged: everything that is a token, section headers, dates, IDs\n"
        f"7. Multiline items: keep same number of lines, single newline between them\n"
        f"8. Do NOT merge blocks, do NOT split blocks, do NOT add extra ###ITEM### markers\n"
        f"9. NEVER invent or add anything not in the original: no new jobs, certifications, courses, achievements, responsibilities, skills, education, outcomes, results, or causal explanations (phrases like \"resulting in\", \"which improved\", \"leading to\", \"by leveraging\", \"ensuring\", \"driving\"). If a sentence has nothing to strengthen, return it unchanged rather than adding filler."
    )

    user_prompt = (
        f"Improve this resume. Return all {n} blocks with their ###ITEM_NNN### identifiers.\n\n"
        f"{ai_input}\n\n"
        f"OUTPUT ({n} blocks):"
    )

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.15,
        "max_tokens": 4000,
    }

    resp = req_lib.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload, timeout=90,
    )

    if resp.status_code == 429:
        payload["model"] = "llama-3.1-8b-instant"
        resp = req_lib.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=90,
        )

    if resp.status_code == 429:
        # Оба варианта модели упёрлись в rate limit — ждём столько,
        # сколько сама Groq API просит подождать (bounded, максимум 12с),
        # и делаем один финальный повтор, прежде чем сдаться.
        err_msg = resp.json().get("error", {}).get("message", "")
        wait_s = _extract_retry_after_seconds(err_msg)
        time.sleep(wait_s)
        resp = req_lib.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=90,
        )

    if resp.status_code != 200:
        err = resp.json().get("error", {}).get("message", "Groq API error")
        return {"success": False, "error": err, "status": 500}

    raw_response = resp.json()["choices"][0]["message"]["content"]
    tokens = resp.json().get("usage", {}).get("total_tokens", 0)

    # -----------------------------------------------------------
    # Шаг 3: Восстановление + Quality Gate + Retry + Отчёт
    # -----------------------------------------------------------
    def _parse_ai_response(raw):
        """Разобрать ответ AI по идентификаторам блоков."""
        result = {}
        parts = re.split(r"###ITEM_(\d+)###", raw)
        k = 1
        while k + 1 < len(parts):
            iid = parts[k].zfill(3)
            text = parts[k + 1].strip()
            text = re.sub(NL + r"{2,}", NL, text)
            result[iid] = text
            k += 2
        return result

    def _restore_and_validate(parsed, attempt_label):
        """
        Восстановить токены, применить Fact Validation,
        применить Quality Gate. Вернуть (id_to_text, block_reports).
        """
        id_to_text = {}
        block_reports = []
        for i, item in enumerate(orig_items):
            iid = item_ids[i]
            orig_text = item["text"]
            # Наследуем реально применённое решение первой попытки —
            # не пересчитываем через _classify_item заново, иначе
            # hard-frozen блоки (idx<=1) теряют freeze-статус.
            strategy = strategy_map.get(iid, "freeze") if iid in parsed else "freeze"

            if iid not in parsed or strategy == "freeze":
                id_to_text[iid] = orig_text
                block_reports.append({
                    "id": iid, "attempt": attempt_label,
                    "strategy": strategy,
                    "decision": "kept_original",
                    "reason": "frozen_or_missing",
                    "similarity": 1.0,
                })
                continue

            improved = _restore_text(parsed[iid], store)

            # Fact Validation
            fact_ok, fact_reason = _validate_block(orig_text, improved)
            if not fact_ok:
                id_to_text[iid] = orig_text
                block_reports.append({
                    "id": iid, "attempt": attempt_label,
                    "strategy": strategy,
                    "decision": "rejected_facts",
                    "reason": fact_reason,
                    "similarity": _text_similarity(orig_text, improved),
                })
                continue

            # Quality Gate
            qg_ok, sim, qg_reason = _quality_gate(orig_text, improved)
            id_to_text[iid] = improved if qg_ok else None  # None = нужен retry
            block_reports.append({
                "id": iid, "attempt": attempt_label,
                "strategy": strategy,
                "decision": "accepted" if qg_ok else "needs_retry",
                "reason": qg_reason,
                "similarity": sim,
            })

        return id_to_text, block_reports

    # --- Первая попытка ---
    parsed_1 = _parse_ai_response(raw_response)
    id_to_text_1, reports_1 = _restore_and_validate(parsed_1, "attempt_1")

    # Блоки которые нужно переделать (strategy=improve, quality gate не прошли)
    retry_ids = [r["id"] for r in reports_1 if r["decision"] == "needs_retry"]
    all_reports = reports_1

    tokens_total = tokens

    if retry_ids:
        # --- Усиленный промпт для повторной попытки ---
        retry_items_text = []
        for iid in retry_ids:
            idx_r = int(iid) - 1
            orig_t = orig_items[idx_r]["text"] if idx_r < len(orig_items) else ""
            if strategy_map.get(iid) == "freeze":
                # Наследуем freeze-решение первой попытки — не отправлять
                # hard-frozen блок в _protect_text(), иначе обычный текст
                # (например имя) уходит в LLM незащищённым.
                tok = _make_token()
                store[tok] = orig_t
                protected_t = tok
            else:
                protected_t = _protect_text(orig_t, store)
            retry_items_text.append(f"###ITEM_{iid}###\n{protected_t}")

        retry_input = "\n\n".join(retry_items_text)
        n_retry = len(retry_ids)

        retry_system = (
            f"You are a professional resume editor. These {n_retry} resume blocks were sent to you before "
            f"and returned nearly identical to the original — but that is only acceptable if there is "
            f"genuinely nothing to improve. Try again, but only change wording that can be genuinely "
            f"strengthened:\n\n"
            f"- Use a stronger, more precise action verb ONLY if a better one exists for what is already described\n"
            f"- Do NOT add new clauses, outcomes, or explanations of impact\n"
            f"- Do NOT add causal or result phrases (\"resulting in\", \"which improved\", \"leading to\", "
            f"\"by leveraging\", \"ensuring\", \"driving\", \"informing\")\n"
            f"- Write in {detected_lang} only\n"
            f"- Copy protected tokens @@@...@@@ EXACTLY as-is\n"
            f"- Return EXACTLY {n_retry} blocks with ###ITEM_NNN### identifiers\n"
            f"- NEVER invent new facts, numbers, companies or technologies\n"
            f"- If a block genuinely has nothing to improve (e.g. it is a name, title, header, or list of "
            f"items), return it completely unchanged — do not pad it to appear different."
        )
        retry_user = (
            f"Rewrite these {n_retry} blocks only where a genuine wording improvement is possible. "
            f"If a block has nothing meaningful to improve, return it unchanged. "
            f"Return with ###ITEM_NNN### identifiers.\n\n"
            f"{retry_input}\n\nOUTPUT ({n_retry} blocks):"
        )

        retry_payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": retry_system},
                {"role": "user", "content": retry_user},
            ],
            "temperature": 0.6,  # выше температура для более творческого ответа
            "max_tokens": 4000,
        }

        resp2 = req_lib.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=retry_payload, timeout=90,
        )

        if resp2.status_code == 429:
            retry_payload["model"] = "llama-3.1-8b-instant"
            resp2 = req_lib.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=retry_payload, timeout=90,
            )

        if resp2.status_code == 200:
            raw_response_2 = resp2.json()["choices"][0]["message"]["content"]
            tokens_total += resp2.json().get("usage", {}).get("total_tokens", 0)
            parsed_2 = _parse_ai_response(raw_response_2)
            # Один вызов — используем один и тот же результат и для отчёта,
            # и для merge, чтобы то, что проверялось, гарантированно совпадало
            # с тем, что попадает в итоговый текст.
            id_to_text_retry, reports_2 = _restore_and_validate(parsed_2, "attempt_2")

            for iid in retry_ids:
                if id_to_text_retry.get(iid) is not None:
                    id_to_text_1[iid] = id_to_text_retry[iid]
                else:
                    # Вторая попытка тоже не прошла — оставляем оригинал
                    idx_r = int(iid) - 1
                    id_to_text_1[iid] = orig_items[idx_r]["text"] if idx_r < len(orig_items) else ""

            all_reports += reports_2

    # --- Финальная сборка ---
    restored_list = []
    for i, item in enumerate(orig_items):
        iid = item_ids[i]
        val = id_to_text_1.get(iid)
        restored_list.append(val if val is not None else item["text"])

    improved_text_for_docx = (
        "###ITEM_" +
        "\n\n###ITEM_".join(
            f"{item_ids[i]}###\n{restored_list[i]}"
            for i in range(len(orig_items))
        )
    )
    display_text = "\n".join(restored_list)

    # --- Отчёт по блокам ---
    quality_report = {
        "total_blocks": len(orig_items),
        "retry_triggered": len(retry_ids),
        "retry_ids": retry_ids,
        "blocks": all_reports,
        "summary": {
            "accepted":        sum(1 for r in all_reports if r["decision"] == "accepted"),
            "kept_original":   sum(1 for r in all_reports if r["decision"] == "kept_original"),
            "rejected_facts":  sum(1 for r in all_reports if r["decision"] == "rejected_facts"),
            "needs_retry":     sum(1 for r in all_reports if r["decision"] == "needs_retry"),
            "avg_similarity":  round(
                sum(r["similarity"] for r in all_reports) / max(len(all_reports), 1), 3
            ),
        },
    }

    return {
        "success": True,
        "improved_resume": improved_text_for_docx,
        "display_text": display_text,
        "original_text": resume_text,
        "detected_language": detected_lang,
        "tokens_used": tokens_total,
        "has_original_docx": original_bytes is not None,
        "quality_report": quality_report,
        "item_ids": item_ids,
    }


def register_missing_routes(app, _extract_text_from_request, _get_current_user):
    from flask import request, jsonify, session, send_file
    import io


    @app.route("/api/admin/improve", methods=["POST"])
    def legacy_admin_improve():
        if "admin" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from flask import current_app

            api_key = current_app.config.get("GROQ_API_KEY")

            original_bytes = None
            filename = None
            resume_text_fallback = None

            file = request.files.get("file") or request.files.get("resume")
            if file:
                filename = file.filename
                original_bytes = file.read()
            else:
                data = request.get_json() or {}
                resume_text_fallback = data.get("resume_text", "").strip()

            result = _run_improve_pipeline(original_bytes, filename, resume_text_fallback, api_key)

            if not result.get("success"):
                return jsonify({"success": False, "error": result.get("error")}), result.get("status", 500)

            if original_bytes:
                import base64
                session["original_docx_b64"] = base64.b64encode(original_bytes).decode("ascii")
                session["item_ids"] = result["item_ids"]

            return jsonify(result)

        except Exception as e:
            import traceback
            app.logger.error("legacy_admin_improve failed: %s\n%s", e, traceback.format_exc())
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/admin/improve/docx", methods=["POST"])
    def legacy_admin_improve_docx():
        if "admin" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        try:
            from docx import Document
            from docx.shared import Pt
            import base64

            original_file = request.files.get("original_file")
            improved_text = request.form.get("improved_resume") or ""

            if not improved_text:
                data = request.get_json() or {}
                improved_text = data.get("improved_resume", "")

            if not improved_text:
                return jsonify({"success": False, "error": "No text provided"}), 400

            # item_ids: сначала из FormData (надёжно), потом из session (fallback)
            item_ids_raw = request.form.get("item_ids") or ""
            if item_ids_raw:
                import json as _json
                try:
                    item_ids = _json.loads(item_ids_raw)
                except Exception:
                    item_ids = []
            else:
                item_ids = session.get("item_ids", [])

            if original_file:
                buf = _apply_improved_text_to_docx(original_file.read(), improved_text, item_ids)
                return send_file(buf, as_attachment=True, download_name="improved_resume.docx",
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

            b64 = session.get("original_docx_b64")
            if b64:
                buf = _apply_improved_text_to_docx(base64.b64decode(b64), improved_text, item_ids)
                return send_file(buf, as_attachment=True, download_name="improved_resume.docx",
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

            # Fallback — простой текстовый DOCX
            doc = Document()
            doc.styles["Normal"].font.size = Pt(11)
            clean = re.sub(r"###ITEM_\d+###", "", improved_text)
            for line in clean.split(chr(10)):
                line = line.strip().lstrip("#").replace("**", "").replace("*", "").strip()
                doc.add_paragraph(line if line else "")
            buf = io.BytesIO()
            doc.save(buf)
            buf.seek(0)
            return send_file(buf, as_attachment=True, download_name="improved_resume.docx",
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
