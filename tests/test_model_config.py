"""
Тесты настроек моделей Groq (GROQ_MODEL_ANALYSIS / EDITOR / FALLBACK / JUDGE),
дополнительных параметров "думающих" моделей, повтора при 400, переключения
на запасную модель при 429 и пустом ответе, удаления <think>...</think>.
Сеть не используется: requests.post замокан.

Запуск: python -m pytest tests/test_model_config.py -v
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

from app import create_app
from app.services import groq_params as gp
from app.services import resume_guardrail as grd

DEFAULT_MODEL = 'openai/gpt-oss-120b'
FALLBACK_MODEL = 'qwen/qwen3.8-27b'

_ANALYSIS_JSON = json.dumps({
    'overall_score': 70, 'ats_score': 65, 'formatting': 60, 'content': 68,
    'summary': 'ok', 'strengths': ['a'], 'improvements': ['b'], 'key_skills': ['c'],
})
_RESUME = ("John Doe\njohn.doe@example.com\n"
           "Developed internal tools for the sales team and maintained reports")


class _R:
    """Минимальный ответ requests."""
    def __init__(self, status=200, content='', usage_tokens=10, error=None):
        self.status_code = status
        self._body = (
            {'choices': [{'message': {'content': content}}], 'usage': {'total_tokens': usage_tokens}}
            if status == 200 else {'error': {'message': error or 'error %s' % status}}
        )
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _Poster:
    """requests.post, отдающий ответы по очереди и запоминающий payload'ы."""
    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.payloads.append(dict(json))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    # config.py читает GROQ_API_KEY при первом импорте; в общем прогоне его могли
    # импортировать раньше, чем этот файл выставил переменную окружения.
    app.config['GROQ_API_KEY'] = 'test-groq-key-not-used-mocked-out'
    with app.app_context():
        yield app


def _analyze(poster):
    from app.services.openrouter_service import OpenRouterService

    class _U:
        id = 0

    with patch('requests.post', side_effect=poster):
        return OpenRouterService.analyze_resume(_U(), _RESUME)


def _improve(poster):
    from app.missing_routes4 import _run_improve_pipeline
    with patch('requests.post', side_effect=poster):
        return _run_improve_pipeline(None, None, _RESUME, 'dummy-key')


_IMPROVE_OK = '###ITEM_003###\nBuilt internal tools for the sales team and maintained reports'


# ---------------------------------------------------------------------------
# Имена моделей из настроек
# ---------------------------------------------------------------------------

def test_01_defaults_in_config_and_payloads(app_ctx):
    app = app_ctx
    assert app.config['GROQ_MODEL_ANALYSIS'] == DEFAULT_MODEL
    assert app.config['GROQ_MODEL_EDITOR'] == DEFAULT_MODEL
    assert app.config['GROQ_MODEL_FALLBACK'] == FALLBACK_MODEL
    assert app.config['GROQ_MODEL_JUDGE'] == DEFAULT_MODEL

    poster = _Poster(_R(200, _ANALYSIS_JSON))
    assert _analyze(poster)['success'] is True
    assert poster.payloads[0]['model'] == DEFAULT_MODEL

    poster = _Poster(_R(200, _IMPROVE_OK))
    assert _improve(poster)['success'] is True
    assert poster.payloads[0]['model'] == DEFAULT_MODEL


def test_02_config_override_changes_model_in_payloads(app_ctx):
    app = app_ctx
    app.config['GROQ_MODEL_ANALYSIS'] = 'qwen/qwen3.8-27b'
    app.config['GROQ_MODEL_EDITOR'] = 'some/other-model'

    poster = _Poster(_R(200, _ANALYSIS_JSON))
    _analyze(poster)
    assert poster.payloads[0]['model'] == 'qwen/qwen3.8-27b'

    poster = _Poster(_R(200, _IMPROVE_OK))
    _improve(poster)
    assert poster.payloads[0]['model'] == 'some/other-model'
    assert 'reasoning_effort' not in poster.payloads[0]       # для прочих - без лишних параметров
    assert 'reasoning_format' not in poster.payloads[0]


def test_02b_model_taken_from_environment_outside_app_context(monkeypatch):
    monkeypatch.setenv('GROQ_MODEL_JUDGE', 'env/judge-model')
    assert gp.groq_model('GROQ_MODEL_JUDGE') == 'env/judge-model'
    monkeypatch.delenv('GROQ_MODEL_JUDGE')
    assert gp.groq_model('GROQ_MODEL_JUDGE') == DEFAULT_MODEL


