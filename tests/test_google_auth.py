"""
Вход через Google (OAuth 2.0 authorization code): app/routes/google_auth.py. Без сети:
requests.post / requests.get подменяются.
Запуск: FLASK_ENV=testing python -m pytest tests/test_google_auth.py -q
"""
import logging
import os
import sys
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

from app import create_app, db

_CLIENT_ID = 'test-client-id.apps.googleusercontent.com'
_CLIENT_SECRET = 'test-google-client-secret-xyz'
_REDIRECT = 'https://example.test/auth/google/callback'
_CODE = 'auth-code-abc123'
_TOKEN = 'tok-secret-access-789'
_PASSWORD = 'a-long-enough-password'
_ERROR_CODES = {'google_unavailable', 'google_denied', 'google_state',
                'google_failed', 'google_email', 'google_disabled'}
_COUNTER = {'n': 0}


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {}

    def json(self):
        return self._data


def _email(tag='g'):
    _COUNTER['n'] += 1
    return 'google-%s-%03d@example.com' % (tag, _COUNTER['n'])


@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config.update(GOOGLE_CLIENT_ID=_CLIENT_ID, GOOGLE_CLIENT_SECRET=_CLIENT_SECRET,
                      GOOGLE_REDIRECT_URI=_REDIRECT)
    with app.app_context():
        yield app


def _start(client):
    resp = client.get('/auth/google/start')
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        return sess['google_oauth']['state']


def _callback(client, state, *, userinfo=None, token=None, userinfo_resp=None, query=None, post_exc=None):
    """Вызвать callback с подменёнными ответами Google. Возвращает (response, post_mock, get_mock)."""
    token = token if token is not None else _Resp(200, {'access_token': _TOKEN})
    userinfo_resp = userinfo_resp if userinfo_resp is not None else _Resp(200, userinfo or {})
    q = dict(query) if query is not None else {'state': state, 'code': _CODE}
    with patch('requests.post', side_effect=post_exc, return_value=token) as p, \
            patch('requests.get', return_value=userinfo_resp) as g:
        resp = client.get('/auth/google/callback', query_string=q)
    return resp, p, g


def _full_login(app, email, **kwargs):
    client = app.test_client()
    state = _start(client)
    resp, p, g = _callback(client, state, userinfo={'email': email, 'email_verified': True}, **kwargs)
    return client, resp


def _loc(resp):
    return resp.headers['Location']


def _user(email):
    from app.models.user import User
    return User.query.filter_by(email=email).first()


def _logged_in(client):
    with client.session_transaction() as sess:
        return sess.get('user_id')


# ---------------------------------------------------------------------------
# providers / start
# ---------------------------------------------------------------------------

def test_providers_false_without_config_and_true_with_config(app_ctx):
    assert app_ctx.test_client().get('/api/auth/providers').get_json() == {'google': True}
    for missing in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI'):
        saved = app_ctx.config[missing]
        app_ctx.config[missing] = None
        assert app_ctx.test_client().get('/api/auth/providers').get_json() == {'google': False}
        app_ctx.config[missing] = saved


def test_providers_response_has_no_secrets(app_ctx):
    body = app_ctx.test_client().get('/api/auth/providers').get_data(as_text=True)
    assert _CLIENT_SECRET not in body and _CLIENT_ID not in body


def test_start_without_config_redirects_to_login_with_error(app_ctx):
    app_ctx.config['GOOGLE_CLIENT_SECRET'] = None
    resp = app_ctx.test_client().get('/auth/google/start')
    assert resp.status_code == 302 and _loc(resp).endswith('/login.html?error=google_unavailable')


def test_start_redirects_to_google_with_required_params_and_saves_state(app_ctx):
    client = app_ctx.test_client()
    resp = client.get('/auth/google/start')
    assert resp.status_code == 302
    url = urlparse(_loc(resp))
    assert (url.scheme, url.netloc, url.path) == ('https', 'accounts.google.com', '/o/oauth2/v2/auth')
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q['client_id'] == _CLIENT_ID and q['redirect_uri'] == _REDIRECT
    assert q['response_type'] == 'code' and q['scope'] == 'openid email' and q['prompt'] == 'select_account'
    assert len(q['state']) >= 32
    assert _CLIENT_SECRET not in _loc(resp)
    with client.session_transaction() as sess:
        assert sess['google_oauth']['state'] == q['state'] and sess['google_oauth']['ts']


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def test_callback_without_start_or_state_is_rejected(app_ctx):
    client = app_ctx.test_client()
    resp, p, g = _callback(client, None, query={'code': _CODE})
    assert _loc(resp).endswith('error=google_state')
    assert p.call_count == 0 and g.call_count == 0 and not _logged_in(client)


def test_callback_with_foreign_state_is_rejected(app_ctx):
    client = app_ctx.test_client()
    _start(client)
    resp, p, g = _callback(client, 'not-the-real-state', userinfo={'email': _email(), 'email_verified': True})
    assert _loc(resp).endswith('error=google_state')
    assert p.call_count == 0 and not _logged_in(client)


