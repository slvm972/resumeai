"""
Тесты для app/services/resume_guardrail.py.

Отдельный файл, tests/test_missing_routes4.py не трогается и не
запускается вместе с этим файлом.

resume_guardrail.py грузится напрямую по пути файла (importlib), а не
через `import app.services.resume_guardrail`, чтобы тест был
изолированным и не тянул за собой app/__init__.py (Flask, SQLAlchemy,
JWT, CORS, Mail). Когда модуль подключится к _run_improve_pipeline()
на Этапе 8, он будет импортироваться штатно через пакет `app.services`.

Этап 4: GroqGuardrailProvider теперь делает настоящий HTTP-запрос
(requests.post) — ни один тест не должен реально стучаться в сеть.
Автоматический фикстура _block_real_network ниже патчит
grd.requests.post заглушкой, которая роняет тест при вызове без
явного переопределения; тесты, которым нужен ответ "от Groq",
переопределяют его своим моком через monkeypatch.
"""

import importlib.util
import os
import sys
import types

import pytest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "services", "resume_guardrail.py",
)

_spec = importlib.util.spec_from_file_location("resume_guardrail", _MODULE_PATH)
grd = importlib.util.module_from_spec(_spec)
sys.modules["resume_guardrail"] = grd
_spec.loader.exec_module(grd)


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch):
    """
    Все тесты в этом файле по умолчанию не должны стучаться в реальную
    сеть. Тест, которому нужен ответ "от Groq", переопределяет
    grd.requests.post своим моком поверх этого guard'а (monkeypatch
    в рамках одного теста — последний setattr побеждает).
    """
    def _forbidden(*args, **kwargs):
        raise AssertionError("Unexpected real network call via requests.post in a test")
    monkeypatch.setattr(grd.requests, "post", _forbidden)
    yield


class _FakeResponse:
    """Минимальная имитация requests.Response — только то, что использует адаптер."""

    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data


def _groq_ok_response(pass_value=True, findings=None, tokens=0):
    return _FakeResponse(200, {
        "choices": [{"message": {"content": json_str_dump(pass_value, findings)}}],
        "usage": {"total_tokens": tokens},
    })


def json_str_dump(pass_value, findings):
    import json
    return json.dumps({"pass": pass_value, "findings": findings or []})


# ===========================================================================
# GroqGuardrailProvider — успешный ответ (мок requests.post)
# ===========================================================================

def test_01_groq_returns_expected_shape_on_success(monkeypatch):
    """Успешный ответ Groq (замокан) даёт ту же итоговую структуру,
    что и заглушка Этапа 3."""
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _groq_ok_response(pass_value=True, findings=[], tokens=0),
    )
    result = grd.GuardrailService.run_check(
        original_text="original resume text",
        improved_text="improved resume text",
        language="English",
        api_key="fake-key",
        provider="groq",
    )
    assert result == {
        "success": True,
        "pass": True,
        "findings": [],
        "guardrail_rejected": False,
        "tokens_used": 0,
        "error": None,
    }


def test_02_groq_is_default_provider(monkeypatch):
    """provider по умолчанию — 'groq', указывать явно не обязательно."""
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _groq_ok_response(pass_value=True, findings=[]),
    )
    result = grd.GuardrailService.run_check(
        original_text="a", improved_text="b", language="English", api_key="fake-key",
    )
    assert result["success"] is True
    assert result["pass"] is True
    assert result["guardrail_rejected"] is False


def test_02b_groq_propagates_tokens_used(monkeypatch):
    """tokens_used из usage.total_tokens ответа Groq доходит до вызывающего кода."""
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _groq_ok_response(pass_value=True, findings=[], tokens=137),
    )
    result = grd.GuardrailService.run_check("a", "b", "English", "fake-key", provider="groq")
    assert result["tokens_used"] == 137


def test_02c_groq_wraps_high_severity_finding_into_guardrail_rejected(monkeypatch):
    """Реальный (замоканный) ответ Groq с findings severity='high' тоже
    должен взводить guardrail_rejected через facade."""
    finding = {
        "type": "role_escalation",
        "severity": "high",
        "original_excerpt": "participated in the project",
        "improved_excerpt": "led the project",
        "explanation": "escalation",
    }
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _groq_ok_response(pass_value=False, findings=[finding], tokens=20),
    )
    result = grd.GuardrailService.run_check("a", "b", "English", "fake-key", provider="groq")
    assert result["success"] is True
    assert result["pass"] is False
    assert result["guardrail_rejected"] is True
    assert result["findings"] == [finding]


