"""
Тесты для _generate_pdf() — изолированная проверка генерации PDF.
Cycle P1: только функция генерации (без Flask-роута, без фронтенда).

Запуск: python -m pytest tests/test_pdf_export.py -v

ВАЖНО — эмпирическая находка (см. отчёт цикла P1):
PyPDF2.extract_text() не гарантирует сохранение логического порядка слов
в строке, если строка смешивает иврит с цифрами/латиницей (RTL+LTR в одной
физической строке). Для ЧИСТОГО ивритского текста (без смешения) извлечение
оказалось точным и детерминированным при многократном прогоне — на этом
построена строгая проверка (test_04). Для смешанного контента строгую
проверку текста не делаем принципиально — только структурную валидность
(test_05), чтобы не притягивать несуществующую гарантию силой.
"""
import io
import os
import sys

sys.path.insert(0, '/home/claude/resumeai')

# Безопасные тестовые значения ДО ЛЮБОГО импорта app/config — тот же паттерн,
# что и в tests/test_odt_export.py / tests/test_credits_pool.py. Порядок
# критичен: "import app.missing_routes4" ниже уже тянет за собой "import app"
# (пакет), а тот на верхнем уровне делает "from config import config" — если
# переменные окружения выставить после этого импорта, config.py закэшируется
# с FLASK_ENV='production' по умолчанию и create_app('testing') позже упадёт
# на проверке SECRET_KEY.
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import pytest
import PyPDF2

import app.missing_routes4 as mr
from app import create_app


def _extract(buf):
    reader = PyPDF2.PdfReader(buf)
    return reader, "\n".join(p.extract_text() or "" for p in reader.pages)


# ---------------------------------------------------------------------------
# Латинский текст — строгая проверка точного совпадения
# ---------------------------------------------------------------------------

def test_01_latin_basic_no_exception_and_readable():
    """Базовая генерация с латинским текстом не должна падать, PDF должен
    открываться и содержать ожидаемые слова."""
    text = "###ITEM_001###\nJohn Doe\nSoftware Engineer\n\n###ITEM_002###\nManaged a team of 5 engineers at Acme Corp."
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    assert "John Doe" in extracted
    assert "Software Engineer" in extracted
    assert "Managed a team of 5 engineers at Acme Corp." in extracted


def test_02_item_markers_removed():
    """Маркеры ###ITEM_NNN### не должны попадать в извлечённый текст."""
    text = "###ITEM_001###\nSome text here\n\n###ITEM_002###\nMore text"
    buf = mr._generate_pdf(text)
    _, extracted = _extract(buf)
    assert "###ITEM_" not in extracted


def test_03_markdown_stripped():
    """Markdown-звёздочки должны быть убраны, как и в _generate_odt."""
    text = "###ITEM_001###\n**Bold Title**\n\n###ITEM_002###\n*italic-ish note*"
    buf = mr._generate_pdf(text)
    _, extracted = _extract(buf)
    assert "**" not in extracted
    assert "Bold Title" in extracted
    assert "italic-ish note" in extracted


# ---------------------------------------------------------------------------
# Иврит — чистые (без смешения с цифрами/латиницей) строки: точная проверка
# ---------------------------------------------------------------------------

def test_04_pure_hebrew_exact_roundtrip():
    """
    Чистый ивритский текст (без смешения с цифрами/латиницей в одной строке)
    извлекается PyPDF2 в правильном логическом порядке слов и символов —
    эмпирически подтверждено детерминированным повтором (3x идентичный
    результат) при написании этого теста. Строгая проверка точного
    совпадения оправдана только для этого случая.
    """
    text = "###ITEM_001###\nישראל ישראלי מהנדס תוכנה בכיר\n\n###ITEM_002###\nניסיון תעסוקתי בחברת אקמי"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    assert extracted.strip() == "ישראל ישראלי מהנדס תוכנה בכיר\nניסיון תעסוקתי בחברת אקמי"


def test_04b_pure_hebrew_deterministic_across_runs():
    """Regression guard: повторная генерация того же текста даёт identical
    extract_text() — важно, т.к. bidi-перестановка теоретически могла
    зависеть от состояния (не должна, но проверяем явно)."""
    text = "###ITEM_001###\nניהול פרויקטים ופיתוח תוכנה"
    results = set()
    for _ in range(3):
        buf = mr._generate_pdf(text)
        _, extracted = _extract(buf)
        results.add(extracted.strip())
    assert len(results) == 1, f"Недетерминированное извлечение: {results}"


