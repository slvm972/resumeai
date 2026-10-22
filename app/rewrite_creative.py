# app/rewrite_creative.py
"""
Creative-режим ResumeAI: переписывание резюме целиком (ядро прототипа proto_rewrite.py v2.2).

Без Flask, без CLI, без файлового ввода-вывода. Только stdlib + requests (+ langid не нужен).
Тексты SYSTEM v2 и JUDGE v1, регулярки и таблицы перенесены из прототипа ДОСЛОВНО;
изменено только то, что требует сервис:
  - call_groq: один повтор при 429 (пауза из сообщения Groq, потолок 12 с);
  - rewrite_with_repair: бюджет времени (deadline), без чтения GROQ_API_KEY из окружения,
    pause по умолчанию 0;
  - creative_rewrite(): публичная обёртка, которая НИКОГДА не бросает исключений.

Публичная функция: creative_rewrite(source_text, api_key, ...) -> dict (см. её docstring).
"""
import datetime
import difflib
import json
import re
import time

PROMPT_VERSION = "v2.2"
JUDGE_PROMPT_VERSION = "v1"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """You are an expert resume writer and career coach. You rewrite resumes so that they read as confident, concrete and professional, WITHOUT inventing anything. The candidate's own facts are the only raw material.

TODAY: {today} (current year {year}). Use it only to decide tenses.

LANGUAGE: Write the whole resume in the language of the source, with correct, natural grammar and idiomatic wording. Never translate names, companies, job titles, school names, tool and technology names, URLs, e-mails or phone numbers. The two service sections DERIVED and CHANGELOG keep their English headings exactly as written in the output format below; their content may be in the language of the resume.

ABSOLUTE RULES (never break them):
1. No new facts. Never add numbers, percentages, amounts, team sizes, durations, tools, technologies, certificates, employers, titles, responsibilities, results or achievements that are not in the source.
2. Derived facts are allowed only when they follow arithmetically from the source (for example 2019-2024 at one employer means 5 years of experience). Every derived fact must be listed in the DERIVED section with its basis.
3. Keep the level of involvement. Supporting roles stay supporting: helped, assisted, participated, took part, was involved, worked with may become supported, assisted, contributed to, took part in (or the natural equivalent in the language), and NEVER resolved, solved, delivered, ensured, led, managed, owned, built, drove, achieved or an equivalent. Example: "Was involved in fixing printer problems" may become "Assisted in fixing printer problems", never "Resolved printer problems". "Responsible for X" may become a plain verb about X, but never a bigger claim.
4. Tense. A job whose period ended in a year earlier than the current year is written in the past tense throughout. A job marked as current (present, now, since a year without an end) uses the present tense. Never mix tenses inside one job. Every bullet is a sentence led by a verb.
5. No embellishment. Do not add purposes, manners, qualities or conditions that are not in the source, such as "while meeting deadlines", "in compliance with regulations", "efficiently", "accurately", "on time", "effectively", "to ensure quality", or their equivalents in any language.
6. No evaluative adjectives or self-praise about the candidate (experienced, skilled, reliable, responsible, motivated, professional, dynamic, results-oriented, and their equivalents in any language) unless the same word is in the source. Describe the role and the scope instead.
7. Prefer plain, precise verbs. Avoid bureaucratic filler verbs ("carried out", "performed", "осуществлял", "обеспечивал") unless the source uses them.

WHAT YOU ARE ENCOURAGED TO DO (this is where the brightness comes from):
- Merge related duties into stronger, tighter statements; reorder bullets so that the most significant come first; split overloaded lines.
- Replace weak openings ("responsible for", "was involved in", "worked on") with precise verbs that keep the same level of involvement.
- Name the function behind the tasks using words from the source (for example "client communication", "document workflow") so that the scope becomes clear.
- Group the skills by category, using only the listed skills.
- Write a short summary (2-3 sentences) built only from the source: start with the job title from the source, then the years of experience when derivable, the main areas of work and the key listed skills.
- Stay concise: the rewrite must not be much longer than the source.

OUTPUT FORMAT (plain text, no markdown, no extra commentary):
<the header lines of the source copied exactly: the name and the contact lines, unchanged>
<a short heading for the summary, in the language of the resume (the natural term for "Summary" or "Profile")>
<the summary, 2-3 sentences>
<the experience heading exactly as in the source>
<for each job: the line with title, company and dates exactly as in the source, then 2-5 bullets, each starting with "- ">
<every other section of the source with its own heading exactly as in the source, in the same order; the skills section is grouped by category>
DERIVED
- <derived fact> = <its basis in the source>
CHANGELOG
- <one line per significant change: what was merged or reworded and why>"""

JUDGE_SYSTEM = """You are a strict fact-checker for resume rewrites. You receive the SOURCE resume and a REWRITE. Check every sentence of the REWRITE, except the DERIVED and CHANGELOG sections, against the SOURCE. Report one issue for each of these types:
- NEW_FACT: a number, name, tool, duration, employer, title, result or responsibility that is not in the source (arithmetic facts listed in DERIVED with a correct basis are fine).
- OVERSTATED_ROLE: the candidate's involvement is raised (helped, assisted, participated, took part, was involved becomes resolved, led, managed, ensured, built, delivered, or similar).
- EMBELLISHMENT: an added purpose, manner, quality or condition that is not in the source (for example "while meeting deadlines", "efficiently").
- TENSE_ERROR: wrong tense for the period (a finished job must be in the past tense, the current job in the present tense).
- LANGUAGE_ERROR: a grammar, agreement, case or gender mistake, or an unnatural phrase.
- FLUFF: evaluative adjectives or self-praise that are not in the source.
Answer with ONE JSON object and nothing else: {"verdict":"pass" or "fail","issues":[{"type":"...","quote":"exact words from the REWRITE","reason":"short reason"}]}. If there are no issues, answer {"verdict":"pass","issues":[]}."""

JUDGE_USER_TEMPLATE = "SOURCE:\n{source}\n\nREWRITE:\n{rewrite}\n\nCheck the rewrite."
JUDGE_ISSUE_TYPES = ("NEW_FACT", "OVERSTATED_ROLE", "EMBELLISHMENT",
                     "TENSE_ERROR", "LANGUAGE_ERROR", "FLUFF")