# ===========================================================================
# Пустой / None api_key — fail fast, БЕЗ сетевого запроса
# ===========================================================================

def test_03_empty_api_key_fails_without_network_call():
    """api_key=None → success=False, requests.post вообще не вызывается
    (см. _block_real_network — если бы вызов случился, тест упал бы
    с AssertionError из заглушки-guard'а, а не дошёл до этих проверок)."""
    result = grd.GuardrailService.run_check(
        original_text="a", improved_text="b", language="English",
        api_key=None, provider="groq", fallback_on_failure=False,
    )
    assert result["success"] is False
    assert result["pass"] is None
    assert result["findings"] == []
    assert result["guardrail_rejected"] is False
    assert result["tokens_used"] == 0
    assert "GROQ_API_KEY" in result["error"]


def test_03b_blank_string_api_key_also_fails_without_network_call():
    result = grd.GuardrailService.run_check(
        original_text="a", improved_text="b", language="English",
        api_key="", provider="groq", fallback_on_failure=False,
    )
    assert result["success"] is False
    assert "GROQ_API_KEY" in result["error"]


# ===========================================================================
# Сетевой timeout
# ===========================================================================

def test_03c_network_timeout(monkeypatch):
    import requests as real_requests

    def _raise_timeout(*a, **kw):
        raise real_requests.exceptions.Timeout("simulated timeout")

    monkeypatch.setattr(grd.requests, "post", _raise_timeout)
    result = grd.GuardrailService.run_check(
        original_text="a", improved_text="b", language="English",
        api_key="fake-key", provider="groq", fallback_on_failure=False,
    )
    assert result["success"] is False
    assert result["pass"] is None
    assert result["findings"] == []
    assert "timeout" in result["error"].lower()


# ===========================================================================
# HTTP error / non-200
# ===========================================================================

def test_03d_http_error_non_200(monkeypatch):
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _FakeResponse(500, {"error": {"message": "internal server error"}}),
    )
    result = grd.GuardrailService.run_check(
        original_text="a", improved_text="b", language="English",
        api_key="fake-key", provider="groq", fallback_on_failure=False,
    )
    assert result["success"] is False
    assert result["pass"] is None
    assert result["error"] == "internal server error"


def test_03e_http_error_without_parsable_body_still_gives_clean_error(monkeypatch):
    """Даже если тело ошибки не в ожидаемой Groq-форме, error всё равно
    непустая строка, без падения."""
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _FakeResponse(503, {}),
    )
    result = grd.GuardrailService.run_check("a", "b", "English", "fake-key", provider="groq", fallback_on_failure=False)
    assert result["success"] is False
    assert "503" in result["error"]


# ===========================================================================
# 429 → трёхступенчатый паттерн: primary -> fallback (без ожидания) ->
# fallback (после sleep(Retry-After)) -> максимум 3 попытки
# ===========================================================================

def test_03f_first_429_switches_to_fallback_model_no_sleep(monkeypatch):
    """Шаги 1->2: первый 429 (на primary-модели) -> переключение на
    fallback-модель -> повтор БЕЗ ожидания -> успех. Ровно 2 запроса,
    time.sleep вообще не вызывается."""
    calls = {"n": 0}
    models_used = []
    sleep_calls = {"n": 0}

    def _post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        models_used.append(json.get("model"))
        if calls["n"] == 1:
            return _FakeResponse(429, {"error": {"message": "Please try again in 0.01s."}})
        return _groq_ok_response(pass_value=True, findings=[], tokens=5)

    monkeypatch.setattr(grd.requests, "post", _post)
    monkeypatch.setattr(grd.time, "sleep", lambda s: sleep_calls.__setitem__("n", sleep_calls["n"] + 1))

    result = grd.GuardrailService.run_check("a", "b", "English", "fake-key", provider="groq")

    assert calls["n"] == 2
    assert sleep_calls["n"] == 0  # переключение модели — без ожидания
    assert models_used == [grd.GROQ_GUARDRAIL_MODEL, grd.GROQ_GUARDRAIL_FALLBACK_MODEL]
    assert result["success"] is True
    assert result["tokens_used"] == 5