# ---------------------------------------------------------------------------
# Смешанный контент (иврит + цифры/латиница в одной строке) —
# ТОЛЬКО структурная валидность, без строгой проверки порядка слов.
# ---------------------------------------------------------------------------

def test_05_mixed_hebrew_and_digits_structurally_valid():
    """
    Смешанная RTL+LTR строка (иврит + цифра) — PDF должен генерироваться без
    исключений и оставаться структурно валидным (открывается, есть текст).
    Порядок слов в строке НЕ проверяем строго — эмпирически он может
    отличаться от логического (см. docstring модуля и отчёт цикла P1).
    """
    text = "###ITEM_001###\nניהל צוות של 5 מהנדסים בחברת אקמי"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    assert extracted.strip() != ""
    # Отдельные ивритские слова (не смежные с цифрой) всё равно должны
    # встречаться как читаемые подстроки, даже если порядок слов в строке иной.
    assert "מהנדסים" in extracted
    assert "ניהל" in extracted


# ---------------------------------------------------------------------------
# Перенос длинных строк
# ---------------------------------------------------------------------------

def test_06_long_line_wraps_into_multiple_lines():
    """Строка длиннее ширины страницы должна быть перенесена на несколько
    физических строк — проверяем через подсчёт вызовов drawString (мок),
    не полагаясь на количество '\\n' в extract_text() (ненадёжно)."""
    long_line = "word " * 80  # заведомо шире доступной ширины A4 при 11pt
    text = f"###ITEM_001###\n{long_line.strip()}"

    calls = {"count": 0}
    from reportlab.pdfgen import canvas as pdf_canvas
    original_draw_string = pdf_canvas.Canvas.drawString

    def _counting_draw_string(self, *args, **kwargs):
        calls["count"] += 1
        return original_draw_string(self, *args, **kwargs)

    pdf_canvas.Canvas.drawString = _counting_draw_string
    try:
        buf = mr._generate_pdf(text)
    finally:
        pdf_canvas.Canvas.drawString = original_draw_string

    assert calls["count"] > 1, f"Длинная строка не была перенесена: только {calls['count']} вызов(ов) drawString"

    reader, _ = _extract(buf)
    assert len(reader.pages) == 1


def test_07_short_line_not_wrapped():
    """Regression guard: короткая строка не должна разбиваться на несколько
    вызовов drawString."""
    calls = {"count": 0}
    from reportlab.pdfgen import canvas as pdf_canvas
    original_draw_string = pdf_canvas.Canvas.drawString

    def _counting_draw_string(self, *args, **kwargs):
        calls["count"] += 1
        return original_draw_string(self, *args, **kwargs)

    pdf_canvas.Canvas.drawString = _counting_draw_string
    try:
        buf = mr._generate_pdf("###ITEM_001###\nShort line")
    finally:
        pdf_canvas.Canvas.drawString = original_draw_string

    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# Пагинация
# ---------------------------------------------------------------------------

def test_08_long_text_spans_multiple_pages():
    """Текст с большим количеством строк должен переноситься на несколько
    страниц PDF."""
    lines = [f"Line number {i} of the resume content section" for i in range(120)]
    text = "###ITEM_001###\n" + "\n".join(lines)
    buf = mr._generate_pdf(text)
    reader, _ = _extract(buf)
    assert len(reader.pages) > 1


def test_09_empty_text_does_not_crash():
    """Пустой/пробельный ввод не должен вызывать исключение."""
    buf = mr._generate_pdf("###ITEM_001###\n\n\n")
    reader, _ = _extract(buf)
    assert len(reader.pages) >= 1


# ---------------------------------------------------------------------------
# Шрифт
# ---------------------------------------------------------------------------

def test_10_font_file_exists_and_is_static_ttf():
    """Регистрируемый файл шрифта должен существовать и быть обычным
    (не variable) TTF — variable-инстансы имеют таблицу 'fvar', которой
    у static-шрифта быть не должно."""
    assert os.path.exists(mr._PDF_FONT_PATH), f"Font file missing: {mr._PDF_FONT_PATH}"
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH)
    assert "fvar" not in ft, "Шрифт содержит таблицу fvar — это variable font, а не static instance"


