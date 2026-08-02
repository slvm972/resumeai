"""
Тесты для _generate_odt() — изолированная проверка генерации ODT.
Cycle O1: только функция генерации (тесты 01-07 ниже).
Cycle O2: HTTP-роут /api/improve/odt через app.test_client() (тесты ниже,
тот же паттерн авторизации через session_transaction(), что и в
tests/test_credits_pool.py).

Запуск: python -m pytest tests/test_odt_export.py -v
"""
import os
import sys

sys.path.insert(0, '/home/claude/resumeai')

# Безопасные тестовые значения ДО ЛЮБОГО импорта app/config — тот же паттерн,
# что и в tests/test_credits_pool.py / tests/test_error_handling.py. Порядок
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

from odf.opendocument import load
from odf.text import P

import app.missing_routes4 as mr
from app import create_app


def _all_paragraph_texts(buf):
    """Прочитать ODT обратно и вернуть список текстов всех параграфов."""
    doc = load(buf)
    return [str(p) for p in doc.getElementsByType(P)]


def test_01_generate_odt_roundtrip_no_exception():
    """Базовая генерация не должна падать, файл должен читаться обратно."""
    text = "###ITEM_001###\nJohn Doe\n\n###ITEM_002###\nSoftware Engineer"
    buf = mr._generate_odt(text)
    # load() бросит исключение, если файл повреждён — этого достаточно
    # для проверки структурной валидности .odt
    doc = load(buf)
    assert doc is not None


def test_02_no_item_markers_in_output():
    """Маркеры ###ITEM_NNN### не должны попадать ни в один параграф результата."""
    text = "###ITEM_001###\nSome text here\n\n###ITEM_002###\nMore text"
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    for t in texts:
        assert "###ITEM_" not in t, f"Маркер утёк в текст: {t!r}"


def test_03_latin_text_preserved():
    """Латинский текст должен сохраниться без искажений."""
    text = "###ITEM_001###\nJohn Doe\nSenior Software Engineer"
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    assert "John Doe" in texts
    assert "Senior Software Engineer" in texts


def test_04_hebrew_text_preserved_and_readable():
    """Ивритский текст должен сохраниться и корректно читаться обратно
    (round-trip через odf.opendocument.load, без исключений)."""
    text = "###ITEM_001###\nניסיון תעסוקתי\n\n###ITEM_002###\nמנהל רשת בכיר"
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    assert "ניסיון תעסוקתי" in texts
    assert "מנהל רשת בכיר" in texts


def test_05_mixed_hebrew_latin_no_crash():
    """Смешанный иврит + латиница в одном документе не должен ронять генерацию."""
    text = (
        "###ITEM_001###\nJohn Doe\n\n"
        "###ITEM_002###\nניסיון תעסוקתי\n\n"
        "###ITEM_003###\nPython, Docker, Kubernetes"
    )
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    assert "John Doe" in texts
    assert "ניסיון תעסוקתי" in texts
    assert "Python, Docker, Kubernetes" in texts


def test_06_markdown_asterisks_stripped():
    """Маркдаун-звёздочки (**bold**, *italic*) из improved_resume должны
    убираться — как и в существующем fallback-пути legacy_improve_docx."""
    text = "###ITEM_001###\n**Bold Title**\n\n###ITEM_002###\n*italic note*"
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    assert "Bold Title" in texts
    assert "italic note" in texts
    for t in texts:
        assert "**" not in t and not t.startswith("*")


def test_07_empty_lines_do_not_crash():
    """Пустые строки (двойной перенос между блоками) не должны ронять генерацию."""
    text = "###ITEM_001###\nLine one\n\n\n###ITEM_002###\nLine two"
    buf = mr._generate_odt(text)
    doc = load(buf)  # не должно бросить исключение
    assert doc is not None


# ===========================================================================
# Cycle O2 — HTTP-роут POST /api/improve/odt
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
    (см. tests/test_credits_pool.py)."""
    from app.services.auth_service import AuthService

    result = AuthService.register(email, 'somepassword123')
    user = result['user']
    with client.session_transaction() as sess:
        sess['user_id'] = user.id
    return user


_ODT_TEST_TEXT = '###ITEM_001###\nJohn Doe\n\n###ITEM_002###\nSoftware Engineer'


def test_08_odt_route_requires_login_returns_401(client_app):
    """Незалогиненный, неадминский запрос -> 401, без каких-либо кредитов."""
    app = client_app
    client = app.test_client()

    resp = client.post('/api/improve/odt', data={'improved_resume': _ODT_TEST_TEXT})
    assert resp.status_code == 401
    assert resp.get_json()['success'] is False


def test_09_odt_route_returns_valid_odt_when_credits_available(client_app):
    """Залогиненный пользователь с credits_remaining() > 0 -> 200, корректный ODT."""
    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'odt-route-ok-test@example.com')

    sub_before = user.get_active_subscription()
    assert sub_before.credits_remaining() == 2

    resp = client.post('/api/improve/odt', data={'improved_resume': _ODT_TEST_TEXT})

    assert resp.status_code == 200
    assert 'opendocument.text' in resp.content_type

    # Тело ответа — валидный ODT: должен загрузиться через odf.opendocument.load
    import io
    doc = load(io.BytesIO(resp.data))
    assert doc is not None
    texts = [str(p) for p in doc.getElementsByType(P)]
    assert 'John Doe' in texts
    assert 'Software Engineer' in texts


def test_10_odt_route_blocks_and_does_not_double_charge_when_credits_exhausted(client_app):
    """
    Залогиненный пользователь с credits_remaining() == 0 -> 403, и credits_used
    не меняется после запроса (прямая проверка "без повторного списания" —
    списание кредита происходит только в /api/improve, не здесь).
    """
    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'odt-route-exhausted-test@example.com')

    sub = user.get_active_subscription()
    sub.credits_used = sub.credits_granted  # исчерпать пул напрямую, без вызова /api/improve
    from app import db
    db.session.commit()

    assert sub.credits_remaining() == 0
    credits_used_before = sub.credits_used

    resp = client.post('/api/improve/odt', data={'improved_resume': _ODT_TEST_TEXT})

    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'No credits remaining. Buy a credit pack to continue.'

    sub_after = user.get_active_subscription()
    assert sub_after.credits_used == credits_used_before