def test_03g_429_all_three_attempts_gives_clean_error_no_infinite_loop(monkeypatch):
    """Все три попытки (primary, fallback, fallback) вернули 429 —
    success=False, без зацикливания. Ровно 3 запроса, ровно один
    sleep (перед третьей, финальной попыткой)."""
    calls = {"n": 0}
    models_used = []
    sleep_calls = {"n": 0}

    def _post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        models_used.append(json.get("model"))
        return _FakeResponse(429, {"error": {"message": "Please try again in 0.01s."}})

    monkeypatch.setattr(grd.requests, "post", _post)
    monkeypatch.setattr(grd.time, "sleep", lambda s: sleep_calls.__setitem__("n", sleep_calls["n"] + 1))

    result = grd.GuardrailService.run_check(
        "a", "b", "English", "fake-key", provider="groq", fallback_on_failure=False,
    )

    assert calls["n"] == 3
    assert sleep_calls["n"] == 1
    assert models_used == [
        grd.GROQ_GUARDRAIL_MODEL,
        grd.GROQ_GUARDRAIL_FALLBACK_MODEL,
        grd.GROQ_GUARDRAIL_FALLBACK_MODEL,
    ]
    assert result["success"] is False
    assert "try again" in result["error"].lower()


# ===========================================================================
# Невалидный JSON в ответе LLM
# ===========================================================================

def test_03h_invalid_json_no_braces_at_all(monkeypatch):
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _FakeResponse(200, {
            "choices": [{"message": {"content": "This is not JSON at all."}}],
            "usage": {"total_tokens": 9},
        }),
    )
    result = grd.GuardrailService.run_check("a", "b", "English", "fake-key", provider="groq", fallback_on_failure=False)
    assert result["success"] is False
    assert result["pass"] is None
    assert result["findings"] == []
    assert result["tokens_used"] == 9  # токены посчитаны, даже если распарсить не удалось
    assert "json" in result["error"].lower() or "parse" in result["error"].lower()


def test_03i_markdown_wrapped_json_falls_back_via_regex(monkeypatch):
    """Ответ обёрнут в ```json ... ``` — должен распарситься через
    regex-фоллбэк, как и в _call_groq_json()."""
    content = '```json\n{"pass": true, "findings": []}\n```'
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _FakeResponse(200, {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 3},
        }),
    )
    result = grd.GuardrailService.run_check("a", "b", "English", "fake-key", provider="groq")
    assert result["success"] is True
    assert result["pass"] is True
    assert result["tokens_used"] == 3


def test_03j_invalid_json_never_silently_reports_clean(monkeypatch):
    """Ключевое отличие от _call_groq_json(): при неудачном парсинге
    Guardrail НЕ должен тихо подставлять success=True/pass=true."""
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _FakeResponse(200, {
            "choices": [{"message": {"content": "{broken json"}}],
            "usage": {"total_tokens": 1},
        }),
    )
    result = grd.GuardrailService.run_check("a", "b", "English", "fake-key", provider="groq", fallback_on_failure=False)
    assert result["success"] is False
    assert result["pass"] is not True


# ===========================================================================
# Модель зафиксирована в константе (Этап 4 требование)
# ===========================================================================

def test_03k_model_constants_match_confirmed_pipeline_models():
    """GROQ_GUARDRAIL_MODEL / GROQ_GUARDRAIL_FALLBACK_MODEL — подтверждены
    консультационной сессией по актуальному коду основного пайплайна
    (закрывает открытый вопрос из Этапа 1 про расхождение с overview.md)."""
    assert grd.GROQ_GUARDRAIL_MODEL == "openai/gpt-oss-120b"
    assert grd.GROQ_GUARDRAIL_FALLBACK_MODEL == "openai/gpt-oss-20b"


def test_03l_model_constant_is_actually_used_in_request(monkeypatch):
    captured = {}

    def _post(url, headers=None, json=None, timeout=None):
        captured["model"] = json.get("model")
        return _groq_ok_response(pass_value=True, findings=[])

    monkeypatch.setattr(grd.requests, "post", _post)
    grd.GuardrailService.run_check("a", "b", "English", "fake-key", provider="groq")
    assert captured["model"] == grd.GROQ_GUARDRAIL_MODEL


# ===========================================================================
# Неизвестный provider — без обращения к какому-либо провайдеру
# ===========================================================================

def test_04_unknown_provider_returns_clean_error():
    result = grd.GuardrailService.run_check(
        original_text="a", improved_text="b", language="English",
        api_key="fake-key", provider="does-not-exist",
    )
    assert result["success"] is False
    assert result["pass"] is None
    assert result["findings"] == []
    assert result["guardrail_rejected"] is False
    assert result["tokens_used"] == 0
    assert "does-not-exist" in result["error"]


