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
