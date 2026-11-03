"""
Cycle E1: пользователь не видит сырой текст ошибки Groq, а резервная модель с малым лимитом выходных
токенов не ломает запрос: при "Request too large" на запасной модели повтор идёт на основной.
Без сети: requests.post и time.sleep заменены заглушками.
Запуск: FLASK_ENV=testing python -m pytest tests/test_llm_errors.py -q
"""
import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import importlib
import config as config_module
importlib.reload(config_module)

from app import create_app
from app.utils import llm_errors as le

# Реальное сообщение с вашего скриншота
RAW_TOO_LARGE = ("Request too large for model `qwen/qwen3.8-27b` in organization `org_01kr49ergbfzrvdb5463as6vke` "
                 "service tier `on_demand` on output tokens per minute (OTPM): Limit 1000, Requested 4543. "
                 "The request's expected output tokens exceed the enforced limit; reduce max_tokens (or the "
                 "request's expected output) and try again. Need more tokens? Upgrade to Dev Tier today at "
                 "https://console.groq.com/settings/billing")
RAW_429 = ("Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01kr49ergbfzrvdb5463as6vke` "
           "service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 7800, Requested 1500. "
           "Please try again in 1.5s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com")
PRIMARY = "openai/gpt-oss-120b"
FALLBACK = "qwen/qwen3.8-27b"
_FORBIDDEN_IN_USER_TEXT = ("qwen", "gpt-oss", "org_", "groq", "console", "on_demand", "OTPM", "max_tokens", "http")


# ---------------------------------------------------------------------------
# sanitize_error
# ---------------------------------------------------------------------------

def _clean(text):
    low = text.lower()
    return all(w.lower() not in low for w in _FORBIDDEN_IN_USER_TEXT)


def test_01_screenshot_message_becomes_busy_and_leaks_nothing():
    out = le.sanitize_error(RAW_TOO_LARGE, 413)
    assert out == le.MSG_BUSY and _clean(out)
    assert le.sanitize_error(RAW_TOO_LARGE) == le.MSG_BUSY          # и без статуса, по тексту


def test_02_rate_limit_messages():
    assert le.sanitize_error(RAW_429, 429) == le.MSG_BUSY
    assert le.sanitize_error("Rate limit reached. Please try again in 5s", None) == le.MSG_BUSY


def test_03_auth_and_server_errors_are_unavailable():
    assert le.sanitize_error("Invalid API Key", 401) == le.MSG_UNAVAILABLE
    assert le.sanitize_error("GROQ_API_KEY not configured") == le.MSG_UNAVAILABLE
    assert le.sanitize_error("Internal error", 503) == le.MSG_UNAVAILABLE


def test_04_own_service_messages_pass_through_unchanged():
    for msg in ("Empty response from the AI model. Please try again.",
                "Cannot read ODT file. Please check the file is not corrupted.",
                "Resume text too short", "File is too large or malformed.",
                "Request timeout. Please try again."):
        assert le.sanitize_error(msg) == msg


def test_05_unknown_provider_text_with_urls_or_models_is_generic():
    out = le.sanitize_error("Failed for model `llama-3.1-8b` see https://console.groq.com/x", 400)
    assert out == le.MSG_GENERIC and _clean(out)
    assert le.sanitize_error("", 400) == le.MSG_GENERIC
    assert le.sanitize_error(None) == le.MSG_GENERIC


def test_06_connection_exception_text_is_not_shown():
    exc_text = "HTTPSConnectionPool(host='api.groq.com', port=443): Max retries exceeded with url: /openai/v1/chat"
    out = le.sanitize_error(exc_text)
    assert _clean(out) and "HTTPSConnectionPool" not in out


def test_07_helpers():
    assert le.is_request_too_large(413, "") and le.is_request_too_large(None, RAW_TOO_LARGE)
    assert not le.is_request_too_large(429, RAW_429)
    assert le.is_rate_limit(429, "") and le.is_rate_limit(None, RAW_429)

    class R:
        status_code = 413
        text = "plain text body"

        def json(self):
            raise ValueError("no json")
    assert le.response_error_message(R()) == "plain text body"


# ---------------------------------------------------------------------------
# Конвейер: основная модель 429, запасная "Request too large"
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, message=None, content=None):
        self.status_code = status
        self._message = message
        self._content = content
        self.text = message or ""

    def json(self):
        if self.status_code == 200:
            return {"choices": [{"message": {"content": self._content}}], "usage": {"total_tokens": 5}}
        return {"error": {"message": self._message}}