def test_05_unknown_provider_error_lists_available_providers():
    result = grd.GuardrailService.run_check("a", "b", "English", "k", provider="mistral")
    assert "groq" in result["error"]
    assert "openai" in result["error"]
    assert "anthropic" in result["error"]


# ===========================================================================
# "Не реализовано" провайдеры — не райзят исключений, без сети
# ===========================================================================

def test_06_openai_provider_does_not_raise():
    result = grd.OpenAIGuardrailProvider.check("a", "b", "English", "fake-key")
    assert result["success"] is False
    assert result["error"]
    assert result["findings"] == []


def test_07_anthropic_provider_does_not_raise():
    result = grd.AnthropicGuardrailProvider.check("a", "b", "English", "fake-key")
    assert result["success"] is False
    assert result["error"]
    assert result["findings"] == []


def test_08_run_check_with_openai_provider_end_to_end():
    result = grd.GuardrailService.run_check(
        original_text="a", improved_text="b", language="English",
        api_key="fake-key", provider="openai", fallback_on_failure=False,
    )
    assert result["success"] is False
    assert result["pass"] is None
    assert result["guardrail_rejected"] is False
    assert "not implemented" in result["error"].lower()


def test_09_run_check_with_anthropic_provider_end_to_end():
    result = grd.GuardrailService.run_check(
        original_text="a", improved_text="b", language="English",
        api_key="fake-key", provider="anthropic", fallback_on_failure=False,
    )
    assert result["success"] is False
    assert result["pass"] is None
    assert "not implemented" in result["error"].lower()


# ===========================================================================
# guardrail_rejected — блокирующая политика по severity (Этап 2, решение №1)
# ===========================================================================

class _FakeHighSeverityProvider(grd.GuardrailProvider):
    """Фейковый provider с finding severity='high' — не завязан на
    реальный Groq-вызов, проверяет только политику блокировки facade'а."""
    PROVIDER_NAME = "fake_high"

    @staticmethod
    def check(original_text, improved_text, language, api_key):
        return {
            "success": True,
            "pass": False,
            "findings": [{
                "type": "role_escalation",
                "severity": "high",
                "original_excerpt": "participated in the project",
                "improved_excerpt": "led the project",
                "explanation": "Escalates participation to leadership without support in the original.",
            }],
            "tokens_used": 42,
            "error": None,
        }


class _FakeMediumSeverityProvider(grd.GuardrailProvider):
    """finding severity='medium' — не должен блокировать (Этап 2, решение №1)."""
    PROVIDER_NAME = "fake_medium"

    @staticmethod
    def check(original_text, improved_text, language, api_key):
        return {
            "success": True,
            "pass": False,
            "findings": [{
                "type": "other",
                "severity": "medium",
                "original_excerpt": "x",
                "improved_excerpt": "y",
                "explanation": "z",
            }],
            "tokens_used": 10,
            "error": None,
        }


def test_10_high_severity_finding_sets_guardrail_rejected(monkeypatch):
    monkeypatch.setitem(grd._PROVIDERS, "fake_high", _FakeHighSeverityProvider)
    result = grd.GuardrailService.run_check("a", "b", "English", "k", provider="fake_high")
    assert result["success"] is True
    assert result["guardrail_rejected"] is True
    assert len(result["findings"]) == 1
    assert result["tokens_used"] == 42


def test_11_medium_severity_finding_does_not_reject(monkeypatch):
    monkeypatch.setitem(grd._PROVIDERS, "fake_medium", _FakeMediumSeverityProvider)
    result = grd.GuardrailService.run_check("a", "b", "English", "k", provider="fake_medium")
    assert result["success"] is True
    assert result["guardrail_rejected"] is False
    assert len(result["findings"]) == 1


# ===========================================================================
# Провайдер, нарушающий контракт (райзит) — facade не должен падать
# ===========================================================================

class _BrokenProvider(grd.GuardrailProvider):
    PROVIDER_NAME = "broken"

    @staticmethod
    def check(original_text, improved_text, language, api_key):
        raise RuntimeError("simulated bug in a future provider implementation")


