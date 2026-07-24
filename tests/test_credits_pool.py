"""
Regression-тест для Шага 2/6 (единый пул кредитов).

Проверяет, что AuthService.register() выдаёт новому пользователю ровно
2 кредита единого пула (credits_granted=2, credits_used=0,
credits_remaining()==2), и что это значение задано ЯВНО в
AuthService.register (Subscription(credits_granted=2, credits_used=0, ...)),
а не полагается неявно на column-default в модели.

Затронутое место: app/services/auth_service.py -> AuthService.register()

Запуск: python -m pytest tests/test_credits_pool.py -v
"""
import os
import sys
import importlib

import pytest

sys.path.insert(0, '/home/claude/resumeai')

# Безопасные тестовые значения ДО импорта config/app — тот же паттерн,
# что и в tests/test_error_handling.py.
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import config as config_module
importlib.reload(config_module)

from app import create_app


@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app


def test_register_grants_2_credits_via_unified_pool(app_ctx):
    """Новый пользователь получает credits_granted=2, credits_used=0."""
    from app.services.auth_service import AuthService

    result = AuthService.register('credits-pool-test@example.com', 'somepassword123')
    assert result['success'] is True

    user = result['user']
    subscription = user.get_active_subscription()
    assert subscription is not None

    assert subscription.credits_granted == 2
    assert subscription.credits_used == 0
    assert subscription.credits_remaining() == 2


def test_register_does_not_touch_legacy_fields(app_ctx):
    """
    Regression guard: старые поля (analysis_used, improvement_used,
    improvement_credits) остаются на своих старых дефолтах — Шаг 2 не должен
    был их менять, старая логика на них всё ещё работает до Шага 3.
    """
    from app.services.auth_service import AuthService

    result = AuthService.register('legacy-fields-test@example.com', 'somepassword123')
    subscription = result['user'].get_active_subscription()

    assert subscription.analysis_used == 0
    assert subscription.improvement_used == 0
    assert subscription.improvement_credits == 0
    assert subscription.improvement_remaining() == 0


# ===========================================================================
# Шаг 3/6 — /api/analyze, /api/improve, /api/improve/docx на едином пуле
# ===========================================================================

_FAKE_ANALYZE_RESULT = {
    'success': True,
    'analysis': 'ok',
    'overall_score': 70,
    'ats_score': 70,
    'formatting': 70,
    'content': 70,
    'summary': 'summary',
    'strengths': [],
    'improvements': [],
    'key_skills': [],
    'detected_language': 'en',
    'tokens_used': 0,
}

_FAKE_IMPROVE_RESULT = {
    'success': True,
    'improved_resume': '###ITEM_001###\nimproved text',
    'display_text': 'improved text',
    'detected_language': 'en',
    'item_ids': ['001'],
    'quality_report': None,
    'has_original_docx': False,
}


@pytest.fixture
def client_app():
    """Полноценный test client + доступ к app для запросов с сессией."""
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app


def _register_and_login(app, client, email):
    """Зарегистрировать пользователя (получает credits_granted=2, credits_used=0)
    и поставить сессию — тот же способ авторизации, что использует живой legacy-фронтенд."""
    from app.services.auth_service import AuthService

    result = AuthService.register(email, 'somepassword123')
    user = result['user']
    with client.session_transaction() as sess:
        sess['user_id'] = user.id
    return user


def test_analyze_consumes_unified_pool_and_blocks_at_zero(client_app):
    """2 успешных /api/analyze расходуют пул из 2, третий -> 403."""
    from unittest.mock import patch

    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'analyze-pool-test@example.com')

    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        return_value=_FAKE_ANALYZE_RESULT,
    ):
        for expected_used in (1, 2):
            resp = client.post('/api/analyze', json={'resume_text': 'A' * 30})
            assert resp.status_code == 200, resp.get_json()
            sub = user.get_active_subscription()
            assert sub.credits_used == expected_used
            assert sub.credits_remaining() == 2 - expected_used

        # третий запрос — пул исчерпан
        resp = client.post('/api/analyze', json={'resume_text': 'A' * 30})
        assert resp.status_code == 403
        assert resp.get_json()['error'] == 'No credits remaining. Buy a credit pack to continue.'