def test_callback_with_non_ascii_state_does_not_crash(app_ctx):
    client = app_ctx.test_client()
    _start(client)
    resp, p, g = _callback(client, 'é-состояние', userinfo={})
    assert resp.status_code == 302 and _loc(resp).endswith('error=google_state')


def test_callback_with_expired_state_is_rejected(app_ctx):
    client = app_ctx.test_client()
    state = _start(client)
    import time as real_time
    later = real_time.time() + 601
    with patch('app.routes.google_auth.time.time', return_value=later):
        resp, p, g = _callback(client, state, userinfo={'email': _email(), 'email_verified': True})
    assert _loc(resp).endswith('error=google_state')
    assert p.call_count == 0 and not _logged_in(client)


def test_state_is_single_use_after_success(app_ctx):
    client = app_ctx.test_client()
    state = _start(client)
    email = _email('reuse')
    first, _, _ = _callback(client, state, userinfo={'email': email, 'email_verified': True})
    assert _loc(first).endswith('/dashboard.html')
    second, p, _ = _callback(client, state, userinfo={'email': email, 'email_verified': True})
    assert _loc(second).endswith('error=google_state') and p.call_count == 0


def test_state_is_single_use_after_failure(app_ctx):
    client = app_ctx.test_client()
    state = _start(client)
    first, _, _ = _callback(client, state, token=_Resp(500, {}))
    assert _loc(first).endswith('error=google_failed')
    second, p, _ = _callback(client, state, userinfo={'email': _email(), 'email_verified': True})
    assert _loc(second).endswith('error=google_state') and p.call_count == 0


# ---------------------------------------------------------------------------
# отказ пользователя и ошибки Google
# ---------------------------------------------------------------------------

def test_callback_with_error_param_is_denied(app_ctx):
    client = app_ctx.test_client()
    state = _start(client)
    resp, p, g = _callback(client, state, query={'error': 'access_denied', 'state': state})
    assert _loc(resp).endswith('error=google_denied')
    assert p.call_count == 0 and not _logged_in(client)


def test_token_exchange_non_200_is_google_failed(app_ctx):
    client = app_ctx.test_client()
    resp, _, g = _callback(client, _start(client), token=_Resp(400, {'error': 'invalid_grant'}))
    assert _loc(resp).endswith('error=google_failed') and g.call_count == 0 and not _logged_in(client)


def test_token_exchange_network_error_is_google_failed(app_ctx):
    client = app_ctx.test_client()
    resp, _, _ = _callback(client, _start(client), post_exc=requests.ConnectionError('boom'))
    assert _loc(resp).endswith('error=google_failed') and not _logged_in(client)


def test_token_response_without_access_token_is_google_failed(app_ctx):
    client = app_ctx.test_client()
    resp, _, _ = _callback(client, _start(client), token=_Resp(200, {}))
    assert _loc(resp).endswith('error=google_failed')


def test_userinfo_non_200_is_google_failed(app_ctx):
    client = app_ctx.test_client()
    resp, _, _ = _callback(client, _start(client), userinfo_resp=_Resp(401, {}))
    assert _loc(resp).endswith('error=google_failed') and not _logged_in(client)


def test_token_exchange_sends_expected_request(app_ctx):
    client = app_ctx.test_client()
    resp, p, g = _callback(client, _start(client), userinfo={'email': _email('req'), 'email_verified': True})
    args, kwargs = p.call_args
    assert args[0] == 'https://oauth2.googleapis.com/token' and kwargs['timeout'] == 10
    assert kwargs['data'] == {'code': _CODE, 'client_id': _CLIENT_ID, 'client_secret': _CLIENT_SECRET,
                              'redirect_uri': _REDIRECT, 'grant_type': 'authorization_code'}
    gargs, gkwargs = g.call_args
    assert gargs[0] == 'https://openidconnect.googleapis.com/v1/userinfo' and gkwargs['timeout'] == 10
    assert gkwargs['headers'] == {'Authorization': 'Bearer ' + _TOKEN}


# ---------------------------------------------------------------------------
# email
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('info', [
    {'email': 'x-unverified@example.com', 'email_verified': False},
    {'email': 'x-nofield@example.com'},
    {'email': 'x-str-false@example.com', 'email_verified': 'false'},
    {'email': 'x-one@example.com', 'email_verified': 1},
    {'email_verified': True},
    {'email': '   ', 'email_verified': True},
    {'email': 123, 'email_verified': True},
])
def test_unverified_or_missing_email_is_rejected_and_user_not_created(app_ctx, info):
    from app.models.user import User
    before = User.query.count()
    client = app_ctx.test_client()
    resp, _, _ = _callback(client, _start(client), userinfo=info)
    assert _loc(resp).endswith('error=google_email')
    assert User.query.count() == before and not _logged_in(client)


def test_email_verified_string_true_is_accepted(app_ctx):
    email = _email('strtrue')
    client = app_ctx.test_client()
    resp, _, _ = _callback(client, _start(client), userinfo={'email': email, 'email_verified': 'true'})
    assert _loc(resp).endswith('/dashboard.html') and _user(email) is not None