def test_02c_token_limits_raised(app_ctx):
    poster = _Poster(_R(200, _ANALYSIS_JSON))
    _analyze(poster)
    assert poster.payloads[0]['max_tokens'] == 3000

    poster = _Poster(_R(200, _IMPROVE_OK))
    _improve(poster)
    assert poster.payloads[0]['max_tokens'] == 6000


# ---------------------------------------------------------------------------
# Extra params
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('model,expected', [
    ('openai/gpt-oss-120b', {'reasoning_effort': 'low'}),
    ('openai/gpt-oss-20b', {'reasoning_effort': 'low'}),
    ('qwen/qwen3.8-27b', {'reasoning_format': 'hidden'}),
    ('qwen/qwen3-32b', {'reasoning_format': 'hidden'}),
    ('some/other-model', {}),
    ('', {}),
    (None, {}),
])
def test_03_groq_extra_params(model, expected):
    assert gp.groq_extra_params(model) == expected


def test_03b_payload_contains_extras_for_gpt_oss_and_qwen(app_ctx):
    poster = _Poster(_R(200, _ANALYSIS_JSON))
    _analyze(poster)
    assert poster.payloads[0]['reasoning_effort'] == 'low'
    assert 'reasoning_format' not in poster.payloads[0]

    app_ctx.config['GROQ_MODEL_ANALYSIS'] = 'qwen/qwen3.8-27b'
    poster = _Poster(_R(200, _ANALYSIS_JSON))
    _analyze(poster)
    assert poster.payloads[0]['reasoning_format'] == 'hidden'
    assert 'reasoning_effort' not in poster.payloads[0]


# ---------------------------------------------------------------------------
# 400 -> один повтор без extra params
# ---------------------------------------------------------------------------

def test_04_400_retries_once_without_extras_analysis(app_ctx):
    poster = _Poster(_R(400, error='unsupported parameter reasoning_effort'), _R(200, _ANALYSIS_JSON))
    result = _analyze(poster)
    assert result['success'] is True and result['overall_score'] == 70
    assert len(poster.payloads) == 2
    assert 'reasoning_effort' in poster.payloads[0]
    assert 'reasoning_effort' not in poster.payloads[1]
    assert poster.payloads[1]['model'] == poster.payloads[0]['model']


def test_04b_400_retry_only_once_and_improve_pipeline(app_ctx):
    # два 400 подряд: повтор ровно один, ответ 400 возвращается вызывающему коду
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(dict(json))
        return _R(400, error='bad')

    resp = gp.post_groq(post, 'u', {}, {'model': DEFAULT_MODEL, 'messages': []}, 5)
    assert resp.status_code == 400 and len(calls) == 2
    assert 'reasoning_effort' in calls[0] and 'reasoning_effort' not in calls[1]

    # модель без extras: 400 не повторяется
    calls.clear()
    gp.post_groq(post, 'u', {}, {'model': 'some/other', 'messages': []}, 5)
    assert len(calls) == 1

    poster = _Poster(_R(400, error='x'), _R(200, _IMPROVE_OK))
    assert _improve(poster)['success'] is True
    assert 'reasoning_effort' not in poster.payloads[1]


# ---------------------------------------------------------------------------
# 429 -> запасная модель (как раньше на llama-3.1-8b-instant)
# ---------------------------------------------------------------------------

def test_05_429_switches_to_fallback_model_in_pipeline(app_ctx):
    poster = _Poster(_R(429, error='rate limit'), _R(200, _IMPROVE_OK))
    result = _improve(poster)
    assert result['success'] is True
    assert poster.payloads[0]['model'] == DEFAULT_MODEL
    assert poster.payloads[1]['model'] == FALLBACK_MODEL
    assert poster.payloads[1]['reasoning_format'] == 'hidden'
    assert 'reasoning_effort' not in poster.payloads[1]


def test_05b_fallback_model_taken_from_config(app_ctx):
    app_ctx.config['GROQ_MODEL_FALLBACK'] = 'custom/fallback'
    poster = _Poster(_R(429, error='rate limit'), _R(200, _IMPROVE_OK))
    _improve(poster)
    assert poster.payloads[1]['model'] == 'custom/fallback'


# ---------------------------------------------------------------------------
# Пустой content -> fallback или ошибка, без падения
# ---------------------------------------------------------------------------

def test_06_empty_content_goes_to_fallback_in_pipeline(app_ctx):
    poster = _Poster(_R(200, ''), _R(200, _IMPROVE_OK))
    result = _improve(poster)
    assert result['success'] is True
    assert [p['model'] for p in poster.payloads] == [DEFAULT_MODEL, FALLBACK_MODEL]