def test_12_facade_survives_provider_raising(monkeypatch):
    """Если провайдер всё же нарушит контракт "никогда не райзить",
    run_check() не должен ронять вызывающий improve-пайплайн."""
    monkeypatch.setitem(grd._PROVIDERS, "broken", _BrokenProvider)
    result = grd.GuardrailService.run_check("a", "b", "English", "k", provider="broken", fallback_on_failure=False)
    assert result["success"] is False
    assert result["guardrail_rejected"] is False
    assert "simulated bug" in result["error"]


# ===========================================================================
# Этап 6 — content-level тесты: мок отвечает так, как ДОЛЖЕН был бы
# ответить LLM, корректно следуя _JUDGE_INSTRUCTIONS (Этап 5), для
# каждого обязательного сценария из ТЗ. Это НЕ тесты HTTP/parsing-слоя
# (те уже в Этапе 4/test_03*) — это проверка, что "правильный" ответ
# LLM корректно доходит до guardrail_rejected/findings через
# GuardrailService. Мокается requests.post — тот же механизм, что в
# Этапе 4, реального LLM-вызова здесь нет.
# ===========================================================================

def _mock_compliant_judge_response(findings, tokens=0):
    """
    "Fake LLM" — фейковый ответ Groq, КАК БЫ его вернул LLM, если бы
    корректно следовал _JUDGE_INSTRUCTIONS для конкретного примера.
    pass=true только если findings пуст — та же семантика, что задана
    самим промтом (Этап 5): "pass": true only if "findings" is an
    empty array, false otherwise. Переиспользует _groq_ok_response,
    уже проверенный HTTP/parsing-тестами Этапа 4.
    """
    pass_value = len(findings) == 0
    return _groq_ok_response(pass_value=pass_value, findings=findings, tokens=tokens)


def test_20_pass_ru_configured_equipment(monkeypatch):
    """PASS: усиление формулировки без нового факта — RU."""
    monkeypatch.setattr(grd.requests, "post", lambda *a, **kw: _mock_compliant_judge_response([]))
    result = grd.GuardrailService.run_check(
        original_text="занимался настройкой оборудования",
        improved_text="настроил оборудование",
        language="Russian", api_key="fake-key", provider="groq",
    )
    assert result["pass"] is True
    assert result["findings"] == []
    assert result["guardrail_rejected"] is False


def test_21_pass_en_tested_and_verified(monkeypatch):
    """PASS: усиление формулировки без нового факта — EN."""
    monkeypatch.setattr(grd.requests, "post", lambda *a, **kw: _mock_compliant_judge_response([]))
    result = grd.GuardrailService.run_check(
        original_text="was responsible for testing",
        improved_text="tested and verified",
        language="English", api_key="fake-key", provider="groq",
    )
    assert result["pass"] is True
    assert result["findings"] == []
    assert result["guardrail_rejected"] is False


def test_22_fail_role_escalation_ru(monkeypatch):
    """FAIL ROLE_ESCALATION (severity=high) -> guardrail_rejected=True — RU."""
    finding = {
        "type": "ROLE_ESCALATION",
        "severity": "high",
        "original_excerpt": "участвовал в разработке внутренних инструментов",
        "improved_excerpt": "руководил командой разработки внутренних инструментов",
        "explanation": "Escalates participation to leading the team, unsupported by the original.",
    }
    monkeypatch.setattr(grd.requests, "post", lambda *a, **kw: _mock_compliant_judge_response([finding]))
    result = grd.GuardrailService.run_check(
        original_text="участвовал в разработке внутренних инструментов",
        improved_text="руководил командой разработки внутренних инструментов",
        language="Russian", api_key="fake-key", provider="groq",
    )
    assert result["pass"] is False
    assert result["findings"] == [finding]
    assert result["guardrail_rejected"] is True


def test_23_fail_helper_escalation_ru_migration(monkeypatch):
    """FAIL HELPER_ESCALATION (severity=high) -> guardrail_rejected=True — RU."""
    finding = {
        "type": "HELPER_ESCALATION",
        "severity": "high",
        "original_excerpt": "помогал с миграцией",
        "improved_excerpt": "выполнил миграцию",
        "explanation": "Turns a helping role into sole execution of the migration.",
    }
    monkeypatch.setattr(grd.requests, "post", lambda *a, **kw: _mock_compliant_judge_response([finding]))
    result = grd.GuardrailService.run_check(
        original_text="помогал с миграцией",
        improved_text="выполнил миграцию",
        language="Russian", api_key="fake-key", provider="groq",
    )
    assert result["pass"] is False
    assert result["findings"] == [finding]
    assert result["guardrail_rejected"] is True


