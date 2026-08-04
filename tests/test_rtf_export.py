"""
Тесты для _generate_rtf() — изолированная проверка генерации RTF.
Cycle R1: только функция генерации (без Flask-роута, без фронтенда).

Запуск: python -m pytest tests/test_rtf_export.py -v

ВАЖНОЕ ОТЛИЧИЕ ОТ PDF (см. tests/test_pdf_export.py и отчёт цикла P1):
в RTF направление RTL/LTR задаётся управляющими словами (\\rtlch/\\ltrch),
а не физической перестановкой символов (bidi.get_display(), как в PDF).
Поэтому round-trip через striprtf.rtf_to_text() оказался точным даже на
смешанных RTL+LTR строках — эмпирически подтверждено при разработке,
см. docstring _generate_rtf в app/missing_routes4.py.
"""
import os
import re
import sys

sys.path.insert(0, '/home/claude/resumeai')

# Безопасные тестовые значения ДО ЛЮБОГО импорта app/config — тот же паттерн,
# что и в tests/test_pdf_export.py / tests/test_odt_export.py. Порядок
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
from striprtf.striprtf import rtf_to_text

import app.missing_routes4 as mr
from app import create_app


def _expected_clean(text):
    """Тот же алгоритм очистки, что и внутри _generate_rtf — используется
    только для построения ожидаемого текста в тестах."""
    clean = re.sub(r"###ITEM_\d+###", "", text)
    lines = [
        l.strip().lstrip('#').replace('**', '').replace('*', '').strip()
        for l in clean.split("\n")
    ]
    return "\n".join(lines)


def _generate_and_extract(text):
    rtf_bytes = mr._generate_rtf(text)
    rtf_str = rtf_bytes.decode('utf-8')
    extracted = rtf_to_text(rtf_str)
    return rtf_str, extracted


# ---------------------------------------------------------------------------
# Латинский текст
# ---------------------------------------------------------------------------

def test_01_latin_roundtrip():
    text = "###ITEM_001###\nJohn Doe\nSoftware Engineer\n\n###ITEM_002###\nManaged a team of 5 engineers at Acme Corp."
    rtf_str, extracted = _generate_and_extract(text)
    assert extracted.strip() == _expected_clean(text).strip()


def test_02_item_markers_not_leaked():
    text = "###ITEM_001###\nSome text\n\n###ITEM_002###\nMore text"
    _, extracted = _generate_and_extract(text)
    assert "###ITEM_" not in extracted


def test_03_markdown_stripped():
    text = "###ITEM_001###\n**Bold Title**\n\n###ITEM_002###\n*italic-ish note*"
    _, extracted = _generate_and_extract(text)
    assert "**" not in extracted
    assert "Bold Title" in extracted
    assert "italic-ish note" in extracted


# ---------------------------------------------------------------------------
# Иврит — точный round-trip ожидается и подтверждается (в отличие от PDF)
# ---------------------------------------------------------------------------

def test_04_pure_hebrew_exact_roundtrip():
    text = "###ITEM_001###\nישראל ישראלי מהנדס תוכנה בכיר\n\n###ITEM_002###\nניסיון תעסוקתי בחברת אקמי"
    _, extracted = _generate_and_extract(text)
    assert extracted.strip() == _expected_clean(text).strip()


def test_05_mixed_hebrew_and_digits_exact_roundtrip():
    """
    В отличие от PDF (test_pdf_export.py::test_05, где смешанные строки
    проверялись только структурно из-за реордеринга при физической
    bidi-перестановке символов), в RTF направление — это только метаданные
    отображения, символы не переставляются физически. Поэтому здесь можно
    и нужно делать строгую проверку точного совпадения даже для смешанного
    контента — что и подтвердилось эмпирически.
    """
    text = "###ITEM_001###\nמהנדס תוכנה בכיר | 050-1234567 | israel@example.com"
    _, extracted = _generate_and_extract(text)
    assert extracted.strip() == _expected_clean(text).strip()


def test_06_mixed_hebrew_multiline_exact_roundtrip():
    text = (
        "###ITEM_001###\n"
        "חברת אקמי בעמ, 2020-2026\n"
        "ניהל צוות פיתוח, אחראי על ארכיטקטורת המערכת ותהליכי CI/CD"
    )
    _, extracted = _generate_and_extract(text)
    assert extracted.strip() == _expected_clean(text).strip()


# ---------------------------------------------------------------------------
# Синтаксическая валидность RTF — сбалансированность фигурных скобок
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "###ITEM_001###\nJohn Doe",
    "###ITEM_001###\nישראל ישראלי",
    "###ITEM_001###\nMixed: C++ and {braces} and \\backslash\\",
    "###ITEM_001###\n**Bold**\n\n###ITEM_002###\n*italic*",
    "###ITEM_001###\n\n\n",
    "",
])
def test_07_braces_balanced(text):
    """Сбалансированность фигурных скобок — необходимое (не достаточное)
    условие валидности RTF-структуры; striprtf терпим к ошибкам и не
    ловит разбалансированные скобки сам по себе, поэтому проверяем отдельно."""
    rtf_str = mr._generate_rtf(text).decode('utf-8')
    assert rtf_str.count('{') == rtf_str.count('}'), (
        f"Разбалансированные фигурные скобки: {{ = {rtf_str.count('{')}, "
        f"}} = {rtf_str.count('}')}"
    )