def test_06b_empty_everywhere_is_error_not_crash(app_ctx):
    poster = _Poster(_R(200, ''), _R(200, '   '))
    result = _improve(poster)
    assert result['success'] is False
    assert 'Empty response' in result['error']
    assert len(poster.payloads) == 2                     # одна запасная попытка, не больше


def test_06c_empty_content_in_analysis_is_clean_error(app_ctx):
    result = _analyze(_Poster(_R(200, '')))
    assert result['success'] is False
    assert 'Empty response' in result['error']
    # None вместо content тоже не роняет
    r = _R(200, '')
    r._body['choices'][0]['message']['content'] = None
    assert _analyze(_Poster(r))['success'] is False


def test_06d_empty_content_guardrail_uses_fallback_then_errors(monkeypatch):
    seen = []

    def post(url, headers=None, json=None, timeout=None):
        seen.append(json['model'])
        return _R(200, '')

    monkeypatch.setattr(grd.requests, 'post', post)
    # без локального отката видна ошибка самого провайдера
    res = grd.GuardrailService.run_check('a', 'b', 'English', 'fake-key', provider='groq',
                                         fallback_on_failure=False)
    assert res['success'] is False and 'Empty response' in res['error']
    assert seen == [DEFAULT_MODEL, FALLBACK_MODEL]

    # с откатом (по умолчанию) сервис не падает и переходит на локальные проверки
    seen.clear()
    res = grd.GuardrailService.run_check('a', 'b', 'English', 'fake-key', provider='groq')
    assert res['success'] is True
    assert seen == [DEFAULT_MODEL, FALLBACK_MODEL]

    seen.clear()
    ok = _R(200, json.dumps({'pass': True, 'findings': []}))
    queue = [_R(200, ''), ok]
    monkeypatch.setattr(grd.requests, 'post', lambda *a, **k: (seen.append(k['json']['model']) or queue.pop(0)))
    res = grd.GuardrailService.run_check('a', 'b', 'English', 'fake-key', provider='groq')
    assert res['success'] is True and res['pass'] is True
    assert seen == [DEFAULT_MODEL, FALLBACK_MODEL]


def test_06e_guardrail_payload_model_extras_and_tokens(monkeypatch):
    captured = {}

    def post(url, headers=None, json=None, timeout=None):
        captured.update(json)
        return _R(200, '{"pass": true, "findings": []}')

    monkeypatch.setattr(grd.requests, 'post', post)
    grd.GuardrailService.run_check('a', 'b', 'English', 'fake-key', provider='groq')
    assert captured['model'] == DEFAULT_MODEL
    assert captured['reasoning_effort'] == 'low'
    assert captured['max_tokens'] >= 3000


# ---------------------------------------------------------------------------
# <think>...</think>
# ---------------------------------------------------------------------------

def test_07_strip_think_variants():
    assert gp.strip_think('<think>рассуждаю\nещё</think>\nOUTPUT') == 'OUTPUT'
    assert gp.strip_think('A<think>x</think>B <THINK>y</THINK>C') == 'AB C'
    assert gp.strip_think('хвост без открытия</think>RESULT') == 'RESULT'
    assert gp.strip_think('RESULT <think>оборвалось') == 'RESULT'
    assert gp.strip_think('') == '' and gp.strip_think(None) == ''


def test_07b_think_removed_from_analysis_and_pipeline_answers(app_ctx):
    result = _analyze(_Poster(_R(200, '<think>сначала подумаю</think>' + _ANALYSIS_JSON)))
    assert result['success'] is True and result['overall_score'] == 70

    result = _improve(_Poster(_R(200, '<think>думаю</think>\n' + _IMPROVE_OK)))
    assert result['success'] is True
    assert 'думаю' not in result['display_text'] and '<think>' not in result['improved_resume']


# ---------------------------------------------------------------------------
# Тест-страж: старых моделей нет в коде приложения
# ---------------------------------------------------------------------------

def test_08_no_dead_llama_models_in_app_code():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app')
    offenders = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith('.py'):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding='utf-8') as f:
                for n, line in enumerate(f, 1):
                    if line.lstrip().startswith('#'):
                        continue                          # в комментариях допустимо
                    if 'llama-3.3-70b-versatile' in line or 'llama-3.1-8b-instant' in line:
                        offenders.append('%s:%d' % (path, n))
    assert not offenders, offenders