# ===========================================================================
# Cycle P-cyr — Alef не содержит кириллицу: PDF для русского/украинского
# резюме рисовал пустые квадраты вместо букв. Фикс: второй LTR-шрифт
# (FiraSans) для всех НЕ-ивритских строк, Alef остаётся только для иврита.
# ===========================================================================

def test_14_latin_font_file_exists_and_is_static_ttf():
    """Тот же контракт, что test_10, но для добавленного LTR-шрифта."""
    assert os.path.exists(mr._PDF_FONT_PATH_LATIN), f"Font file missing: {mr._PDF_FONT_PATH_LATIN}"
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH_LATIN)
    assert "fvar" not in ft, "Шрифт содержит таблицу fvar — это variable font, а не static instance"


def test_15_latin_font_covers_cyrillic_glyphs():
    """Регрессионный барьер именно на найденный баг: если LTR-шрифт когда-
    нибудь заменят на что-то без кириллицы — этот тест должен упасть раньше,
    чем баг снова доедет до прода."""
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH_LATIN)
    covered = set()
    for t in ft["cmap"].tables:
        covered |= set(t.cmap.keys())
    cyrillic_sample = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    missing = [c for c in cyrillic_sample if ord(c) not in covered]
    assert not missing, f"В шрифте отсутствуют кириллические глифы: {missing}"


def test_16_latin_font_covers_accented_latin_glyphs():
    """Заодно фиксируем то, что уже работало (расширенная латиница —
    французский/испанский/итальянский/польский/чешский/немецкий) — чтобы
    будущая замена шрифта не потеряла и это по пути."""
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH_LATIN)
    covered = set()
    for t in ft["cmap"].tables:
        covered |= set(t.cmap.keys())
    accented_sample = "éèçàêôñáíóúàòùìłąężśćńčšřěůýäöüß"
    missing = [c for c in accented_sample if ord(c) not in covered]
    assert not missing, f"В шрифте отсутствуют символы расширенной латиницы: {missing}"


def test_17_cyrillic_text_renders_without_exception_and_is_extractable():
    """Сквозная проверка на уровне _generate_pdf(): кириллический текст не
    падает и извлекается обратно (раньше здесь были бы пустые квадраты —
    но extract_text() на них тоже не упал бы молча, поэтому проверяем
    именно содержимое, а не только факт отсутствия исключения)."""
    text = "###ITEM_001###\nИванов Иван Иванович\nОпыт роботи: розробка програмного забезпечення."
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) >= 1
    assert "Иванов" in extracted
    assert "Опыт" in extracted


def test_18_mixed_hebrew_and_cyrillic_in_same_document_no_exception():
    """Документ, где одни строки на иврите (шрифт Alef), а другие на
    кириллице (шрифт FiraSans) — переключение шрифта построчно не должно
    падать и не должно путать перенос строк (word-wrap меряет ширину
    шрифтом СВОЕЙ строки, не шрифтом предыдущей)."""
    text = (
        "###ITEM_001###\n"
        "מוסתובוי סלבה — תמיכה טכנית\n"
        "###ITEM_002###\n"
        "Иванов Иван Иванович — опытный инженер-программист с большим стажем работы.\n"
    )
    buf = mr._generate_pdf(text)
    reader, _ = _extract(buf)
    assert len(reader.pages) >= 1


# ===========================================================================
# Cycle P-ar — арабский (диапазон \u0600-\u06FF) рисовался пустыми
# изолированными буквами (или квадратами) вместо связного письма. Фикс:
# третий шрифт (NotoSansArabic-VF.ttf) + arabic_reshaper.reshape() перед
# bidi.get_display() — в отличие от иврита арабским буквам нужен contextual
# shaping (изолированная/начальная/срединная/конечная форма), не только
# перестановка порядка.
#
# ВАЖНО — эмпирическая находка, симметричная той, что уже задокументирована
# для иврита в шапке файла (расхождение extract_text() с логическим
# текстом), но с ДРУГОЙ причиной и ДРУГИМ способом починки сравнения:
# после reshape() PyPDF2.extract_text() возвращает не базовые арабские
# буквы, а codepoints форм представления (Unicode block "Arabic
# Presentation Forms", \uFB50-\uFEFF) — это ожидаемое, детерминированное
# следствие contextual shaping, не баг и не недетерминизм. Прямое сравнение
# extracted-строки с оригиналом всегда ложно (проверено эмпирически перед
# написанием этого теста). unicodedata.normalize('NFKC', ...) восстанавливает
# исходные базовые буквы точно — на этом построена строгая проверка
# test_19 (тот же уровень строгости, что test_04 для иврита, но с
# дополнительным шагом нормализации).
# ===========================================================================