# v2.2: LOST_ITEM выдаётся только кодом (det_issues), судья его не возвращает
ALL_ISSUE_TYPES = JUDGE_ISSUE_TYPES + ("LOST_ITEM",)

USER_TEMPLATE = "RESUME:\n{resume}\n\nRewrite it following the rules above."

def build_messages(resume_text, today=None):
    """system + user. {today} (YYYY-MM-DD) и {year} подставляются через str.replace;
    текст резюме тоже подставляется через replace (в нём могут быть фигурные скобки)."""
    if today is None:
        today = datetime.date.today()
    today_s = today if isinstance(today, str) else today.isoformat()
    system = SYSTEM_PROMPT.replace("{today}", today_s).replace("{year}", today_s[:4])
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": USER_TEMPLATE.replace("{resume}", resume_text)},
    ]


def strip_reasoning(text):
    """
    Убрать блоки <think>...</think> и текст рассуждений "думающих" моделей:
    - закрытые блоки <think>...</think> удаляются целиком;
    - есть только закрывающий </think> (начало срезано) -> берём текст после
      последнего </think>;
    - незакрытый <think> -> отбрасывается всё, начиная с него (рассуждение
      оборвалось по лимиту токенов).
    """
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    if re.search(r"</think>", text, re.I):
        text = re.split(r"</think>", text, flags=re.I)[-1]
    m = re.search(r"<think>", text, re.I)
    if m:
        text = text[:m.start()]
    return text.strip()


def parse_retry_after(message, default=2.0, cap=20.0):
    """Разобрать 'try again in X.XXs' из сообщения об ошибке (с ограничением cap)."""
    m = re.search(r"try again in ([\d.]+)\s*s", message or "", re.I)
    if not m:
        return default
    try:
        return min(float(m.group(1)), cap)
    except ValueError:
        return default


def _reasoning_params(model):
    name = model or ""
    if name.startswith("qwen/qwen3"):
        return {"reasoning_format": "hidden"}
    if name.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    return {}


def _error_message(resp):
    try:
        return resp.json().get("error", {}).get("message", "") or resp.text[:300]
    except Exception:
        return (resp.text or "")[:300]


def call_groq(api_key, model, messages, temperature=0.4, max_tokens=4000,
              post=None, sleep=time.sleep, timeout=120, max_429_retries=1, retry_cap=12.0):
    """
    POST chat/completions. 400 из-за неподдерживаемого параметра рассуждений ->
    повтор без него. 429 -> один раз ждём "try again in X.XXs" (потолок retry_cap = 12 с,
    как в missing_routes4._extract_retry_after_seconds) и повторяем; второй 429 -> RuntimeError.
    Возвращает dict: text, usage, seconds, dropped_params, retries_429.
    Ключ нигде не печатается.
    """
    if post is None:
        import requests
        post = requests.post

    extras = _reasoning_params(model)
    dropped = []
    retries_429 = 0
    t0 = time.time()

    while True:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        payload.update(extras)
        resp = post(
            GROQ_URL,
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        status = resp.status_code

        if status == 400 and extras:
            dropped.extend(sorted(extras))
            extras = {}
            continue

        if status == 429 and retries_429 < max_429_retries:
            retries_429 += 1
            sleep(parse_retry_after(_error_message(resp), cap=retry_cap))
            continue

        if status != 200:
            raise RuntimeError("Groq API %s: %s" % (status, _error_message(resp)))

        data = resp.json()
        raw = data["choices"][0]["message"]["content"] or ""
        return {
            "text": strip_reasoning(raw),
            "usage": data.get("usage", {}) or {},
            "seconds": round(time.time() - t0, 2),
            "dropped_params": dropped,
            "retries_429": retries_429,
        }

# ---------------------------------------------------------------------------
# Автоматическая проверка результата (универсальная для любого языка)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(
    r"https?://[^\s)\]]+|www\.[^\s)\]]+|(?:linkedin\.com|github\.com|gitlab\.com)/[^\s)\]]+",
    re.I)
_PHONE_RES = [
    re.compile(r"\+\d[\d\s\-().]{7,}\d"),                                       # +7 912 345 6789
    re.compile(r"(?:\+\d{1,3}[\s\-]?)?\(?\d{2,4}\)?[\s\-]\d{3}[\s\-]\d{4,}"),   # (415) 555-0192
    re.compile(r"(?<!\d)0\d{1,2}[\s\-]?\d{7}(?!\d)"),                           # 053-3341679
    re.compile(r"(?<!\d)\d{9,}(?!\d)"),                                         # 9+ цифр подряд
]
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-\u2022*\u2013\u2014]|\d+[.)])\s+")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*%?")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'+#\-]*")

_DERIVED_ALIASES = {
    "derived", "derived facts", "производные факты", "производные данные",
    "вычисленные данные", "похідні факти", "похідні дані",
}
_CHANGELOG_ALIASES = {
    "changelog", "change log", "журнал изменений", "список изменений",
    "изменения", "журнал змін", "список змін", "зміни", "changes",
}
_EXPERIENCE_ALIASES = {
    "experience", "work experience", "professional experience", "employment history",
    "опыт работы", "опыт", "досвід роботи", "досвід", "ניסיון תעסוקתי", "ניסיון",
}
_OTHER_SECTION_ALIASES = {
    "professional summary", "summary", "education", "skills", "languages", "contact",
    "contacts", "certifications", "projects", "profile", "awards", "publications",
    "образование", "навыки", "языки", "контакты", "профиль", "о себе", "сертификаты",
    "освіта", "навички", "мови", "контакти", "השכלה", "שפות", "כישורים",
}
_HEADING_WORDS = {
    "professional", "summary", "experience", "education", "skills", "languages",
    "certifications", "projects", "contact", "contacts", "profile", "work",
    "history", "employment", "objective", "awards", "publications", "references",
    "additional", "information", "courses", "interests", "derived", "changelog",
    "and", "of",
}
_FORBIDDEN = [
    "resulting in", "ensuring", "leveraging", "fostering", "drove", "spearheaded",
    "proven track record", "highly motivated", "passionate", "dynamic",
    "обеспечив", "благодаря чему", "что позволило", "результатом стало",
]