def test_24_fail_helper_escalation_en_api(monkeypatch):
    """FAIL HELPER_ESCALATION (severity=high) -> guardrail_rejected=True — EN."""
    finding = {
        "type": "HELPER_ESCALATION",
        "severity": "high",
        "original_excerpt": "assisted team with API",
        "improved_excerpt": "built the API",
        "explanation": "Turns a supporting role into sole ownership of building the API.",
    }
    monkeypatch.setattr(grd.requests, "post", lambda *a, **kw: _mock_compliant_judge_response([finding]))
    result = grd.GuardrailService.run_check(
        original_text="assisted team with API",
        improved_text="built the API",
        language="English", api_key="fake-key", provider="groq",
    )
    assert result["pass"] is False
    assert result["findings"] == [finding]
    assert result["guardrail_rejected"] is True


def test_25_fail_metric_hallucination_en(monkeypatch):
    """FAIL METRIC_HALLUCINATION (severity=medium) -> guardrail_rejected=False (не блокирует)."""
    finding = {
        "type": "METRIC_HALLUCINATION",
        "severity": "medium",
        "original_excerpt": "worked with customer data",
        "improved_excerpt": "processed 2 million customer records",
        "explanation": "Introduces a specific figure (2 million records) absent from the original.",
    }
    monkeypatch.setattr(grd.requests, "post", lambda *a, **kw: _mock_compliant_judge_response([finding]))
    result = grd.GuardrailService.run_check(
        original_text="worked with customer data",
        improved_text="processed 2 million customer records",
        language="English", api_key="fake-key", provider="groq",
    )
    assert result["pass"] is False
    assert result["findings"] == [finding]
    assert result["guardrail_rejected"] is False  # medium — не блокирует


def test_26_fail_technology_hallucination_en(monkeypatch):
    """FAIL TECHNOLOGY_HALLUCINATION (severity=medium) -> guardrail_rejected=False (не блокирует)."""
    finding = {
        "type": "TECHNOLOGY_HALLUCINATION",
        "severity": "medium",
        "original_excerpt": "worked with databases",
        "improved_excerpt": "developed PostgreSQL optimization pipelines",
        "explanation": "Names a specific technology (PostgreSQL) absent from the original.",
    }
    monkeypatch.setattr(grd.requests, "post", lambda *a, **kw: _mock_compliant_judge_response([finding]))
    result = grd.GuardrailService.run_check(
        original_text="worked with databases",
        improved_text="developed PostgreSQL optimization pipelines",
        language="English", api_key="fake-key", provider="groq",
    )
    assert result["pass"] is False
    assert result["findings"] == [finding]
    assert result["guardrail_rejected"] is False  # medium — не блокирует


# ===========================================================================
# Content-level edge cases (Этап 6)
# ===========================================================================

def test_27_identical_original_and_improved_yields_no_findings(monkeypatch):
    """identical original/improved -> findings пуст, pass=true, guardrail_rejected=False."""
    text = "Managed a team of 5 engineers across two product lines."
    monkeypatch.setattr(grd.requests, "post", lambda *a, **kw: _mock_compliant_judge_response([]))
    result = grd.GuardrailService.run_check(
        original_text=text, improved_text=text, language="English",
        api_key="fake-key", provider="groq",
    )
    assert result["pass"] is True
    assert result["findings"] == []
    assert result["guardrail_rejected"] is False


def test_28_build_judge_prompt_handles_empty_original_text():
    """Пустой original_text не должен приводить к исключению в
    _build_judge_prompt() — сеть здесь не нужна."""
    system_prompt, user_prompt = grd._build_judge_prompt("", "some improved text", "English")
    assert isinstance(system_prompt, str) and system_prompt
    assert isinstance(user_prompt, str) and user_prompt
    assert "ORIGINAL:" in user_prompt
    assert "IMPROVED:" in user_prompt


def test_29_build_judge_prompt_handles_empty_improved_text():
    """Пустой improved_text не должен приводить к исключению в
    _build_judge_prompt() — сеть здесь не нужна."""
    system_prompt, user_prompt = grd._build_judge_prompt("some original text", "", "English")
    assert isinstance(system_prompt, str) and system_prompt
    assert isinstance(user_prompt, str) and user_prompt