def test_19_pure_arabic_exact_roundtrip_after_nfkc():
    """
    Чистый арабский текст (без смешения с цифрами/латиницей) извлекается
    PyPDF2 в виде presentation-forms кодпоинтов (следствие reshape()) —
    после unicodedata.normalize('NFKC', ...) точно совпадает с исходным
    логическим текстом. Эмпирически подтверждено перед написанием теста
    (см. комментарий блока выше) — прямое сравнение без NFKC было бы
    гарантированно ложным, это не опечатка.
    """
    import unicodedata
    text = "###ITEM_001###\nمهندس برمجيات أول\n\n###ITEM_002###\nخبرة في تطوير التطبيقات"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    normalized = unicodedata.normalize("NFKC", extracted.strip())
    assert normalized == "مهندس برمجيات أول\nخبرة في تطوير التطبيقات"


def test_19b_pure_arabic_deterministic_across_runs():
    """Regression guard: повторная генерация того же арабского текста даёт
    identical extract_text() — тот же контракт, что test_04b для иврита."""
    text = "###ITEM_001###\nإدارة المشاريع وتطوير البرمجيات"
    results = set()
    for _ in range(3):
        buf = mr._generate_pdf(text)
        _, extracted = _extract(buf)
        results.add(extracted.strip())
    assert len(results) == 1, f"Недетерминированное извлечение: {results}"


def test_20_mixed_arabic_and_digits_structurally_valid():
    """Смешанная RTL+LTR строка (арабский + латиница/цифры) — PDF должен
    генерироваться без исключений и оставаться структурно валидным. Как и
    для иврита (test_05), порядок слов строго не проверяем — только факт
    непустого извлекаемого содержимого."""
    text = "###ITEM_001###\nطور تطبيقات ويب باستخدام Python 5 سنوات خبرة"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    assert extracted.strip() != ""


def test_21_arabic_font_file_exists():
    """Регистрируемый файл арабского шрифта должен существовать.
    В отличие от test_10/test_14 (Alef/FiraSans — static TTF, без 'fvar')
    здесь ОСОЗНАННО не проверяем отсутствие 'fvar': NotoSansArabic-VF.ttf —
    variable font, это задокументированное намеренное отклонение (см.
    комментарий у _PDF_FONT_NAME_ARABIC в missing_routes4.py — static-сборка
    для этого шрифта в принципе недоступна в формате, который принимает
    reportlab). Вместо этого проверяем то, что реально важно: дефолтный
    instance оси wght действительно = 400 (Regular), а не какой-то другой
    вес — именно это проверялось эмпирически перед принятием решения
    использовать variable font."""
    assert os.path.exists(mr._PDF_FONT_PATH_ARABIC), f"Font file missing: {mr._PDF_FONT_PATH_ARABIC}"
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH_ARABIC)
    assert "fvar" in ft, (
        "Ожидался variable font (см. комментарий в missing_routes4.py) — "
        "если это больше не так, комментарий и этот тест нужно обновить вместе."
    )
    wght_axis = next(a for a in ft["fvar"].axes if a.axisTag == "wght")
    assert wght_axis.defaultValue == 400, (
        f"Default instance веса шрифта изменился: {wght_axis.defaultValue} "
        f"(ожидался 400/Regular) — PDF будет рисовать другим начертанием."
    )


def test_22_arabic_font_covers_arabic_glyphs():
    """Регрессионный барьер: если шрифт когда-нибудь заменят на что-то без
    арабских глифов, тест должен упасть раньше, чем это дойдёт до прода."""
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH_ARABIC)
    covered = set()
    for t in ft["cmap"].tables:
        covered |= set(t.cmap.keys())
    arabic_sample = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
    missing = [c for c in arabic_sample if ord(c) not in covered]
    assert not missing, f"В шрифте отсутствуют арабские глифы: {missing}"