def _norm_heading(line):
    s = line.strip()
    s = re.sub(r"^[\s*#_`>\-\u2013\u2014]+", "", s)
    s = re.sub(r"[\s*#_`:\-\u2013\u2014]+$", "", s)
    return s.strip().lower()


def split_sections(output):
    """(main, derived, changelog): основной текст, секция DERIVED, секция CHANGELOG."""
    buckets = {"main": [], "derived": [], "changelog": []}
    state = "main"
    for line in output.split("\n"):
        h = _norm_heading(line)
        if h in _DERIVED_ALIASES:
            state = "derived"
            continue
        if h in _CHANGELOG_ALIASES:
            state = "changelog"
            continue
        buckets[state].append(line)
    return ("\n".join(buckets["main"]).strip(),
            "\n".join(buckets["derived"]).strip(),
            "\n".join(buckets["changelog"]).strip())


def _strip_contacts(text):
    text = _EMAIL_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    for rx in _PHONE_RES:
        text = rx.sub(" ", text)
    return text


def extract_contacts(text):
    """Нормализованные контакты: e-mail (нижний регистр), URL, телефоны (только цифры)."""
    emails = {m.group(0).lower() for m in _EMAIL_RE.finditer(text)}
    urls = {m.group(0).lower().rstrip(".,;:") for m in _URL_RE.finditer(text)}
    phones = set()
    for rx in _PHONE_RES:
        for m in rx.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) >= 7:
                phones.add("tel:" + digits)
    return emails | urls | phones


def _normalize_number(tok):
    pct = tok.endswith("%")
    t = tok.rstrip("%")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", t):
        t = re.sub(r"[.,]", "", t)           # разделитель тысяч
    elif "," in t:
        t = t.replace(",", ".")              # десятичная запятая
    return t + ("%" if pct else "")


def _numbers(text):
    """Нормализованные числовые токены без контактов и маркеров списков."""
    cleaned = []
    for line in text.split("\n"):
        cleaned.append(_LIST_MARKER_RE.sub("", line))
    body = _strip_contacts("\n".join(cleaned))
    return {_normalize_number(m.group(0)) for m in _NUM_RE.finditer(body)}


def _is_heading_line(s):
    words = re.findall(r"[A-Za-z]+", s)
    if words and len(s.split()) <= 4 and all(w.lower() in _HEADING_WORDS for w in words):
        return True
    return s.isupper()


def _clean_word(w):
    return w.strip("'-")


def _script_mode(source):
    letters = [c for c in _strip_contacts(source) if c.isalpha()]
    if not letters:
        return "latin"
    latin = sum(1 for c in letters if "a" <= c.lower() <= "z")
    return "non_latin" if (len(letters) - latin) / len(letters) > 0.5 else "latin"


def _new_latin_tokens(source, main):
    src = {_clean_word(w).lower() for w in _WORD_RE.findall(_strip_contacts(source))}
    found = []
    for line in main.split("\n"):
        s = _LIST_MARKER_RE.sub("", line).strip()
        if not s or _is_heading_line(s):
            continue
        for w in _WORD_RE.findall(_strip_contacts(s)):
            w = _clean_word(w)
            if w and w.lower() not in src and w not in found:
                found.append(w)
    return found


def _is_sentence_start(s, pos):
    prefix = s[:pos].rstrip()
    return (not prefix) or prefix[-1] in ".!?"


def _new_capitalized(source, main):
    src = {_clean_word(w).lower() for w in _WORD_RE.findall(_strip_contacts(source))}
    found = []
    for line in main.split("\n"):
        s = _LIST_MARKER_RE.sub("", line).strip()
        if not s or _is_heading_line(s):
            continue
        s = _strip_contacts(s)
        for m in _WORD_RE.finditer(s):
            w = _clean_word(m.group(0))
            if not w or not w[0].isupper():
                continue
            if _is_sentence_start(s, m.start()):
                continue
            if w.lower() in src or w in found:
                continue
            found.append(w)
    return found


def _forbidden_counts(source, main):
    counts = {}
    for phrase in _FORBIDDEN:
        if re.search(r"[A-Za-z]", phrase):
            rx = re.compile(r"\b" + re.escape(phrase) + r"\b", re.I)
        else:
            rx = re.compile(re.escape(phrase), re.I)
        if rx.search(source):
            continue                         # в исходнике фраза была - не считаем
        n = len(rx.findall(main))
        if n:
            counts[phrase] = n
    return counts


def _experience_bullets(main):
    """Число строк '- ' в секции опыта; None, если заголовок секции не найден."""
    in_exp = False
    found = False
    count = 0
    for line in main.split("\n"):
        h = _norm_heading(line)
        if h in _EXPERIENCE_ALIASES:
            in_exp, found = True, True
            continue
        if in_exp and (h in _OTHER_SECTION_ALIASES
                       or (line.strip().isupper() and len(line.split()) <= 4
                           and not line.lstrip().startswith("-"))):
            in_exp = False
        if in_exp and line.lstrip().startswith("- "):
            count += 1
    return count if found else None


# ---------------------------------------------------------------------------
# Судья (проверка фактов, ролей, времён, языка) - отдельный запрос
# ---------------------------------------------------------------------------

def build_judge_messages(source, rewrite):
    """system + user для судьи (подстановка через replace: в тексте бывают { })."""
    user = JUDGE_USER_TEMPLATE.replace("{source}", source).replace("{rewrite}", rewrite)
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]


def _first_json_object(text):
    """Первый сбалансированный {...}, который разбирается как JSON (с учётом строк)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def parse_judge(text):
    """
    Разбор ответа судьи -> {"verdict": "pass"|"fail", "issues": [...]}.
    Срезает <think>-блоки и ```json-ограждения, берёт первый сбалансированный
    {...}. При неудаче: {"verdict": "error", "issues": [], "raw": первые 300 символов}.
    """
    cleaned = strip_reasoning(text or "")
    cleaned = re.sub(r"```(?:json)?", "", cleaned, flags=re.I)
    obj = _first_json_object(cleaned)
    verdict = str(obj.get("verdict", "")).strip().lower() if isinstance(obj, dict) else ""
    if verdict not in ("pass", "fail"):
        return {"verdict": "error", "issues": [], "raw": (text or "")[:300]}
    issues = []
    raw_issues = obj.get("issues", [])
    for it in raw_issues if isinstance(raw_issues, list) else []:
        if isinstance(it, dict):
            issues.append({
                "type": str(it.get("type", "")).strip().upper(),
                "quote": str(it.get("quote", "")),
                "reason": str(it.get("reason", "")),
            })
    return {"verdict": verdict, "issues": issues}


