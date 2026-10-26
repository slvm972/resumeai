# app/routes/google_auth.py
"""
Вход через Google (OAuth 2.0, authorization code на сервере). Новых зависимостей нет: только requests.

Маршруты:
  GET /api/auth/providers        -> {"google": true|false}
  GET /auth/google/start         -> 302 на accounts.google.com
  GET /auth/google/callback      -> обмен code на токен, userinfo, вход, 302 на /dashboard.html

Безопасность: state одноразовый (pop из сессии), сравнение через hmac.compare_digest, срок 10 минут;
минимальный scope "openid email"; требуется email_verified; в логи не пишутся code, токены, email
и ответы Google (только короткие коды причин); в query редиректов ошибок только фиксированные коды.
"""
import hmac
import logging
import re
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from flask import Blueprint, current_app, jsonify, redirect, request, session

from app import db

logger = logging.getLogger(__name__)
google_auth_bp = Blueprint('google_auth', __name__)

_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
_TOKEN_URL = 'https://oauth2.googleapis.com/token'
_USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'
_STATE_MAX_AGE_SECONDS = 600
_HTTP_TIMEOUT = 10


def _cfg(key):
    """Значение настройки без пробелов и переводов строки по краям (частая ошибка при вставке в панель хостинга)."""
    value = current_app.config.get(key)
    return value.strip() if isinstance(value, str) else value


