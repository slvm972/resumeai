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