def run_judge(api_key, judge_model, source, rewrite, call=None):
    """Запрос судье через call_groq (temperature 0, max_tokens 3000)."""
    call = call or call_groq
    result = call(api_key, judge_model, build_judge_messages(source, rewrite),
                  temperature=0, max_tokens=3000)
    judged = parse_judge(result.get("text", ""))
    judged.update({
        "model": judge_model,
        "prompt_version": JUDGE_PROMPT_VERSION,
        "seconds": result.get("seconds"),
        "tokens": (result.get("usage", {}) or {}).get("total_tokens"),
    })
    return judged


def issue_counts(judge):
    counts = {t: 0 for t in ALL_ISSUE_TYPES}
    for it in (judge or {}).get("issues", []):
        if it.get("type") in counts:
            counts[it["type"]] += 1
    return counts


_ISSUE_LETTERS = (("NEW_FACT", "N"), ("OVERSTATED_ROLE", "O"), ("EMBELLISHMENT", "E"),
                  ("TENSE_ERROR", "T"), ("LANGUAGE_ERROR", "L"), ("FLUFF", "F"),
                  ("LOST_ITEM", "I"))


def _issues_cell(judge):
    c = issue_counts(judge)
    return "/".join(str(c[t]) for t, _ in _ISSUE_LETTERS)


def _issues_text(judge):
    c = issue_counts(judge)
    return " ".join("%s:%d" % (letter, c[t]) for t, letter in _ISSUE_LETTERS)