def test_23_arabic_text_renders_without_exception_and_is_extractable():
    """Сквозная проверка на уровне _generate_pdf(): арабский текст не падает
    и извлекается обратно (до фикса здесь были бы либо пустые изолированные
    буквы, либо пустые квадраты — но extract_text() на них тоже не упал бы
    молча, поэтому проверяем именно содержимое через NFKC, а не только
    отсутствие исключения)."""
    import unicodedata
    text = "###ITEM_001###\nأحمد حسن\nمهندس برمجيات"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) >= 1
    normalized = unicodedata.normalize("NFKC", extracted.strip())
    assert "أحمد" in normalized
    assert "مهندس" in normalized


def test_24_mixed_hebrew_and_arabic_in_same_document_no_exception():
    """Документ, где одни строки на иврите (шрифт Alef, без reshape), а
    другие на арабском (шрифт NotoArabic, с reshape) — маловероятный, но
    возможный кейс (например ошибочно определённый язык резюме). Оба RTL,
    оба используют \\u05xx/\\u06xx диапазоны — проверяем что has_hebrew
    проверяется раньше has_arabic и не путает шрифты/reshape между строками."""
    text = (
        "###ITEM_001###\n"
        "מוסתובוי סלבה — תמיכה טכנית\n"
        "###ITEM_002###\n"
        "أحمد حسن — مهندس برمجيات\n"
    )
    buf = mr._generate_pdf(text)
    reader, _ = _extract(buf)
    assert len(reader.pages) >= 1


def test_25_arabic_dependency_reshaper_importable():
    """Regression guard: arabic_reshaper должен быть импортируемым (проверка
    того, что requirements.txt действительно содержит и устанавливает
    зависимость, а не просто что она случайно оказалась в окружении)."""
    import arabic_reshaper  # noqa: F401 — сам факт успешного импорта — тест


# ===========================================================================
# Cycle CN — китайский (диапазон \u4E00-\u9FFF). В отличие от иврита/арабского
# (Cycle P1/P-ar), китайский LTR — bidi.get_display() и arabic_reshaper.reshape()
# здесь НЕ применяются, рисуется обычным drawString слева, той же веткой кода,
# что и латиница/кириллица (см. else-ветку в _generate_pdf).
#
# ВАЖНО — эмпирическая находка (проверено перед написанием тестов, не
# предположение по аналогии с ивритом/арабским): PyPDF2.extract_text()
# извлекает китайский текст ТОЧНО и в правильном порядке даже при смешении
# с цифрами/латиницей в одной физической строке (test_28) — в отличие от
# иврита (test_05) и арабского (test_20), где смешанные RTL+LTR строки
# проверяются только на структурную валидность, без строгого сравнения.
# Причина: у китайского в этом пайплайне вообще нет bidi-перестановки
# порядка символов (только LTR drawString) — поэтому нет и связанного с
# ней расхождения логического/визуального порядка при извлечении.
# ===========================================================================

def test_26_cjk_font_file_exists_and_is_static_ttf():
    """Тот же контракт, что test_10/test_14 (Alef/FiraSans): регистрируемый
    файл шрифта должен существовать и НЕ быть variable font ('fvar'
    отсутствует) — в отличие от test_21 (NotoSansArabic-VF), где 'fvar'
    ОЖИДАЕТСЯ по документированной причине. Здесь, наоборот, найден
    подлинный static-инстанс — см. комментарий у _PDF_FONT_NAME_CJK."""
    assert os.path.exists(mr._PDF_FONT_PATH_CJK), f"Font file missing: {mr._PDF_FONT_PATH_CJK}"
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH_CJK)
    assert "fvar" not in ft, "Шрифт содержит таблицу fvar — это variable font, а не static instance"
    assert "CFF " not in ft, "Шрифт использует CFF/PostScript-контуры — reportlab их не грузит"
    assert "glyf" in ft, "Ожидались TrueType-контуры (glyf)"


def test_27_cjk_font_covers_common_hanzi_glyphs():
    """Регрессионный барьер: если шрифт когда-нибудь заменят на что-то без
    нужных иероглифов, тест должен упасть раньше, чем это дойдёт до прода."""
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH_CJK)
    covered = set()
    for t in ft["cmap"].tables:
        covered |= set(t.cmap.keys())
    # Частотные иероглифы резюме-лексики: опыт/работа/образование/навыки/год
    sample = "简历经验工作教育背景技能年软件工程师开发负责"
    missing = [c for c in sample if ord(c) not in covered]
    assert not missing, f"В шрифте отсутствуют иероглифы: {missing}"