def test_08_special_rtf_chars_escaped_and_roundtrip():
    """Backslash и фигурные скобки в исходном тексте должны быть
    экранированы и корректно восстанавливаться при чтении обратно."""
    text = "###ITEM_001###\nC++ \\ {test} special chars"
    rtf_str, extracted = _generate_and_extract(text)
    assert rtf_str.count('{') == rtf_str.count('}')
    assert extracted.strip() == _expected_clean(text).strip()


# ---------------------------------------------------------------------------
# Пустой/пробельный вход
# ---------------------------------------------------------------------------

def test_09_empty_text_does_not_crash():
    rtf_bytes = mr._generate_rtf("")
    assert isinstance(rtf_bytes, bytes)
    rtf_str = rtf_bytes.decode('utf-8')
    assert rtf_str.count('{') == rtf_str.count('}')
    # Не должно падать при чтении обратно
    rtf_to_text(rtf_str)


def test_10_whitespace_only_text_does_not_crash():
    rtf_bytes = mr._generate_rtf("###ITEM_001###\n\n\n")
    rtf_str = rtf_bytes.decode('utf-8')
    assert rtf_str.count('{') == rtf_str.count('}')
    rtf_to_text(rtf_str)


def test_11_marker_only_no_content_does_not_crash():
    rtf_bytes = mr._generate_rtf("###ITEM_001###")
    rtf_str = rtf_bytes.decode('utf-8')
    assert "###ITEM_" not in rtf_to_text(rtf_str)


# ===========================================================================
# Cycle R2 — HTTP-роут POST /api/improve/rtf
# Зеркально tests/test_pdf_export.py (Cycle P2) / tests/test_odt_export.py
# (Cycle O2): тот же fixture, тот же способ авторизации через
# session_transaction(), те же три сценария.
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
    (см. tests/test_credits_pool.py, tests/test_odt_export.py, tests/test_pdf_export.py)."""
    from app.services.auth_service import AuthService

    result = AuthService.register(email, 'somepassword123')
    user = result['user']
    with client.session_transaction() as sess:
        sess['user_id'] = user.id
    return user


_RTF_TEST_TEXT = '###ITEM_001###\nJohn Doe\n\n###ITEM_002###\nSoftware Engineer'


def test_12_rtf_route_requires_login_returns_401(client_app):
    """Незалогиненный, неадминский запрос -> 401, без каких-либо кредитов."""
    app = client_app
    client = app.test_client()

    resp = client.post('/api/improve/rtf', data={'improved_resume': _RTF_TEST_TEXT})
    assert resp.status_code == 401
    assert resp.get_json()['success'] is False


def test_13_rtf_route_returns_valid_rtf_when_credits_available(client_app):
    """Залогиненный пользователь с credits_remaining() > 0 -> 200, корректный RTF."""
    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'rtf-route-ok-test@example.com')

    sub_before = user.get_active_subscription()
    assert sub_before.credits_remaining() == 2

    resp = client.post('/api/improve/rtf', data={'improved_resume': _RTF_TEST_TEXT})

    assert resp.status_code == 200
    assert 'rtf' in resp.content_type

    # Тело ответа — валидный RTF: должно читаться через striprtf без исключений
    # и содержать ожидаемый текст.
    rtf_str = resp.data.decode('utf-8')
    assert rtf_str.count('{') == rtf_str.count('}')
    extracted = rtf_to_text(rtf_str)
    assert 'John Doe' in extracted
    assert 'Software Engineer' in extracted


def test_14_rtf_route_blocks_and_does_not_double_charge_when_credits_exhausted(client_app):
    """
    Залогиненный пользователь с credits_remaining() == 0 -> 403, и credits_used
    не меняется после запроса (прямая проверка "без повторного списания" —
    списание кредита происходит только в /api/improve, не здесь).
    """
    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'rtf-route-exhausted-test@example.com')

    sub = user.get_active_subscription()
    sub.credits_used = sub.credits_granted  # исчерпать пул напрямую, без вызова /api/improve
    from app import db
    db.session.commit()

    assert sub.credits_remaining() == 0
    credits_used_before = sub.credits_used

    resp = client.post('/api/improve/rtf', data={'improved_resume': _RTF_TEST_TEXT})

    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'No credits remaining. Buy a credit pack to continue.'

    sub_after = user.get_active_subscription()
    assert sub_after.credits_used == credits_used_before