def _squash(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _header_preserved(source, output):
    """Строки шапки источника (_header_lines) присутствуют в результате (пробелы нормализуются)."""
    required = _header_lines(source)
    if not required:
        return True
    out = _squash(output)
    return all(_squash(l) in out for l in required)


def check_output(source, output):
    """
    Автоматическая проверка результата переработки относительно исходника.
    Секции DERIVED и CHANGELOG из проверки основного текста исключаются.
    """
    main, derived, _changelog = split_sections(output)

    # а) числа
    src_nums = _numbers(source)
    main_nums = _numbers(main)
    derived_nums = _numbers(derived)
    new_main = main_nums - src_nums
    derived_numbers = sorted(new_main & derived_nums)
    new_numbers = sorted(new_main - derived_nums)

    # б) контакты
    src_contacts = extract_contacts(source)
    out_contacts = extract_contacts(main)
    lost_contacts = sorted(src_contacts - out_contacts)
    new_contacts = sorted(out_contacts - src_contacts)

    result = {
        "NEW_NUMBERS": new_numbers,
        "DERIVED_NUMBERS": derived_numbers,
        "LOST_CONTACTS": lost_contacts,
        "NEW_CONTACTS": new_contacts,
    }

    # в) латиница / заглавные
    mode = _script_mode(source)
    result["SOURCE_SCRIPT"] = mode
    if mode == "non_latin":
        result["NEW_LATIN_TOKENS"] = _new_latin_tokens(source, main)
    else:
        result["NEW_CAPITALIZED"] = _new_capitalized(source, main)

    # г) запрещённые фразы
    forbidden = _forbidden_counts(source, main)
    result["FORBIDDEN_PHRASES"] = forbidden
    result["FORBIDDEN_TOTAL"] = sum(forbidden.values())

    # д) метрики
    result["LENGTH_RATIO"] = round(len(main) / max(len(source), 1), 3)
    result["EXPERIENCE_BULLETS"] = _experience_bullets(main)
    result["HEADER_PRESERVED"] = _header_preserved(source, output)
    return result


# ---------------------------------------------------------------------------
# Файлы результата и summary
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# v2.1: детерминированная постобработка и жёсткие проверки в коде
# ---------------------------------------------------------------------------

_LOOKALIKE_HYPHENS = ("\u2010", "\u2011")      # hyphen, non-breaking hyphen
_BULLET_RE = re.compile(r"^(\s*)(?:\u2022|\*|\u2013)\s+")

SUMMARY_HEADINGS = {
    "en": "Summary", "ru": "Профиль", "uk": "Профіль", "de": "Profil",
    "fr": "Profil", "es": "Perfil", "it": "Profilo", "pt": "Perfil",
    "pl": "Profil", "he": "סיכום מקצועי", "ar": "الملخص المهني",
}


def detect_lang(text):
    """
    Язык резюме. Сначала по алфавиту: иврит -> he, арабский -> ar, китайские
    иероглифы -> zh, кириллица -> uk (есть і ї є ґ) или ru. Для латиницы
    langid (необязательная зависимость; нет - считаем en).
    """
    sample = (text or "")[:3000]
    counts = {"he": 0, "ar": 0, "zh": 0, "cyr": 0, "lat": 0}
    ukrainian = False
    for c in sample:
        if not c.isalpha():
            continue
        o = ord(c)
        if 0x0590 <= o <= 0x05FF:
            counts["he"] += 1
        elif 0x0600 <= o <= 0x06FF:
            counts["ar"] += 1
        elif 0x4E00 <= o <= 0x9FFF:
            counts["zh"] += 1
        elif 0x0400 <= o <= 0x04FF:
            counts["cyr"] += 1
            if c in "іїєґІЇЄҐ":
                ukrainian = True
        elif "a" <= c.lower() <= "z" or 0x00C0 <= o <= 0x024F:
            counts["lat"] += 1
    if not any(counts.values()):
        return "en"
    top = max(counts, key=counts.get)
    if top in ("he", "ar", "zh"):
        return top
    if top == "cyr":
        return "uk" if ukrainian else "ru"
    try:
        import langid
        return langid.classify(sample)[0]
    except Exception:
        return "en"


def _header_lines(source):
    """Строки шапки источника: первая непустая и строки с e-mail/телефоном/URL
    среди первых 5 непустых (их же проверяет HEADER_PRESERVED)."""
    lines = [l for l in (source or "").split("\n") if l.strip()][:5]
    if not lines:
        return []
    return [lines[0]] + [l for l in lines[1:] if extract_contacts(l)]


def _fix_summary_heading(source, text, lang=None):
    """Заменить заголовок вступления (первая непустая строка после шапки) на
    стандартный для языка резюме. Нет языка в таблице / нет заголовка - без изменений."""
    heading = SUMMARY_HEADINGS.get(lang or detect_lang(source))
    required = [_squash(l) for l in _header_lines(source)]
    if not heading or not required:
        return text
    lines = text.split("\n")
    last = -1
    for req in required:
        for i, l in enumerate(lines):
            sq = _squash(l)
            if sq == req or (req and req in sq):
                last = max(last, i)
                break
    if last < 0:
        return text
    for j in range(last + 1, len(lines)):
        cand = lines[j].strip()
        if not cand:
            continue
        # защита: заменяем только строку, похожую на заголовок (короткая, не
        # предложение и не пункт списка), чтобы не затереть сам текст вступления
        if len(cand.split()) <= 4 and not cand.endswith((".", "!", "?")) and not cand.startswith("- "):
            lines[j] = heading
        break
    return "\n".join(lines)


def postprocess(source, output, lang=None):
    """
    Чистая функция (без сети): U+2010/U+2011 -> "-" (только если символа нет в
    source, каждый символ отдельно); хвостовые пробелы убираются; 3+ пустых
    строк подряд -> одна; маркеры "• ", "* ", "– " -> "- " (строки шапки не
    трогаем); заголовок вступления -> стандартный по языку.
    """
    text = output or ""
    for ch in _LOOKALIKE_HYPHENS:
        if ch in text and ch not in (source or ""):
            text = text.replace(ch, "-")
    header = {_squash(l) for l in _header_lines(source)}
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        if _squash(line) not in header:
            line = _BULLET_RE.sub(r"\1- ", line)
        lines.append(line)
    text = re.sub(r"\n{4,}", "\n\n", "\n".join(lines)).strip("\n")
    return _fix_summary_heading(source, text, lang)


_FLUFF_EN = [
    (r"\bskilled in\b", r"\bskilled\b"),
    (r"\bexperienced\b", None), (r"\bproficient\b", None), (r"\bexpert\b", None),
    (r"\bresults-oriented\b", None), (r"\bhighly motivated\b", None),
    (r"\bpassionate\b", None), (r"\bdynamic\b", None), (r"\breliable\b", None),
    (r"\bhard-working\b", None), (r"\bdetail-oriented\b", None),
]
_FLUFF_CYR = [
    r"\bопытн\w*", r"\bквалифицирован\w*", r"\bответственн(?!ост)\w*",
    r"\bвідповідальн(?!іст)\w*", r"\bнадёжн\w*", r"\bнадежн\w*", r"\bнадійн\w*",
    r"\bцелеустремл[её]нн\w*", r"\bцілеспрямован\w*",
    r"\bхорошее знание", r"\bдобре знання",
]
_OWN_EN = ("managed", "managing", "led", "leading", "owned", "resolved", "resolving",
           "delivered", "ensured", "ensuring", "oversaw", "directed", "spearheaded", "controlled")
_OWN_CYR_ROOTS = ("управля", "руковод", "контролир", "обеспечива", "оформля", "осуществля",
                  "ведущ", "керува", "контролю", "забезпечу", "оформл")
_OWN_RE = re.compile(r"\b(?:%s)\b|\b(?:%s)\w*" % ("|".join(_OWN_EN), "|".join(_OWN_CYR_ROOTS)),
                     re.I)


def _snippet(text, start, end, before=18, after=28):
    """Найденное слово с короткой фразой вокруг; границы сдвигаются до границ слов."""
    a = max(0, start - before)
    b = min(len(text), end + after)
    while 0 < a < start and not text[a - 1].isspace():
        a += 1
    while end < b < len(text) and not text[b].isspace():
        b -= 1
    return text[a:b].replace("\n", " ").strip()


# ---- v2.2: пункты раздела навыков -----------------------------------------

_SKILLS_HEADINGS = {
    "skills", "key skills", "technical skills", "core competencies",
    "навыки", "ключевые навыки", "навички", "ключові навички", "כישורים", "المهارات",
}
_SECTION_END_HEADINGS = (_OTHER_SECTION_ALIASES | _EXPERIENCE_ALIASES | _SKILLS_HEADINGS
                         | _DERIVED_ALIASES | _CHANGELOG_ALIASES
                         | {"work history", "certificates", "technical skills"})
_SKILL_SPLIT_RE = re.compile(r"[,;\u2022\u060c\u061b\uff0c\uff1b\u3001]")
LOST_ITEM_REASON = "skill listed in the source is missing in the output"


def _norm_item(text):
    return " ".join((text or "").lower().split())


def _split_skill_line(line):
    """Пункты одной строки раздела навыков (маркер, запятые, ';', метка с двоеточием)."""
    line = _LIST_MARKER_RE.sub("", line, count=1)
    items = []
    for piece in _SKILL_SPLIT_RE.split(line):
        piece = _LIST_MARKER_RE.sub("", piece.strip(), count=1)
        if ":" in piece:
            piece = piece.split(":", 1)[1]          # метка до двоеточия отбрасывается
        piece = piece.strip(" \t.")
        if piece:
            items.append(piece)
    return items


def _iter_skill_lines(text):
    """
    Строки текста с признаком "это пункт раздела навыков". Раздел: строка-заголовок
    (Skills, Навыки, ...) и текст ниже до пустой строки или до следующего заголовка раздела
    (пустые строки сразу после заголовка пропускаются). Заголовки в пункты не входят.
    """
    in_section = started = False
    for line in (text or "").split("\n"):
        h = _norm_heading(line)
        if h in _SKILLS_HEADINGS:
            in_section, started = True, False
            yield line, False
            continue
        if not in_section:
            yield line, False
            continue
        if h in _SECTION_END_HEADINGS:
            in_section = False
            yield line, False
            continue
        if not line.strip():
            if started:
                in_section = False
            yield line, False
            continue
        started = True
        yield line, True


def skill_items(source):
    """Пункты раздела навыков (см. _iter_skill_lines). Нет раздела - пустой список."""
    items, seen = [], set()
    for line, is_item in _iter_skill_lines(source):
        if not is_item:
            continue
        for it in _split_skill_line(line):
            key = _norm_item(it)
            if key and key not in seen:
                seen.add(key)
                items.append(it)
    return items


def _item_kept(item, main_norm):
    """Пункт сохранён: подстрока основного текста; для однословного от 6 букв - ещё и
    префикс длины max(5, len-4) (падежи и формы: ответственность / ответственный)."""
    n = _norm_item(item)
    if n in main_norm:
        return True
    if " " not in n and len(n) >= 6:
        return n[:max(5, len(n) - 4)] in main_norm
    return False


def lost_item_issues(source, main):
    items = skill_items(source)
    if not items:
        return []
    main_norm = _norm_item(main)
    return [{"type": "LOST_ITEM", "quote": it, "reason": LOST_ITEM_REASON}
            for it in items if not _item_kept(it, main_norm)]


# v2.3: существительные, однокоренные с глаголами владения (руководитель, керівник, director),
# глаголами не являются. Перед проверкой OVERSTATED_ROLE они затираются пробелами той же длины
# (позиции для _snippet сохраняются). "directed" не затрагивается: EN - по границам слов.
_ROLE_NOUN_RE = re.compile(
    r"\b(?:руководител|руководств|руководящ|керівник|керівництв)\w*"
    r"|\b(?:directors?|directorship|controllers?)\b",
    re.I)


def _strip_role_nouns(text):
    return _ROLE_NOUN_RE.sub(lambda m: " " * len(m.group(0)), text or "")


def det_issues(source, output):
    """
    Жёсткие проверки в коде, замечания в формате судьи ({"type","quote","reason"}),
    только по основному тексту (без DERIVED и CHANGELOG).
      FLUFF           оценочные слова/обороты, которых нет в source
      OVERSTATED_ROLE глаголы владения (managed, руковод-, осуществля-, ...),
                      проверка выполняется ТОЛЬКО если в source их нет совсем
      LOST_ITEM       пункт раздела навыков source, которого нет в результате (v2.2)
    """
    main, _derived, _changelog = split_sections(output or "")
    src = source or ""
    issues = []

    for out_rx, src_rx in _FLUFF_EN + [(p, None) for p in _FLUFF_CYR]:
        src_rx = src_rx or out_rx
        if re.search(src_rx, src, re.I):
            continue
        for m in re.finditer(out_rx, main, re.I):
            issues.append({"type": "FLUFF", "quote": _snippet(main, m.start(), m.end()),
                           "reason": "Evaluative wording '%s' is not in the source" % m.group(0)})
            break

    if not _OWN_RE.search(_strip_role_nouns(src)):
        seen = set()
        for m in _OWN_RE.finditer(_strip_role_nouns(main)):
            word = m.group(0).lower()
            if word in seen:
                continue
            seen.add(word)
            issues.append({"type": "OVERSTATED_ROLE", "quote": _snippet(main, m.start(), m.end()),
                           "reason": "Ownership verb '%s' is not in the source; involvement may be overstated" % m.group(0)})

    issues.extend(lost_item_issues(src, main))
    return issues


def _merge_issues(*lists):
    """Объединить замечания, убрав дубликаты по quote."""
    merged, seen = [], set()
    for lst in lists:
        for it in lst:
            key = _squash(it.get("quote", "")).lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    return merged


REPAIR_INSTRUCTION = (
    "Fix ONLY the following problems and change nothing else. Remove or reword the "
    "offending text without adding any new facts, and keep all the rules. Problems:\n"
    "{problems}\n"
    "Return the full corrected resume in the same output format."
)


def build_repair_message(issues):
    problems = "\n".join('- %s: "%s" - %s' % (i.get("type", ""), i.get("quote", ""), i.get("reason", ""))
                         for i in issues)
    text = REPAIR_INSTRUCTION.replace("{problems}", problems)
    if any(i.get("type") == "LOST_ITEM" for i in issues):
        text += (" For LOST_ITEM problems, put the listed item back into the skills section "
                 "(its wording may be inflected to fit the sentence); do not remove it.")
    return text


# ---- v2.2: CHANGELOG строится кодом ---------------------------------------

_CHANGELOG_LABELS = {
    "en": {"changes": "Changes", "rewritten": "Rewritten", "added": "Added", "removed": "Removed",
           "skills_same": "Skills: reformatted, no items changed",
           "skills_added": "Skills: reformatted. Added: "},
    "ru": {"changes": "Изменения", "rewritten": "Переписано", "added": "Добавлено", "removed": "Удалено",
           "skills_same": "Навыки: переформатированы, состав не изменён",
           "skills_added": "Навыки: переформатированы. Добавлено: "},
    "uk": {"changes": "Зміни", "rewritten": "Переписано", "added": "Додано", "removed": "Видалено",
           "skills_same": "Навички: переформатовано, склад не змінено",
           "skills_added": "Навички: переформатовано. Додано: "},
}


def _line_key(line):
    """Ключ сравнения строки: без маркера списка и лишних пробелов."""
    return " ".join(_LIST_MARKER_RE.sub("", line.strip(), count=1).split())


def _content_lines(text):
    return [l.strip() for l in (text or "").split("\n") if l.strip()]


def _pair_replaced(old, new):
    """Сопоставить строки заменённого блока: поровну - по порядку, иначе по сходству."""
    if len(old) == len(new):
        return [(o, n) for o, n in zip(old, new)], [], []
    pairs, used = [], set()
    for o in old:
        best, best_r = None, 0.5
        for j, n in enumerate(new):
            if j in used:
                continue
            r = difflib.SequenceMatcher(None, _line_key(o), _line_key(n)).ratio()
            if r >= best_r:
                best, best_r = j, r
        if best is not None:
            used.add(best)
            pairs.append((o, new[best]))
    paired_old = {o for o, _ in pairs}
    removed = [o for o in old if o not in paired_old]
    added = [n for j, n in enumerate(new) if j not in used]
    return pairs, removed, added


def _clean_quote(line):
    """Цитата для CHANGELOG: без маркера списка в начале и висящей точки в конце."""
    line = _LIST_MARKER_RE.sub("", line.strip(), count=1).strip()
    if line.endswith("."):
        line = line[:-1].rstrip()
    return line


def _split_skill_lines(text):
    """(прочие непустые строки, непустые строки-пункты раздела навыков) - порядок сохранён."""
    other, skills = [], []
    for line, is_item in _iter_skill_lines(text):
        if line.strip():
            (skills if is_item else other).append(line.strip())
    return other, skills


def build_changelog(source, output_main):
    """
    Отличия результата от исходника, посчитанные кодом (difflib по строкам).
    Заменённая строка: «было» → «стало»; добавленная: + «текст»; удалённая: - «текст».
    Одинаковые строки не включаются; маркер списка и висящая точка в цитатах убираются, пара
    с одинаковыми «было» и «стало» не выводится. Раздел навыков, у которого сохранились все
    пункты источника, не разбирается построчно: одна строка "Навыки: переформатированы..."
    (+ список новых пунктов). Подписи: ru/uk по языку резюме, иначе en. Нет отличий - "".
    """
    labels = _CHANGELOG_LABELS.get(detect_lang(source), _CHANGELOG_LABELS["en"])
    a, b = _content_lines(source), _content_lines(output_main)

    skills_line, new_items = "", []
    src_items = skill_items(source)
    if src_items:
        out_items = skill_items(output_main)
        out_norm = {_norm_item(x) for x in out_items}
        if all(_norm_item(x) in out_norm for x in src_items):
            a, a_skills = _split_skill_lines(source)
            b, b_skills = _split_skill_lines(output_main)
            if [_line_key(x) for x in a_skills] != [_line_key(x) for x in b_skills]:
                src_norm = {_norm_item(x) for x in src_items}
                new_items = [x for x in out_items if _norm_item(x) not in src_norm]
                skills_line = (labels["skills_added"] + ", ".join(new_items)) if new_items \
                    else labels["skills_same"]

    sm = difflib.SequenceMatcher(None, [_line_key(x) for x in a], [_line_key(x) for x in b],
                                 autojunk=False)
    rewritten, added, removed = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            removed.extend(a[i1:i2])
        elif tag == "insert":
            added.extend(b[j1:j2])
        else:
            pairs, rem, add = _pair_replaced(a[i1:i2], b[j1:j2])
            rewritten.extend(pairs)
            removed.extend(rem)
            added.extend(add)
    added.extend(new_items)

    rewritten = [(_clean_quote(o), _clean_quote(n)) for o, n in rewritten]
    rewritten = [(o, n) for o, n in rewritten if o != n]
    added = [t for t in (_clean_quote(x) for x in added) if t]
    removed = [t for t in (_clean_quote(x) for x in removed) if t]
    if not (skills_line or rewritten or added or removed):
        return ""
    out = [labels["changes"]]
    if skills_line:
        out.append(skills_line)
    if rewritten:
        out.append(labels["rewritten"] + ":")
        out.extend("\u00ab%s\u00bb \u2192 \u00ab%s\u00bb" % (o, n) for o, n in rewritten)
    if added:
        out.append(labels["added"] + ":")
        out.extend("+ \u00ab%s\u00bb" % t for t in added)
    if removed:
        out.append(labels["removed"] + ":")
        out.extend("- \u00ab%s\u00bb" % t for t in removed)
    return "\n".join(out)


def _heading_line(text, aliases, default):
    """Строка-заголовок секции как её написала модель (или default)."""
    for line in (text or "").split("\n"):
        if _norm_heading(line) in aliases:
            return line.strip()
    return default


def assemble_final(source, model_text):
    """
    Итог: основной текст + DERIVED модели (как есть) + CHANGELOG от кода.
    Возвращает (final_text, raw_model_changelog).
    """
    main, derived, raw_changelog = split_sections(model_text or "")
    parts = [main]
    if derived:
        parts.append(_heading_line(model_text, _DERIVED_ALIASES, "DERIVED") + "\n" + derived)
    changelog = build_changelog(source, main)
    if changelog:
        parts.append(changelog)
    return "\n\n".join(p for p in parts if p), raw_changelog

class _Pacer:
    """Пауза между ВСЕМИ запросами к API (перед первым паузы нет)."""
    def __init__(self, pause):
        self.pause = pause
        self.n = 0

    def wait(self):
        if self.n and self.pause > 0:
            time.sleep(self.pause)
        self.n += 1


class _BudgetExhausted(Exception):
    """Бюджет времени (max_seconds) исчерпан до очередного запроса."""


def _now():
    return time.monotonic()


def rewrite_with_repair(source, model, judge_model, repair_rounds=2, pause=0,
                        api_key=None, temperature=0.4, max_tokens=6000, today=None, pacer=None,
                        deadline=None):
    """
    Раунд 0: переписывание (SYSTEM v2) -> postprocess -> det_issues + судья.
    Нет замечаний -> status "clean". Иначе до repair_rounds раундов repair
    (SYSTEM v2 + USER + ASSISTANT предыдущий результат + USER с замечаниями);
    исправлено -> "repaired" (rounds = номер раунда), остались -> "unresolved".
    verdict судьи "error" -> от судьи замечаний нет, judge_error=True (det_issues работают).
    deadline (значение _now()): если исчерпан до очередного запроса - выход со status
    "unresolved" и тем, что есть (budget_exhausted=True).
    """
    pacer = pacer or _Pacer(pause)
    base_messages = build_messages(source, today)
    state = {"calls": 0, "seconds": 0.0, "prompt": 0, "completion": 0, "total": 0,
             "dropped": set(), "retries": 0}
    judge_error = [False]

    def _bounded_call(*args, **kw):
        if deadline is not None:
            kw["timeout"] = max(5.0, min(120.0, deadline - _now()))
        return call_groq(*args, **kw)

    def _check_budget():
        if deadline is not None and _now() >= deadline:
            raise _BudgetExhausted()

    def ask(messages):
        _check_budget()
        pacer.wait()
        res = _bounded_call(api_key, model, messages, temperature=temperature, max_tokens=max_tokens)
        state["calls"] += 1
        state["seconds"] += res.get("seconds") or 0
        u = res.get("usage", {}) or {}
        state["prompt"] += u.get("prompt_tokens") or 0
        state["completion"] += u.get("completion_tokens") or 0
        state["total"] += u.get("total_tokens") or 0
        state["dropped"].update(res.get("dropped_params", []))
        state["retries"] += res.get("retries_429", 0)
        return postprocess(source, res["text"])

    def evaluate(text):
        _check_budget()
        pacer.wait()
        try:
            judge = run_judge(api_key, judge_model, source, text, call=_bounded_call)
        except Exception as e:
            judge = {"verdict": "error", "issues": [], "raw": "judge request failed: %s" % e,
                     "model": judge_model, "prompt_version": JUDGE_PROMPT_VERSION}
        state["calls"] += 1
        state["seconds"] += judge.get("seconds") or 0
        state["total"] += judge.get("tokens") or 0
        if judge["verdict"] == "error":
            judge_error[0] = True
            judge_issues = []
        else:
            judge_issues = judge["issues"]
        return judge, _merge_issues(judge_issues, det_issues(source, text))

    text, judge, issues = "", {}, []
    issues_by_round = []
    status, rounds = "unresolved", 0
    budget_exhausted = False

    try:
        text = ask(base_messages)
        judge, issues = evaluate(text)
        issues_by_round = [issues]
        status = "clean" if not issues else "unresolved"

        for r in range(1, repair_rounds + 1):
            if not issues:
                break
            messages = base_messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": build_repair_message(issues)},
            ]
            text = ask(messages)
            judge, issues = evaluate(text)
            issues_by_round.append(issues)
            rounds = r
            if not issues:
                status = "repaired"
                break
    except _BudgetExhausted:
        budget_exhausted = True
        status = "unresolved"
        # замечания последнего текста: судью уже не спросить, остаются проверки кодом
        issues = det_issues(source, text) if text else []

    final_text, raw_model_changelog = assemble_final(source, text)
    return {
        "status": status,
        "rounds": rounds,
        "calls": state["calls"],
        "issues_by_round": issues_by_round,
        "final_issues": issues,
        "judge_error": judge_error[0],
        "judge": judge,
        "budget_exhausted": budget_exhausted,
        "text": text,
        "final_text": final_text,
        "raw_model_changelog": raw_model_changelog,
        "changelog_source": "code",
        "result": {
            "text": text,
            "usage": {"prompt_tokens": state["prompt"], "completion_tokens": state["completion"],
                      "total_tokens": state["total"]},
            "seconds": round(state["seconds"], 2),
            "dropped_params": sorted(state["dropped"]),
            "retries_429": state["retries"],
        },
    }


