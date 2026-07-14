"""
Тесты на утечку внутренних деталей исключений — 5 сценариев.
Проверяют, что клиент получает только общее сообщение об ошибке,
а не str(e) замоканного внутреннего исключения.

Затронутые места в app/__init__.py:
    1. legacy_admin_analyze  (/api/admin/analyze)
    2. legacy_analyze        (/api/analyze)
    3. legacy_improve        (/api/improve)
    4. legacy_improve_docx   (/api/improve/docx)
    5. _extract_text_from_request (косвенно, через /api/analyze + .docx)

Запуск: python -m pytest tests/test_error_handling.py -v
"""
import os
import sys
import io
import json
import importlib
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')

# Безопасные тестовые значения ДО импорта config/app — иначе сработает
# RuntimeError guard (SECRET_KEY/JWT_SECRET_KEY insecure defaults) или
# приложение решит что это production.
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import config as config_module
importlib.reload(config_module)

from app import create_app

# Маркер, который НЕ должен появляться ни в одном ответе клиенту
SECRET_LEAK = "secret_db_path_leaked_TESTMARKER_xyz123"


@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _login_admin(client):
    """Поставить admin-сессию напрямую, минуя реальный логин."""
    with client.session_transaction() as sess:
        sess['admin'] = 'admin'


def _assert_no_leak(resp, expected_status, expected_error=None):
    assert resp.status_code == expected_status
    body = resp.get_json()
    assert body is not None
    raw = json.dumps(body)
    assert SECRET_LEAK not in raw, f"Внутренняя деталь исключения утекла в ответ: {raw}"
    if expected_error is not None:
        assert body.get('error') == expected_error
    return body


# ===========================================================================
# 1. legacy_admin_analyze — /api/admin/analyze
# ===========================================================================

def test_admin_analyze_hides_exception_detail(client):
    _login_admin(client)
    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        side_effect=Exception(SECRET_LEAK),
    ):
        resp = client.post(
            '/api/admin/analyze',
            data={'resume_text': 'A' * 30},
            content_type='multipart/form-data',
        )
    _assert_no_leak(resp, 500, 'Internal server error. Please try again.')


# ===========================================================================
# 2. legacy_analyze — /api/analyze
# ===========================================================================

def test_analyze_hides_exception_detail(client):
    _login_admin(client)
    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        side_effect=Exception(SECRET_LEAK),
    ):
        resp = client.post(
            '/api/analyze',
            data={'resume_text': 'A' * 30},
            content_type='multipart/form-data',
        )
    _assert_no_leak(resp, 500, 'Internal server error. Please try again.')


# ===========================================================================
# 3. legacy_improve — /api/improve
# ===========================================================================

def test_improve_hides_exception_detail(client):
    _login_admin(client)
    with patch(
        'app.missing_routes4._run_improve_pipeline',
        side_effect=Exception(SECRET_LEAK),
    ):
        resp = client.post(
            '/api/improve',
            json={'resume_text': 'A' * 30},
        )
    _assert_no_leak(resp, 500, 'Internal server error. Please try again.')


# ===========================================================================
# 4. legacy_improve_docx — /api/improve/docx
# ===========================================================================

def test_improve_docx_hides_exception_detail(client):
    _login_admin(client)
    with patch(
        'app.missing_routes4._apply_improved_text_to_docx',
        side_effect=Exception(SECRET_LEAK),
    ):
        data = {
            'improved_resume': '###ITEM_001###\nSome improved text',
            'original_file': (io.BytesIO(b'fake docx bytes, not a real zip'), 'resume.docx'),
        }
        resp = client.post(
            '/api/improve/docx',
            data=data,
            content_type='multipart/form-data',
        )
    _assert_no_leak(resp, 500, 'Internal server error. Please try again.')


# ===========================================================================
# 5. _extract_text_from_request — косвенно через /api/analyze + .docx
# ===========================================================================

def test_extract_text_from_request_hides_exception_detail(client):
    _login_admin(client)
    with patch('docx.Document', side_effect=Exception(SECRET_LEAK)):
        data = {
            'file': (io.BytesIO(b'not a real docx zip content'), 'resume.docx'),
        }
        resp = client.post(
            '/api/analyze',
            data=data,
            content_type='multipart/form-data',
        )
    # _extract_text_from_request сам оборачивает исключение в безопасный
    # ValueError, который legacy_analyze прокидывает клиенту как есть (400) —
    # сообщение должно быть общим, без текста замоканного исключения.
    _assert_no_leak(
        resp, 400,
        'Cannot read DOCX file. Please check the file is not corrupted.',
    )