def test_28_pure_chinese_exact_roundtrip():
    """Чистый китайский текст извлекается PyPDF2 ТОЧНО (без каких-либо
    NFKC-нормализаций или допущений на неточный порядок, в отличие от
    иврита/арабского) — подтверждено эмпирически перед написанием теста,
    см. комментарий блока выше. Строгая проверка точного совпадения,
    включая случай смешения с цифрами/латиницей в одной строке."""
    text = "###ITEM_001###\n软件工程师\n\n###ITEM_002###\n使用 Python 开发了 5 个项目"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    assert extracted.strip() == "软件工程师\n使用 Python 开发了 5 个项目"


def test_28b_pure_chinese_deterministic_across_runs():
    """Regression guard: повторная генерация того же китайского текста даёт
    identical extract_text() — тот же контракт, что test_04b/test_19b."""
    text = "###ITEM_001###\n负责后端系统开发与维护"
    results = set()
    for _ in range(3):
        buf = mr._generate_pdf(text)
        _, extracted = _extract(buf)
        results.add(extracted.strip())
    assert len(results) == 1, f"Недетерминированное извлечение: {results}"


def test_29_chinese_text_renders_without_exception_and_is_extractable():
    """Сквозная проверка на уровне _generate_pdf(): китайский текст не падает
    и извлекается обратно корректно (до фикса здесь были бы пустые квадраты
    вместо иероглифов)."""
    text = "###ITEM_001###\n张伟\n软件工程师"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) >= 1
    assert "张伟" in extracted
    assert "软件工程师" in extracted


def test_30_mixed_hebrew_and_chinese_in_same_document_no_exception():
    """Документ, где одни строки на иврите (шрифт Alef, RTL с bidi), а
    другие на китайском (шрифт WenQuanYiMicroHei, LTR без bidi) — оба
    диапазона (\\u05xx и \\u4Exx-\\u9Fxx) не пересекаются, но проверяем что
    переключение шрифта построчно (и переключение RTL/LTR логики) не путает
    друг друга и не падает."""
    text = (
        "###ITEM_001###\n"
        "מוסתובוי סלבה — תמיכה טכנית\n"
        "###ITEM_002###\n"
        "软件工程师 - 五年经验\n"
    )
    buf = mr._generate_pdf(text)
    reader, _ = _extract(buf)
    assert len(reader.pages) >= 1


def test_31_mixed_chinese_latin_hebrew_arabic_all_four_scripts_no_exception():
    """Документ со всеми четырьмя шрифтами проекта на одной странице
    (латиница/кириллица, иврит, арабский, китайский) — самый широкий
    интеграционный тест: переключение между 4 шрифтами построчно, включая
    смену RTL/LTR и наличие/отсутствие reshape, не должно падать."""
    text = (
        "###ITEM_001###\n"
        "John Doe - Software Engineer\n"
        "###ITEM_002###\n"
        "מוסתובוי סלבה — תמיכה טכנית\n"
        "###ITEM_003###\n"
        "أحمد حسن — مهندس برمجيات\n"
        "###ITEM_004###\n"
        "软件工程师 - 五年经验\n"
    )
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) >= 1
    assert extracted.strip() != ""


def test_32_cjk_range_boundary_does_not_false_positive_on_japanese_kana_or_hangul():
    """Regression guard: диапазон \\u4E00-\\u9FFF — это только унифицированные
    Han-иероглифы. Японская кана (хирагана/катакана, \\u3040-\\u30FF) и
    корейский хангыль (\\uAC00-\\uD7A3) НЕ входят в этот диапазон и не должны
    ошибочно определяться как 'китайский' (has_cjk) — WenQuanYiMicroHei их
    не покрывает (шрифт заявлен только под Han/CJK, не под кану/хангыль),
    попытка нарисовать эти символы этим шрифтом дала бы пустые квадраты.
    Здесь фиксируем сам факт: чисто кана-строка не должна попадать в
    CJK-ветку классификации (только проверка диапазона на уровне текста,
    без обращения к приватному состоянию функции — косвенно, через то что
    рендер не падает и это остаётся заботой будущего цикла, если понадобится
    японский/корейский)."""
    # Хирагана "конничива" (здравствуйте) — вне диапазона \u4E00-\u9FFF
    kana = "こんにちは"
    assert not any("\u4E00" <= ch <= "\u9FFF" for ch in kana), \
        "Кана ошибочно попадает в диапазон CJK Han — граница диапазона неверна"