# ---------------------------------------------------------------------------
# Публичный интерфейс для сервиса
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "openai/gpt-oss-120b"
_ERR_NO_KEY = "API key is not configured"
_ERR_NO_TEXT = "Resume text is empty"
_ERR_FAILED = "Creative rewrite failed"


def _empty_checks():
    return {"NEW_NUMBERS": [], "DERIVED_NUMBERS": [], "LOST_CONTACTS": [],
            "HEADER_PRESERVED": False, "FORBIDDEN": 0}


def _error_result(error):
    """Результат со status "error": без деталей исключения, ключа и текста резюме."""
    return {
        "status": "error", "acceptable": False, "text": "", "final_text": "", "changelog": "",
        "rounds": 0, "calls": 0, "final_issues": [], "judge_error": False,
        "checks": _empty_checks(), "error": error, "prompt_version": PROMPT_VERSION,
    }


def _is_acceptable(status, checks):
    """
    Результат можно отдавать пользователю только если одновременно: status clean/repaired,
    нет новых чисел, не потеряны контакты, шапка сохранена, нет запрещённых фраз.
    Иначе сервис берёт точный режим.
    """
    return bool(
        status in ("clean", "repaired")
        and not checks["NEW_NUMBERS"]
        and not checks["LOST_CONTACTS"]
        and checks["HEADER_PRESERVED"] is True
        and checks["FORBIDDEN"] == 0
    )