def test_email_is_stripped_and_lowercased(app_ctx):
    client = app_ctx.test_client()
    resp, _, _ = _callback(client, _start(client),
                           userinfo={'email': '  Mixed.Google@Example.COM ', 'email_verified': True})
    assert _loc(resp).endswith('/dashboard.html')
    assert _user('mixed.google@example.com') is not None


# ---------------------------------------------------------------------------
# аккаунты
# ---------------------------------------------------------------------------

def test_new_user_is_created_with_credits_and_logged_in(app_ctx):
    email = _email('new')
    client, resp = _full_login(app_ctx, email)
    assert resp.status_code == 302 and _loc(resp).endswith('/dashboard.html')
    user = _user(email)
    assert user is not None and user.is_email_verified is True and user.last_login_at is not None
    assert user.get_active_subscription().credits_granted == 2
    with client.session_transaction() as sess:
        assert sess['user_id'] == user.id and sess['user_email'] == email
        assert sess['user_name'] == email.split('@')[0] and sess['premium'] is False
        assert sess.permanent is True and 'google_oauth' not in sess
    me = client.get('/api/user/me')                          # сессия настоящая: страницы кабинета её примут
    assert me.status_code == 200 and me.get_json()['subscription']['credits_remaining'] == 2


def test_new_user_gets_pending_payment_credited(app_ctx):
    from app.models.payment import Payment
    email = _email('pending')
    _COUNTER['n'] += 1
    db.session.add(Payment(user_id=None, payer_email=email, amount=9.99, currency='usd',
                           status='completed_unclaimed', provider='lemonsqueezy', plan_name='credits5',
                           external_id='google-pending-%03d' % _COUNTER['n'],
                           description='Lemon Squeezy order (unclaimed, waiting for registration)'))
    db.session.commit()
    _full_login(app_ctx, email)
    assert _user(email).get_active_subscription().credits_granted == 2 + 10


def test_existing_verified_user_logs_in_without_password_change(app_ctx):
    from app.services.auth_service import AuthService
    email = _email('verified')
    user = AuthService.register(email, _PASSWORD)['user']
    user.is_email_verified = True
    db.session.commit()
    old_hash = user.password_hash
    client, resp = _full_login(app_ctx, email)
    assert _loc(resp).endswith('/dashboard.html') and _logged_in(client) == user.id
    assert _user(email).password_hash == old_hash
    assert AuthService.login(email, _PASSWORD)['success'] is True


def test_existing_unverified_user_gets_verified_and_old_password_stops_working(app_ctx):
    from app.services.auth_service import AuthService
    email = _email('unverified')
    user = AuthService.register(email, _PASSWORD)['user']
    assert user.is_email_verified is False
    old_hash = user.password_hash
    client, resp = _full_login(app_ctx, email)
    assert _loc(resp).endswith('/dashboard.html') and _logged_in(client) == user.id
    fresh = _user(email)
    assert fresh.is_email_verified is True and fresh.password_hash != old_hash
    assert AuthService.login(email, _PASSWORD)['success'] is False


def test_inactive_user_is_disabled(app_ctx):
    from app.services.auth_service import AuthService
    email = _email('inactive')
    user = AuthService.register(email, _PASSWORD)['user']
    user.is_active = False
    db.session.commit()
    client, resp = _full_login(app_ctx, email)
    assert _loc(resp).endswith('error=google_disabled') and not _logged_in(client)


# ---------------------------------------------------------------------------
# приватность и фиксированные коды
# ---------------------------------------------------------------------------

def test_logs_do_not_contain_code_token_secret_or_email(app_ctx, caplog):
    caplog.set_level(logging.DEBUG)
    new_email, bad_email = _email('logs'), _email('logs-bad')
    _full_login(app_ctx, new_email)                                                     # успех
    client = app_ctx.test_client()
    _callback(client, _start(client), token=_Resp(500, {}))                             # сбой обмена
    client = app_ctx.test_client()
    _callback(client, _start(client), userinfo={'email': bad_email, 'email_verified': False})   # email не подтверждён
    for secret in (_CODE, _TOKEN, _CLIENT_SECRET, new_email, bad_email):
        assert secret not in caplog.text, secret


def test_all_error_redirects_use_only_fixed_codes(app_ctx):
    seen = set()
    client = app_ctx.test_client()
    seen.add(_loc(_callback(client, None, query={'code': _CODE})[0]))                   # state
    client = app_ctx.test_client()
    state = _start(client)
    seen.add(_loc(_callback(client, state, query={'error': 'access_denied; <script>', 'state': state})[0]))
    client = app_ctx.test_client()
    seen.add(_loc(_callback(client, _start(client), token=_Resp(500, {}))[0]))
    client = app_ctx.test_client()
    seen.add(_loc(_callback(client, _start(client), userinfo={'email': 'a@b.com', 'email_verified': False})[0]))
    app_ctx.config['GOOGLE_CLIENT_ID'] = None
    seen.add(_loc(app_ctx.test_client().get('/auth/google/start')))
    codes = {u.split('error=')[1] for u in seen}
    assert codes <= _ERROR_CODES and len(codes) == 5
    assert all(u.startswith('/login.html?error=') and '&' not in u for u in seen)