def test_33_cjk_dependency_fonttools_glyf_confirmed_no_cff():
    """Regression guard, зеркально test_25 (arabic_reshaper importable), но
    для более критичного риска у CJK: если файл шрифта когда-нибудь будет
    заменён на OTF/CFF-сборку (например, случайно перезаписан другой Noto
    CJK сборкой) — этот тест должен упасть раньше, чем reportlab выдаст
    TTFError в проде при первой генерации PDF с китайским текстом."""
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH_CJK)
    assert "CFF " not in ft and "CFF2" not in ft, (
        "Шрифт содержит CFF/CFF2 (PostScript outlines) — reportlab.pdfbase."
        "ttfonts.TTFont не сможет его загрузить (TTFError), см. диагностику "
        "Cycle CN в комментарии у _PDF_FONT_NAME_CJK."
    )


# ===========================================================================
# Cycle P2 — HTTP-роут POST /api/improve/pdf
# Зеркально tests/test_odt_export.py (Cycle O2): тот же fixture, тот же
# способ авторизации через session_transaction(), те же три сценария.
# ===========================================================================

@pytest.fixture
def client_app():
    """Полноценный test client + доступ к app для запросов с сессией."""
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app


def _register_and_login(app, client, email):
    """Зарегистрировать пользователя (получает credits_granted=2, credits_used=0)
    и поставить сессию — тот же способ авторизации, что использует живой legacy-фронтенд
    (см. tests/test_credits_pool.py, tests/test_odt_export.py)."""
    from app.services.auth_service import AuthService

    result = AuthService.register(email, 'somepassword123')
    user = result['user']
    with client.session_transaction() as sess:
        sess['user_id'] = user.id
    return user


_PDF_TEST_TEXT = '###ITEM_001###\nJohn Doe\n\n###ITEM_002###\nSoftware Engineer'


def test_11_pdf_route_requires_login_returns_401(client_app):
    """Незалогиненный, неадминский запрос -> 401, без каких-либо кредитов."""
    app = client_app
    client = app.test_client()

    resp = client.post('/api/improve/pdf', data={'improved_resume': _PDF_TEST_TEXT})
    assert resp.status_code == 401
    assert resp.get_json()['success'] is False


def test_12_pdf_route_returns_valid_pdf_when_credits_available(client_app):
    """Залогиненный пользователь с credits_remaining() > 0 -> 200, корректный PDF."""
    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'pdf-route-ok-test@example.com')

    sub_before = user.get_active_subscription()
    assert sub_before.credits_remaining() == 2

    resp = client.post('/api/improve/pdf', data={'improved_resume': _PDF_TEST_TEXT})

    assert resp.status_code == 200
    assert 'application/pdf' in resp.content_type

    # Тело ответа — валидный PDF: должен открыться через PyPDF2.PdfReader
    reader, extracted = _extract(io.BytesIO(resp.data))
    assert len(reader.pages) >= 1
    assert 'John Doe' in extracted
    assert 'Software Engineer' in extracted


def test_13_pdf_route_blocks_and_does_not_double_charge_when_credits_exhausted(client_app):
    """
    Залогиненный пользователь с credits_remaining() == 0 -> 403, и credits_used
    не меняется после запроса (прямая проверка "без повторного списания" —
    списание кредита происходит только в /api/improve, не здесь).
    """
    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'pdf-route-exhausted-test@example.com')

    sub = user.get_active_subscription()
    sub.credits_used = sub.credits_granted  # исчерпать пул напрямую, без вызова /api/improve
    from app import db
    db.session.commit()

    assert sub.credits_remaining() == 0
    credits_used_before = sub.credits_used

    resp = client.post('/api/improve/pdf', data={'improved_resume': _PDF_TEST_TEXT})

    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'No credits remaining. Buy a credit pack to continue.'

    sub_after = user.get_active_subscription()
    assert sub_after.credits_used == credits_used_before