def _is_configured():
    return all(_cfg(k) for k in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI'))


def _google_error_fields(resp):
    """
    Из ответа Google берём только поля error / error_description (обрезаны, без '@' и без лишних символов).
    Тело ответа целиком, токены и коды не логируются никогда.
    """
    try:
        data = resp.json()
    except ValueError:
        return '-', '-'
    if not isinstance(data, dict):
        return '-', '-'
    error = data.get('error')
    error = error if isinstance(error, str) and re.fullmatch(r'[a-z_]{1,40}', error) else '-'
    desc = data.get('error_description')
    if isinstance(desc, str) and '@' not in desc:
        desc = re.sub(r'[^A-Za-z0-9 .,:_/-]', '', desc)[:100].strip() or '-'
    else:
        desc = '-'
    return error, desc


def _log_step(step, reason, resp=None):
    """Диагностика: шаг, причина, HTTP-статус и безопасные поля ошибки Google (без email, кодов и токенов)."""
    if resp is not None:
        error, desc = _google_error_fields(resp)
        logger.error('Google OAuth failed: step=%s reason=%s status=%s google_error=%s description=%s',
                     step, reason, resp.status_code, error, desc)
    else:
        logger.error('Google OAuth failed: step=%s reason=%s', step, reason)


def _fail(code):
    """Редирект на страницу входа с фиксированным кодом ошибки; в лог только код причины."""
    logger.warning('Google OAuth: %s', code)
    return redirect('/login.html?error=' + code)


@google_auth_bp.route('/api/auth/providers', methods=['GET'])
def providers():
    return jsonify({'google': bool(_is_configured())})


@google_auth_bp.route('/auth/google/start', methods=['GET'])
def google_start():
    if not _is_configured():
        return _fail('google_unavailable')
    state = secrets.token_urlsafe(32)
    session['google_oauth'] = {'state': state, 'ts': time.time()}
    params = {
        'client_id': _cfg('GOOGLE_CLIENT_ID'),
        'redirect_uri': _cfg('GOOGLE_REDIRECT_URI'),
        'response_type': 'code',
        'scope': 'openid email',          # минимальный набор: имя и фото не запрашиваем
        'state': state,
        'prompt': 'select_account',
    }
    return redirect(_AUTH_URL + '?' + urlencode(params))


def _state_is_valid(stored, received):
    if not isinstance(stored, dict) or not stored.get('state') or not received:
        return False
    try:
        age = time.time() - float(stored.get('ts', 0))
    except (TypeError, ValueError):
        return False
    if age < 0 or age > _STATE_MAX_AGE_SECONDS:
        return False
    return hmac.compare_digest(str(stored['state']).encode('utf-8'), received.encode('utf-8'))


def _fetch_google_email(code):
    """Вернуть (email, None) или (None, код_ошибки). Ничего из ответов Google целиком не логируется."""
    try:
        token_resp = requests.post(_TOKEN_URL, data={
            'code': code,
            'client_id': _cfg('GOOGLE_CLIENT_ID'),
            'client_secret': _cfg('GOOGLE_CLIENT_SECRET'),
            'redirect_uri': _cfg('GOOGLE_REDIRECT_URI'),
            'grant_type': 'authorization_code',
        }, timeout=_HTTP_TIMEOUT)
        if token_resp.status_code != 200:
            _log_step('token', 'http_status', token_resp)
            return None, 'google_failed'
        access_token = (token_resp.json() or {}).get('access_token')
        if not access_token or not isinstance(access_token, str):
            _log_step('token', 'no_access_token', token_resp)
            return None, 'google_failed'

        info_resp = requests.get(_USERINFO_URL, headers={'Authorization': 'Bearer ' + access_token},
                                 timeout=_HTTP_TIMEOUT)
        if info_resp.status_code != 200:
            _log_step('userinfo', 'http_status', info_resp)
            return None, 'google_failed'
        info = info_resp.json() or {}
    except requests.RequestException as exc:
        # Только имя класса исключения: текст ошибки может содержать адреса и параметры запроса.
        _log_step('network', type(exc).__name__)
        return None, 'google_failed'
    except ValueError:
        _log_step('response', 'invalid_json')
        return None, 'google_failed'

    verified = info.get('email_verified')
    email = info.get('email')
    if not (verified is True or verified == 'true') or not isinstance(email, str) or not email.strip():
        logger.error('Google OAuth failed: step=userinfo reason=email_not_usable verified=%s has_email=%s',
                     verified is True or verified == 'true', isinstance(email, str) and bool(email.strip()))
        return None, 'google_email'
    return email.strip().lower(), None


@google_auth_bp.route('/auth/google/callback', methods=['GET'])
def google_callback():
    from app.models.user import User
    from app.services.auth_service import AuthService

    if not _is_configured():
        return _fail('google_unavailable')

    stored = session.pop('google_oauth', None)          # state одноразовый: удаляем при любом исходе
    if request.args.get('error'):
        return _fail('google_denied')
    if not _state_is_valid(stored, request.args.get('state', '')):
        return _fail('google_state')

    code = request.args.get('code', '')
    if not code:
        return _fail('google_failed')

    email, error_code = _fetch_google_email(code)
    if error_code:
        return _fail(error_code)

    user = User.query.filter_by(email=email).first()
    if user is None:
        # Через AuthService.register: те же 2 бесплатных кредита и зачисление ожидающих платежей по почте.
        result = AuthService.register(email, secrets.token_urlsafe(32))
        if not result.get('success'):
            # error в AuthService.register - фиксированные тексты без email
            logger.error('Google OAuth failed: step=register reason=%s', result.get('error'))
            return _fail('google_failed')
        user = result['user']
        user.is_email_verified = True
    else:
        if not user.is_active:
            return _fail('google_disabled')
        if not user.is_email_verified:
            # Аккаунт мог быть создан кем-то с чужой почтой без подтверждения: подтверждаем и
            # меняем пароль на случайный, чтобы ранее заданный пароль перестал работать.
            user.is_email_verified = True
            user.set_password(secrets.token_urlsafe(32))
    user.last_login_at = datetime.utcnow()
    db.session.commit()

    # Те же ключи сессии, что ставит /api/user/login (_do_login в app/__init__.py)
    session.clear()
    session['user_id'] = user.id
    session['user_email'] = user.email
    session['user_name'] = user.email.split('@')[0]
    session['premium'] = user.get_plan_name() != 'free'
    session.permanent = True
    return redirect('/dashboard.html')
