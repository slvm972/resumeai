"""
Cycle C3: почему creative_v2 «падает без единого вызова модели» на резюме инженера.

Вывод расследования (подробно в отчёте цикла): исключение не доходит до except в legacy_improve. Оно
перехватывается ВНУТРИ app.rewrite_creative.creative_rewrite (и ещё раз в app.creative_bridge.creative_improve),
которые возвращают status="error" с rounds=0, calls=0. Эти нули константа результата-ошибки, а не счётчик:
ошибка любого запроса к модели (HTTP 4xx/5xx, второй 429, таймаут) даёт те же rounds=0 calls=0, даже если
часть запросов уже была сделана. Входной текст инженера сам по себе Creative не роняет (тесты ниже).
Без сети: requests.post и call_groq подменены. Запуск: FLASK_ENV=testing python -m pytest tests/test_creative_c3.py -q
"""
import io
import logging
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import importlib
import config as config_module
importlib.reload(config_module)

from app import create_app, creative_bridge, rewrite_creative

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'creative_c1')
ORIGINALS = ['resume_russian_engineer.docx', 'resume_ua.docx', 'resume_english_designer.docx']
CFG = {'GROQ_API_KEY': 'K', 'CREATIVE_V2_ENABLED': '1'}


def orig(name):
    with open(os.path.join(DATA, name), 'rb') as f:
        return f.read()


class _Resp:
    status_code = 200
    text = ''

    def __init__(self, content):
        self._c = content

    def json(self):
        return {"choices": [{"message": {"content": self._c}}], "usage": {"total_tokens": 1}}


def echo_post(url, headers=None, json=None, timeout=None, **kw):
    """Идеальная «модель»: возвращает текст резюме из пользовательского сообщения без правок."""
    return _Resp(json['messages'][-1]['content'])


# --- ввод инженера не роняет Creative ---------------------------------------------------------------

@pytest.mark.parametrize('name', ORIGINALS)
def test_creative_improve_works_on_all_three_originals(name):
    with patch('requests.post', side_effect=echo_post) as post:
        r = creative_bridge.creative_improve(orig(name), name, None, CFG)
    assert r['attempted'] and r['status'] in ('clean', 'repaired') and r['acceptable']
    assert r['calls'] >= 2 and post.call_count == r['calls']
    assert r['improved_resume'].strip()


def test_source_text_of_engineer_is_extracted_and_long_enough():
    text = creative_bridge._source_text(orig('resume_russian_engineer.docx'), 'r.docx', None)
    assert len(text) > 1000 and 'Московский' in text


# --- механизм: ошибка запроса к модели -> status=error, rounds=0, calls=0 ------------------------------

def test_first_request_error_gives_error_result_with_zero_counters():
    boom = RuntimeError('Groq API 500: upstream error')
    with patch('app.rewrite_creative.call_groq', side_effect=boom) as call:
        r = creative_bridge.creative_improve(orig('resume_russian_engineer.docx'), 'r.docx', None, CFG)
    assert call.call_count == 1                       # запрос к модели БЫЛ
    assert r['attempted'] and r['status'] == 'error' and not r['acceptable']
    assert r['rounds'] == 0 and r['calls'] == 0       # но счётчик показывает 0


def test_error_after_successful_calls_also_reports_zero_calls():
    calls = {'n': 0}

    def flaky(api_key, model, messages, **kw):
        calls['n'] += 1
        if calls['n'] <= 2:      # 1: переписывание (потеряно число -> repair), 2: судья
            text = messages[-1]['content'].replace('2012', '1999') if calls['n'] == 1 else messages[-1]['content']
            return {'text': text, 'usage': {}, 'seconds': 0.1, 'dropped_params': [], 'retries_429': 0}
        raise RuntimeError('Groq API 429: rate limit')     # 3: раунд repair
    with patch('app.rewrite_creative.call_groq', side_effect=flaky):
        r = creative_bridge.creative_improve(orig('resume_russian_engineer.docx'), 'r.docx', None, CFG)
    assert calls['n'] == 3 and r['status'] == 'error' and r['calls'] == 0   # 2 запроса успели, счётчик 0


def test_creative_rewrite_swallows_exception_and_hides_type():
    with patch('app.rewrite_creative.rewrite_with_repair', side_effect=ValueError('x')):
        r = rewrite_creative.creative_rewrite('Some resume text that is long enough', 'K')
    assert r['status'] == 'error' and r['calls'] == 0 and 'ValueError' not in str(r)


# --- журнал сервера ----------------------------------------------------------------------------------

@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['CREATIVE_V2_ENABLED'] = 'true'
    app.config['GROQ_API_KEY'] = 'K-test'
    with app.app_context():
        yield app


def _improve(app, name, mode='creative'):
    with app.test_client() as c:
        with c.session_transaction() as s:
            s['admin'] = 'admin'
        return c.post('/api/improve', data={'file': (io.BytesIO(orig(name)), name), 'creativity_mode': mode},
                      content_type='multipart/form-data')


@pytest.mark.parametrize('name', ORIGINALS)
def test_route_creative_still_works_for_all_originals(app_ctx, name):
    with patch('requests.post', side_effect=echo_post):
        r = _improve(app_ctx, name)
    body = r.get_json()
    assert r.status_code == 200 and body['engine'] == 'creative_v2' and body['item_ids'] == []


def test_error_result_is_logged_with_reason_text(app_ctx, caplog):
    boom = RuntimeError('Groq API 500: upstream error')
    with patch('app.rewrite_creative.call_groq', side_effect=boom), patch('requests.post', side_effect=echo_post):
        with caplog.at_level(logging.INFO):
            r = _improve(app_ctx, 'resume_russian_engineer.docx')
    body = r.get_json()
    assert r.status_code == 200 and body['engine'] == 'precise' and body['creative_fallback'] is True
    assert body['creative_status'] == 'error'                     # ответ клиенту не изменился
    assert 'creative_v2 failed: status=error' in caplog.text
    assert 'not reliable' in caplog.text


def test_exception_in_bridge_is_logged_with_type_and_traceback_without_message(app_ctx, caplog):
    secret = 'Московский государственный технический университет'      # текст резюме
    with patch('app.creative_bridge.creative_improve', side_effect=ValueError(secret + ' user data')), \
         patch('requests.post', side_effect=echo_post):
        with caplog.at_level(logging.INFO):
            r = _improve(app_ctx, 'resume_russian_engineer.docx')
    body = r.get_json()
    assert r.status_code == 200 and body['engine'] == 'precise' and body['creative_status'] == 'error'
    assert 'creative_v2 raised ValueError' in caplog.text
    assert 'creative_improve' in caplog.text and 'File "' in caplog.text      # стек есть
    assert secret not in caplog.text and 'user data' not in caplog.text       # текста исключения/резюме нет
    assert 'K-test' not in caplog.text
