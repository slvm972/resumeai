"""
Серверная проверка почты и пароля при регистрации (AuthService.register).
Проверка в одном месте действует для всех путей:
  /api/user/register, /api/auth/register (в app/__init__.py) и /auth/register (блюпринт).
Без сети. Запуск: FLASK_ENV=testing python -m pytest tests/test_register_validation.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

from app import create_app

_WEAK_MSG = 'Password must be at least 8 characters'
_GOOD_PASSWORD = 'a-long-enough-password'
_COUNTER = {'n': 0}


@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app


def _email(tag='u'):
    _COUNTER['n'] += 1
    return 'regval-%s-%03d@example.com' % (tag, _COUNTER['n'])


def _register(app, email, password, url='/api/user/register'):
    return app.test_client().post(url, json={'email': email, 'password': password})


def _user(email):
    from app.models.user import User
    return User.query.filter_by(email=email).first()


# ---------------------------------------------------------------------------
# Пароль
# ---------------------------------------------------------------------------

def test_password_of_7_chars_rejected_and_user_not_created(app_ctx):
    email = _email('short')
    resp = _register(app_ctx, email, '1234567')
    data = resp.get_json()
    assert resp.status_code == 400 and data['success'] is False
    assert data['error'] == _WEAK_MSG
    assert _user(email) is None


def test_password_of_8_chars_accepted(app_ctx):
    email = _email('eight')
    resp = _register(app_ctx, email, '12345678')
    assert resp.status_code == 201 and resp.get_json()['success'] is True
    assert _user(email) is not None


def test_password_of_129_chars_rejected_and_128_accepted(app_ctx):
    email = _email('long')
    resp = _register(app_ctx, email, 'x' * 129)
    data = resp.get_json()
    assert resp.status_code == 400 and data['success'] is False
    assert '128' in data['error']
    assert _user(email) is None
    ok = _register(app_ctx, _email('long128'), 'x' * 128)
    assert ok.status_code == 201


@pytest.mark.parametrize('bad', [None, '', 12345678, ['12345678'], {'p': 1}])
def test_non_string_or_empty_password_rejected_without_server_error(app_ctx, bad):
    email = _email('badtype')
    resp = _register(app_ctx, email, bad)
    assert resp.status_code == 400 and resp.get_json()['success'] is False
    assert _user(email) is None


def test_password_with_edge_spaces_is_kept_exactly_and_login_works(app_ctx):
    email = _email('spaces')
    password = '  pass word 1  '
    assert _register(app_ctx, email, password).status_code == 201
    good = app_ctx.test_client().post('/api/user/login', json={'email': email, 'password': password})
    assert good.status_code == 200 and good.get_json()['success'] is True
    stripped = app_ctx.test_client().post('/api/user/login', json={'email': email, 'password': password.strip()})
    assert stripped.status_code == 401                     # пароль не обрезался


# ---------------------------------------------------------------------------
# Почта
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('bad_email', [
    'no-at-sign.example.com', 'a b@example.com', 'a@exa mple.com', '', '   ', None, 123, ['a@b.com'],
])
def test_invalid_email_rejected(app_ctx, bad_email):
    before = _count_users()
    resp = _register(app_ctx, bad_email, _GOOD_PASSWORD)
    data = resp.get_json()
    assert resp.status_code == 400 and data['success'] is False
    assert data['error'] == 'Invalid email format'
    assert _count_users() == before


def _count_users():
    from app.models.user import User
    return User.query.count()


def test_uppercase_email_with_edge_spaces_is_normalized(app_ctx):
    resp = _register(app_ctx, '  Mixed.Case@Example.COM ', _GOOD_PASSWORD)
    assert resp.status_code == 201
    assert _user('mixed.case@example.com') is not None
    assert _user('  Mixed.Case@Example.COM ') is None


def test_duplicate_email_in_other_case_keeps_old_error(app_ctx):
    assert _register(app_ctx, 'dup-regval@example.com', _GOOD_PASSWORD).status_code == 201
    resp = _register(app_ctx, ' DUP-regval@Example.com', _GOOD_PASSWORD)
    data = resp.get_json()
    assert resp.status_code == 400 and data['success'] is False
    assert data['error'] == 'Email already registered'


def test_validation_runs_before_duplicate_lookup(app_ctx):
    email = _email('order')
    assert _register(app_ctx, email, _GOOD_PASSWORD).status_code == 201
    resp = _register(app_ctx, email, 'short')
    assert resp.get_json()['error'] == _WEAK_MSG           # не 'Email already registered'


# ---------------------------------------------------------------------------
# Все три маршрута
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', ['/api/user/register', '/api/auth/register', '/auth/register'])
def test_all_register_routes_reject_weak_password(app_ctx, url):
    email = _email('route')
    resp = _register(app_ctx, email, '1234567', url=url)
    data = resp.get_json()
    assert resp.status_code == 400 and data['success'] is False
    assert _WEAK_MSG in data['error']
    assert _user(email) is None


@pytest.mark.parametrize('url', ['/api/user/register', '/api/auth/register', '/auth/register'])
def test_all_register_routes_accept_normal_registration(app_ctx, url):
    email = _email('ok')
    resp = _register(app_ctx, email, _GOOD_PASSWORD, url=url)
    assert resp.status_code == 201 and resp.get_json()['success'] is True
    sub = _user(email).get_active_subscription()
    assert sub.credits_granted == 2                        # 2 стартовых кредита не сломаны