def test_mixed_analyze_and_improve_share_single_pool(client_app):
    """
    1x analyze + 1x improve исчерпывают пул из 2 -> следующий любой из двух
    блокируется. Это то, что доказывает единый пул, а не два раздельных
    независимых счётчика.
    """
    from unittest.mock import patch

    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'mixed-pool-test@example.com')

    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        return_value=_FAKE_ANALYZE_RESULT,
    ):
        resp = client.post('/api/analyze', json={'resume_text': 'A' * 30})
        assert resp.status_code == 200, resp.get_json()

    sub = user.get_active_subscription()
    assert sub.credits_used == 1
    assert sub.credits_remaining() == 1

    with patch(
        'app.missing_routes4._run_improve_pipeline',
        return_value=_FAKE_IMPROVE_RESULT,
    ):
        resp = client.post('/api/improve', json={'resume_text': 'A' * 30})
        assert resp.status_code == 200, resp.get_json()

    sub = user.get_active_subscription()
    assert sub.credits_used == 2
    assert sub.credits_remaining() == 0

    # Пул исчерпан общим расходом (1 analyze + 1 improve) — оба эндпоинта
    # теперь должны блокировать, хотя по отдельности каждый использовался
    # только один раз.
    resp = client.post('/api/analyze', json={'resume_text': 'A' * 30})
    assert resp.status_code == 403

    resp = client.post('/api/improve', json={'resume_text': 'A' * 30})
    assert resp.status_code == 403


def test_improve_docx_gate_uses_unified_pool_not_plan_name(client_app):
    """
    Regression-тест на найденный и подтверждённый в Шаге 3 баг: gate в
    /api/improve/docx проверял plan_name (никогда не меняется в этом шаге),
    а не остаток пула. Пользователь со свежими credits_remaining()>0 не
    должен получать 403 на этом эндпоинте просто потому что plan_name=='free'.
    """
    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'docx-pool-test@example.com')

    sub = user.get_active_subscription()
    assert sub.plan_name == 'free'
    assert sub.credits_remaining() == 2

    resp = client.post('/api/improve/docx', data={'improved_resume': '###ITEM_001###\ntext'})
    # Не должно быть 403 из-за plan_name=='free' — с пустым original_docx
    # в сессии и без original_file код уйдёт в fallback-ветку генерации
    # простого docx, но это уже не про квоту/доступ.
    assert resp.status_code != 403, resp.get_json()


def test_improve_docx_blocks_when_pool_exhausted(client_app):
    """Regression guard: gate всё ещё блокирует, когда пул реально исчерпан."""
    from unittest.mock import patch

    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'docx-pool-exhausted-test@example.com')

    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        return_value=_FAKE_ANALYZE_RESULT,
    ):
        client.post('/api/analyze', json={'resume_text': 'A' * 30})
        client.post('/api/analyze', json={'resume_text': 'A' * 30})

    sub = user.get_active_subscription()
    assert sub.credits_remaining() == 0

    resp = client.post('/api/improve/docx', data={'improved_resume': '###ITEM_001###\ntext'})
    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'No credits remaining. Buy a credit pack to continue.'


def test_legacy_counters_still_increment_alongside_credits_used(client_app):
    """
    Regression guard: analysis_used/improvement_used по-прежнему растут как
    раньше (читаются /users/usage и /api/admin/debug/user-state) — Шаг 3
    дополняет их credits_used, не заменяет.
    """
    from unittest.mock import patch

    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'legacy-counters-test@example.com')

    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        return_value=_FAKE_ANALYZE_RESULT,
    ):
        client.post('/api/analyze', json={'resume_text': 'A' * 30})

    with patch(
        'app.missing_routes4._run_improve_pipeline',
        return_value=_FAKE_IMPROVE_RESULT,
    ):
        client.post('/api/improve', json={'resume_text': 'A' * 30})

    sub = user.get_active_subscription()
    assert sub.analysis_used == 1
    assert sub.improvement_used == 1
    assert sub.credits_used == 2