def test_30_build_judge_prompt_handles_both_texts_empty():
    """Оба текста пустые — тоже не должно падать (граничный случай)."""
    system_prompt, user_prompt = grd._build_judge_prompt("", "", "English")
    assert isinstance(system_prompt, str) and system_prompt
    assert isinstance(user_prompt, str) and user_prompt


# ===========================================================================
# Этап 7 — fallback на локальную защиту (app/missing_routes4.py) при
# недоступности LLM. Ровно 5 тестов, как согласовано.
# ===========================================================================

def _install_fake_missing_routes4(monkeypatch, validate_block_fn, check_role_escalation_fn):
    """
    Подставляет фейковый app.missing_routes4 в sys.modules ДО того,
    как LocalFallbackGuardrailProvider.check() сделает свой lazy
    import — реальный app/missing_routes4.py в этом изолированном
    тестовом окружении недоступен (и не должен быть нужен: он тянет
    app/__init__.py -> Flask/SQLAlchemy/JWT/CORS/Mail, см. докстринг
    модуля). monkeypatch.setitem сам откатит sys.modules после теста.
    """
    fake_app_pkg = types.ModuleType("app")
    fake_missing_routes4 = types.ModuleType("app.missing_routes4")
    fake_missing_routes4._validate_block = validate_block_fn
    fake_missing_routes4._check_role_escalation = check_role_escalation_fn
    monkeypatch.setitem(sys.modules, "app", fake_app_pkg)
    monkeypatch.setitem(sys.modules, "app.missing_routes4", fake_missing_routes4)


def test_31_fallback_triggers_on_groq_failure(monkeypatch, caplog):
    """fallback_on_failure=True (по умолчанию): Groq вернул success=False
    -> GuardrailService автоматически пробует LocalFallbackGuardrailProvider
    и логирует факт перехода на fallback."""
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _FakeResponse(500, {"error": {"message": "boom"}}),
    )
    _install_fake_missing_routes4(
        monkeypatch,
        validate_block_fn=lambda o, n: (True, ""),
        check_role_escalation_fn=lambda o, n: (True, ""),
    )
    with caplog.at_level("WARNING"):
        result = grd.GuardrailService.run_check(
            "a", "b", "English", "fake-key", provider="groq",
        )
    assert result["success"] is True   # дошли до чистого результата fallback'а
    assert result["pass"] is True
    assert result["findings"] == []
    assert any("fall" in rec.message.lower() for rec in caplog.records)


def test_32_fallback_disabled_returns_primary_failure(monkeypatch):
    """fallback_on_failure=False: при success=False от Groq сразу
    возвращаем ошибку Groq, LocalFallbackGuardrailProvider не вызывается
    (если бы вызывался — в этом окружении без фейкового sys.modules
    словил бы ImportError и error был бы другим)."""
    monkeypatch.setattr(
        grd.requests, "post",
        lambda *a, **kw: _FakeResponse(500, {"error": {"message": "boom"}}),
    )
    result = grd.GuardrailService.run_check(
        "a", "b", "English", "fake-key", provider="groq", fallback_on_failure=False,
    )
    assert result["success"] is False
    assert result["error"] == "boom"


def test_33_local_fallback_catches_invented_fact(monkeypatch):
    """LocalFallbackGuardrailProvider вызывает _validate_block() напрямую
    и транслирует её (False, reason) в finding type='invented_fact'."""
    _install_fake_missing_routes4(
        monkeypatch,
        validate_block_fn=lambda o, n: (False, "Invented facts: 50"),
        check_role_escalation_fn=lambda o, n: (True, ""),
    )
    result = grd.LocalFallbackGuardrailProvider.check(
        "team of 5", "team of 50", "English", "fake-key",
    )
    assert result["success"] is True
    assert result["pass"] is False
    assert len(result["findings"]) == 1
    assert result["findings"][0]["type"] == "invented_fact"
    assert result["findings"][0]["severity"] == "high"
    assert result["findings"][0]["explanation"] == "Invented facts: 50"


def test_34_local_fallback_catches_role_escalation(monkeypatch):
    """LocalFallbackGuardrailProvider вызывает _check_role_escalation()
    напрямую и транслирует её (False, reason) в finding
    type='role_escalation', explanation — reason как есть, без
    переформулировки."""
    reason = "Role escalation: introduced 'led', 'managed' not present in original"
    _install_fake_missing_routes4(
        monkeypatch,
        validate_block_fn=lambda o, n: (True, ""),
        check_role_escalation_fn=lambda o, n: (False, reason),
    )
    result = grd.LocalFallbackGuardrailProvider.check(
        "participated in the project", "led the project", "English", "fake-key",
    )
    assert result["success"] is True
    assert result["pass"] is False
    assert len(result["findings"]) == 1
    assert result["findings"][0]["type"] == "role_escalation"
    assert result["findings"][0]["severity"] == "high"
    assert result["findings"][0]["explanation"] == reason


