"""
Cycle E2: ответ Groq с НЕ-JSON телом (страница шлюза, пустой ответ) не показывает пользователю текст
разбора ("Expecting value: line 2 column ..."), а при временном сбое делается одна повторная попытка.
Без сети: requests.post и time.sleep заменены заглушками.
Запуск: FLASK_ENV=testing python -m pytest tests/test_groq_nonjson.py -q
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
from app.utils.llm_errors import MSG_UNAVAILABLE, MSG_BUSY

RESUME = "Experienced engineer. Managed a team of five people and built internal tools for the company."
FORBIDDEN = ("Expecting value", "line 2 column", "line 1 column", "JSONDecodeError", "Traceback")
GOOD_ANALYSIS = {
    "overall_score": 70, "ats_score": 65, "formatting": 60, "content": 75,
    "summary": "Solid resume.", "strengths": ["a", "b", "c"], "improvements": ["x", "y", "z"],
    "key_skills": ["Python"],
}


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self._body = body

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)      # как настоящий requests: бросает JSONDecodeError
        return self._body


def _ok_resp():
    return FakeResp(200, {"choices": [{"message": {"content": json.dumps(GOOD_ANALYSIS)}}],
                          "usage": {"total_tokens": 42}})


@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['admin'] = 'admin'
        yield c


def _analyze(client, responses):
    """Вызвать /api/analyze, подставив последовательность ответов Groq. Возвращает (ответ, число запросов)."""
    seq = list(responses)
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return seq.pop(0) if len(seq) > 1 else seq[0]

    with patch('app.services.openrouter_service.requests.post', side_effect=fake_post), \
         patch('app.services.openrouter_service.time.sleep') as slept:
        r = client.post('/api/analyze', data={'file': (io.BytesIO(RESUME.encode()), 'resume.txt')},
                        content_type='multipart/form-data')
    return r, calls["n"], slept


def _assert_clean(r):
    raw = json.dumps(r.get_json())
    for bad in FORBIDDEN:
        assert bad not in raw, f"утечка {bad!r}: {raw}"


def test_a_gateway_502_html_body(client):
    r, n, slept = _analyze(client, [FakeResp(502, "<html><body>Bad Gateway</body></html>")])
    _assert_clean(r)
    assert r.status_code == 500 and r.get_json()['error'] == MSG_UNAVAILABLE
    assert n == 2 and slept.called          # один повтор, не больше


def test_b_status_200_with_empty_body(client):
    r, n, _ = _analyze(client, [FakeResp(200, "")])
    _assert_clean(r)
    assert r.status_code == 500 and r.get_json()['error'] == MSG_UNAVAILABLE
    assert n == 2


def test_c_429_with_non_json_body(client):
    r, n, _ = _analyze(client, [FakeResp(429, "<html>Too Many Requests</html>")])
    _assert_clean(r)
    assert r.status_code == 500 and r.get_json()['error'] == MSG_BUSY
    assert n == 2                           # повтор после 429 как раньше, без лишнего


def test_d_retry_succeeds_after_first_failure(client):
    r, n, slept = _analyze(client, [FakeResp(502, "<html>Bad Gateway</html>"), _ok_resp()])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['overall_score'] == 70
    assert n == 2 and slept.called


def test_e_text_call_non_json_200_not_leaked():
    app = create_app('testing')
    with app.app_context():
        from app.services import openrouter_service as svc
        with patch('app.services.openrouter_service.requests.post', return_value=FakeResp(200, "")):
            res = svc._call_groq_text("improve this")
    assert res['success'] is False and res['error'] == MSG_UNAVAILABLE


def test_f_safe_json_never_raises():
    from app.services.openrouter_service import _safe_json
    assert _safe_json(FakeResp(502, "<html>")) == {}
    assert _safe_json(FakeResp(200, "")) == {}
    assert _safe_json(FakeResp(200, {"a": 1})) == {"a": 1}
    assert _safe_json(FakeResp(200, "[1, 2]")) == {}       # не dict -> {}