def creative_rewrite(source_text, api_key, model=DEFAULT_MODEL, judge_model=DEFAULT_MODEL,
                     repair_rounds=2, pause=0.0, max_seconds=120):
    """
    Переписать резюме целиком (creative-режим). Никогда не бросает исключений.

    Возвращает dict: status ("clean" | "repaired" | "unresolved" | "error"), acceptable (bool),
    text (результат модели после postprocess), final_text (text + DERIVED + CHANGELOG от кода),
    changelog (CHANGELOG от кода), rounds, calls, final_issues, judge_error,
    checks (NEW_NUMBERS, DERIVED_NUMBERS, LOST_CONTACTS, HEADER_PRESERVED, FORBIDDEN),
    error (краткий общий текст или None), prompt_version.
    """
    try:
        if not isinstance(api_key, str) or not api_key.strip():
            return _error_result(_ERR_NO_KEY)
        if not isinstance(source_text, str) or not source_text.strip():
            return _error_result(_ERR_NO_TEXT)

        deadline = None if max_seconds is None else _now() + max_seconds
        rr = rewrite_with_repair(source_text, model, judge_model, repair_rounds=repair_rounds,
                                 pause=pause, api_key=api_key, deadline=deadline)

        raw_checks = check_output(source_text, rr["text"])
        checks = {
            "NEW_NUMBERS": list(raw_checks["NEW_NUMBERS"]),
            "DERIVED_NUMBERS": list(raw_checks["DERIVED_NUMBERS"]),
            "LOST_CONTACTS": list(raw_checks["LOST_CONTACTS"]),
            "HEADER_PRESERVED": bool(raw_checks["HEADER_PRESERVED"]),
            "FORBIDDEN": int(raw_checks["FORBIDDEN_TOTAL"]),
        }
        main_text = split_sections(rr["text"])[0]
        return {
            "status": rr["status"],
            "acceptable": _is_acceptable(rr["status"], checks),
            "text": rr["text"],
            "final_text": rr["final_text"],
            "changelog": build_changelog(source_text, main_text),
            "rounds": rr["rounds"],
            "calls": rr["calls"],
            "final_issues": rr["final_issues"],
            "judge_error": rr["judge_error"],
            "checks": checks,
            "error": None,
            "prompt_version": PROMPT_VERSION,
        }
    except Exception:
        return _error_result(_ERR_FAILED)