def test_35_local_fallback_returns_clean_failure_on_import_error(monkeypatch):
    """Принудительно смоделировать отсутствие app.missing_routes4 через
    sys.modules[...] = None — стандартный приём, вызывающий ImportError
    при импорте независимо от того, закэширован ли модуль другими
    тестами в этом же прогоне pytest (например tests/test_missing_routes4.py,
    который импортирует его на уровне модуля)."""
    monkeypatch.setitem(sys.modules, "app.missing_routes4", None)
    result = grd.LocalFallbackGuardrailProvider.check("a", "b", "English", "fake-key")
    assert result["success"] is False
    assert result["pass"] is None
    assert result["findings"] == []
    assert result["error"]


# ===========================================================================
# ПРАВКА B — LocalFallbackGuardrailProvider сравнивает текст ПОСТРОЧНО
# (_validate_block/_check_role_escalation писались для отдельных блоков;
# на целом многострочном тексте глагол в начале не первой строки давал
# ложное "Invented facts"). Ровно 3 теста.
# ===========================================================================

def test_36_local_fallback_checks_only_the_changed_line_pair(monkeypatch):
    """Многострочный текст с одной изменённой строкой: обе функции
    вызваны ТОЛЬКО с этой парой строк и ни разу с аргументом,
    содержащим перенос строки."""
    validate_calls = []
    role_calls = []

    def fake_validate(o, n):
        validate_calls.append((o, n))
        return True, ""

    def fake_role(o, n):
        role_calls.append((o, n))
        return True, ""

    _install_fake_missing_routes4(monkeypatch, fake_validate, fake_role)

    original = "Опыт работы\nManaged a team of 5\nEnglish (Native)"
    improved = "Опыт работы\nDirected a team of 5\nEnglish (Native)"
    result = grd.LocalFallbackGuardrailProvider.check(original, improved, "English", "fake-key")

    assert result["success"] is True
    assert result["pass"] is True
    assert validate_calls == [("Managed a team of 5", "Directed a team of 5")]
    assert role_calls == [("Managed a team of 5", "Directed a team of 5")]
    assert all("\n" not in arg for call in validate_calls + role_calls for arg in call)


def test_37_local_fallback_fails_cleanly_when_line_counts_differ(monkeypatch):
    """Разное число строк: success=False, проверки на целых текстах не
    запускаются (фейки не вызваны)."""
    calls = []

    def fake_validate(o, n):
        calls.append(("validate", o, n))
        return True, ""

    def fake_role(o, n):
        calls.append(("role", o, n))
        return True, ""

    _install_fake_missing_routes4(monkeypatch, fake_validate, fake_role)

    result = grd.LocalFallbackGuardrailProvider.check(
        "line one\nline two", "line one\nline two\nline three", "English", "fake-key",
    )

    assert result["success"] is False
    assert result["pass"] is None
    assert result["findings"] == []
    assert result["tokens_used"] == 0
    assert result["error"] == "Local fallback cannot align original and improved text line by line"
    assert calls == []


def test_38_local_fallback_finding_excerpts_are_the_line_pair(monkeypatch):
    """При срабатывании на изменённой строке original_excerpt/
    improved_excerpt в finding — именно эта пара строк, а не весь текст;
    explanation — reason из функции как есть."""
    _install_fake_missing_routes4(
        monkeypatch,
        validate_block_fn=lambda o, n: (False, "Invented facts: 50") if "50" in n else (True, ""),
        check_role_escalation_fn=lambda o, n: (True, ""),
    )

    original = "Опыт работы\nteam of 5\nEnglish (Native)"
    improved = "Опыт работы\nteam of 50\nEnglish (Native)"
    result = grd.LocalFallbackGuardrailProvider.check(original, improved, "English", "fake-key")

    assert result["success"] is True
    assert result["pass"] is False
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["type"] == "invented_fact"
    assert finding["severity"] == "high"
    assert finding["original_excerpt"] == "team of 5"
    assert finding["improved_excerpt"] == "team of 50"
    assert finding["explanation"] == "Invented facts: 50"
