"""
Cycle T2: внутренний текст исключений (путь к файлу, адрес провайдера) не должен
попадать в ответы маршрутов. Без сети: всё внутреннее замокано.

Запуск: FLASK_ENV=testing python -m pytest tests/test_error_leaks.py -q
"""
import os
import sys
import io
import json
import importlib
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import config as config_module
importlib.reload(config_module)

from app import create_app

LEAK_PATH = "/home/claude/resumeai/app/secret_module.py"
LEAK_URL = "api.groq.com/openai/v1/chat/completions"
LEAK_TEXT = f"boom at {LEAK_PATH} while calling https://{LEAK_URL}"
FORBIDDEN = ("secret_module", "/home/claude", "api.groq.com", "groq")
RESUME = "Experienced engineer. Managed a team of five people and built internal tools for the company."


@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['admin'] = 'admin'
        yield c


def _assert_clean(resp, status):
    assert resp.status_code == status
    raw = json.dumps(resp.get_json())
    for bad in FORBIDDEN:
        assert bad not in raw, f"утечка {bad!r}: {raw}"


def _txt():
    return (io.BytesIO(RESUME.encode()), 'resume.txt')


def test_analyze_valueerror_not_leaked(client):
    with patch('app.services.openrouter_service.OpenRouterService.analyze_resume',
               side_effect=ValueError(LEAK_TEXT)):
        r = client.post('/api/analyze', data={'file': _txt()}, content_type='multipart/form-data')
    _assert_clean(r, 400)


def test_analyze_exception_not_leaked(client):
    with patch('app.services.openrouter_service.OpenRouterService.analyze_resume',
               side_effect=RuntimeError(LEAK_TEXT)):
        r = client.post('/api/analyze', data={'file': _txt()}, content_type='multipart/form-data')
    _assert_clean(r, 500)


def test_improve_exception_not_leaked(client):
    with patch('app.missing_routes4._run_improve_pipeline', side_effect=RuntimeError(LEAK_TEXT)):
        r = client.post('/api/improve', data={'file': _txt()}, content_type='multipart/form-data')
    _assert_clean(r, 500)


def test_improve_docx_exception_not_leaked(client):
    with patch('app.missing_routes4._apply_improved_text_to_docx', side_effect=RuntimeError(LEAK_TEXT)):
        r = client.post('/api/improve/docx',
                        data={'original_file': (io.BytesIO(b'x'), 'a.docx'),
                              'improved_resume': '###ITEM_001:PLAIN###\nHello'},
                        content_type='multipart/form-data')
    _assert_clean(r, 500)


def test_admin_improve_exception_not_leaked(client):
    with patch('app.missing_routes4._run_improve_pipeline', side_effect=RuntimeError(LEAK_TEXT)):
        r = client.post('/api/admin/improve', data={'file': _txt()}, content_type='multipart/form-data')
    _assert_clean(r, 500)


def test_admin_improve_docx_exception_not_leaked(client):
    with patch('app.missing_routes4._apply_improved_text_to_docx', side_effect=RuntimeError(LEAK_TEXT)):
        r = client.post('/api/admin/improve/docx',
                        data={'original_file': (io.BytesIO(b'x'), 'a.docx'),
                              'improved_resume': '###ITEM_001:PLAIN###\nHello'},
                        content_type='multipart/form-data')
    _assert_clean(r, 500)


def test_own_safe_message_still_shown(client):
    """Собственное безопасное сообщение сервиса проходит без изменений."""
    with patch('app.services.openrouter_service.OpenRouterService.analyze_resume',
               side_effect=ValueError("Cannot read DOCX file. Please check the file is not corrupted.")):
        r = client.post('/api/analyze', data={'file': _txt()}, content_type='multipart/form-data')
    assert r.status_code == 400
    assert r.get_json()['error'] == "Cannot read DOCX file. Please check the file is not corrupted."