def _echo(body):
    user = ""
    for m in body.get("messages", []):
        if m.get("role") == "user":
            user = m.get("content", "")
    start, end = user.find("###ITEM_"), user.rfind("OUTPUT (")
    return user[start:end].strip() if start >= 0 else "x"


class _FakeGroq:
    """scenario: 'A' основная 429 один раз, запасная 413, дальше основная работает;
                 'B' основная всегда 429, запасная всегда 413;
                 'C' основная 429, запасная 429 (обычный лимит, не размер)."""
    def __init__(self, scenario):
        self.scenario = scenario
        self.calls = []
        self.primary_429_sent = False

    def post(self, url, headers=None, json=None, timeout=None):
        model = json.get("model")
        self.calls.append(model)
        if self.scenario == "B":
            return _Resp(429, RAW_429) if model == PRIMARY else _Resp(413, RAW_TOO_LARGE)
        if self.scenario == "C":
            return _Resp(429, RAW_429) if model == PRIMARY and len(self.calls) == 1 else (
                _Resp(429, RAW_429.replace("1.5s", "2.5s")) if model == FALLBACK else _Resp(200, content=_echo(json)))
        # scenario A
        if model == PRIMARY and not self.primary_429_sent:
            self.primary_429_sent = True
            return _Resp(429, RAW_429)
        if model == FALLBACK:
            return _Resp(413, RAW_TOO_LARGE)
        return _Resp(200, content=_echo(json))


_RESUME = ("Anna Smith\nanna@example.com\nExperience\nIT Support, 2019-2022\n"
           "Managed a team of 5 engineers and installed office network equipment\n"
           "Developed internal tools and maintained servers for the whole company")


@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['GROQ_API_KEY'] = 'test-key'
    with app.app_context():
        yield app


def _admin_client(app):
    from app.services.auth_service import AuthService
    user = AuthService.register('e1-%s@example.com' % uuid.uuid4().hex[:10], 'somepassword123')['user']
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = user.id
        sess['admin'] = 'admin'
    return client


def _improve(app, monkeypatch, scenario):
    fake = _FakeGroq(scenario)
    sleeps = []
    monkeypatch.setattr("requests.post", fake.post)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    client = _admin_client(app)
    resp = client.post("/api/admin/improve", json={"resume_text": _RESUME})
    return resp, fake, sleeps


def test_08_fallback_too_large_retries_primary_and_succeeds(app_ctx, monkeypatch):
    resp, fake, sleeps = _improve(app_ctx, monkeypatch, "A")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["success"] is True
    first_three = fake.calls[:3]
    assert first_three == [PRIMARY, FALLBACK, PRIMARY]      # основная -> запасная (413) -> снова основная
    assert 1.5 in sleeps                                      # ждали по подсказке ОСНОВНОЙ модели


def test_09_all_providers_failing_gives_friendly_message_without_leaks(app_ctx, monkeypatch):
    resp, fake, sleeps = _improve(app_ctx, monkeypatch, "B")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["success"] is False and body["error"] == le.MSG_BUSY
    assert _clean(body["error"])
    assert "org_" not in resp.get_data(as_text=True)


def test_10_plain_429_keeps_old_behaviour_final_retry_on_fallback(app_ctx, monkeypatch):
    resp, fake, sleeps = _improve(app_ctx, monkeypatch, "C")
    assert fake.calls[:3] == [PRIMARY, FALLBACK, FALLBACK]   # прежняя логика для обычного 429
    assert 2.5 in sleeps


# ---------------------------------------------------------------------------
# Анализ (openrouter_service)
# ---------------------------------------------------------------------------

def test_11_analysis_error_is_sanitized(app_ctx, monkeypatch):
    from app.services.openrouter_service import OpenRouterService

    class U:
        id = 0
    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp(413, RAW_TOO_LARGE))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    res = OpenRouterService.analyze_resume(U(), "A reasonably long resume text " * 5)
    assert res["success"] is False and res["error"] == le.MSG_BUSY and _clean(res["error"])


def test_12_analysis_network_exception_is_sanitized(app_ctx, monkeypatch):
    from app.services.openrouter_service import OpenRouterService

    class U:
        id = 0

    def boom(*a, **k):
        raise RuntimeError("HTTPSConnectionPool(host='api.groq.com', port=443): Max retries exceeded")
    monkeypatch.setattr("requests.post", boom)
    res = OpenRouterService.analyze_resume(U(), "A reasonably long resume text " * 5)
    assert res["success"] is False and _clean(res["error"])
