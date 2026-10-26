"""
Страницы входа и кабинета: login.html, dashboard.html, history.html. Без сети и без браузера.
  - статические проверки текста страниц
  - node --check для инлайн-скриптов (если node есть)
  - контракт сервера, на который опираются страницы (/api/user/register|login|me|logout)
Запуск: FLASK_ENV=testing python -m pytest tests/test_frontend_auth_pages.py -q
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PAGES = ('login.html', 'dashboard.html', 'history.html')


def _read(name):
    with open(os.path.join(_ROOT, name), encoding='utf-8') as f:
        return f.read()


def _scripts(html):
    return re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)


# ---------------------------------------------------------------------------
# login.html
# ---------------------------------------------------------------------------

def test_login_has_no_fake_auth_or_github_buttons():
    html = _read('login.html')
    cleaned = html.replace("localStorage.removeItem('rai_users')", '').replace("localStorage.removeItem('rai_session')", '')
    for key in ('rai_users', 'rai_session'):
        assert key not in cleaned, key           # ключ допустим только внутри removeItem
    for bad in ('socialLogin', 'btn-github', 'Continue with GitHub', 'GitHub', 'github'):
        assert bad not in html, bad       # кнопка Google разрешена (см. тесты ниже), GitHub нет
    assert 'coming soon' not in html.lower()


def test_login_removes_legacy_keys_and_uses_server_api():
    html = _read('login.html')
    assert "localStorage.removeItem('rai_users')" in html
    assert "localStorage.removeItem('rai_session')" in html
    assert '/api/user/login' in html and '/api/user/register' in html and '/api/user/me' in html
    assert 'Full Name' not in html and 'rName' not in html
    assert 'localStorage.setItem' not in html     # на странице входа в localStorage больше ничего не пишется


def test_login_password_minimum_matches_server_validator():
    from app.utils.validators import validate_password
    html = _read('login.html')
    assert 'const MIN_PASSWORD = 8' in html and 'min. 8 characters' in html
    assert validate_password('1234567')[0] is False and validate_password('12345678')[0] is True


def test_login_server_messages_are_shown_via_textcontent():
    html = _read('login.html')
    assert 'e.textContent=m' in html
    assert not re.search(r'\.innerHTML\s*=', html)


# ---------------------------------------------------------------------------
# dashboard.html / history.html
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['dashboard.html', 'history.html'])
def test_pages_use_server_auth_not_local_session(name):
    html = _read(name)
    assert '/api/user/me' in html and '/api/user/logout' in html
    assert "getItem('rai_session')" not in html and 'getItem("rai_session")' not in html
    assert '/login.html' in html                  # редирект при 401
    assert "localStorage.getItem('rai_history')" in html   # история по-прежнему в браузере


@pytest.mark.parametrize('name', ['dashboard.html', 'history.html'])
def test_pages_do_not_inject_data_into_innerhtml(name):
    code = '\n'.join(_scripts(_read(name)))
    # любая подстановка ${...} допустима только внутри esc(...)
    for expr in re.findall(r'\$\{([^}]*)\}', code):
        assert expr.strip().startswith('esc('), expr
    # присваивания innerHTML: только пустая строка или статичный литерал без переменных
    for rhs in re.findall(r'\.innerHTML\s*=\s*([^;\n]+);', code):
        rhs = rhs.strip()
        assert re.fullmatch(r"'[^']*'", rhs) and '${' not in rhs, rhs
    assert 'insertAdjacentHTML' not in code and 'document.write' not in code


def test_dashboard_has_credits_card_and_buy_link():
    html = _read('dashboard.html')
    assert 'Credits left' in html and 'id="statCredits"' in html
    assert 'href="/#pricing"' in html and 'Buy credits' in html
    assert 'credits_remaining' in html


# ---------------------------------------------------------------------------
# node --check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', _PAGES)
def test_inline_scripts_pass_node_check(name):
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден в окружении: проверка синтаксиса инлайн-скриптов пропущена')
    scripts = _scripts(_read(name))
    assert scripts
    for i, code in enumerate(scripts):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tmp:
            tmp.write(code)
        try:
            res = subprocess.run([node, '--check', tmp.name], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(tmp.name)
        assert res.returncode == 0, '%s script #%d: %s' % (name, i, res.stderr[:400])


# ---------------------------------------------------------------------------
# Контракт сервера, на который опираются страницы
# ---------------------------------------------------------------------------

@pytest.fixture
def app_ctx():
    from app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app


def test_server_contract_register_login_me_logout(app_ctx):
    email, password = 'auth-pages-contract@example.com', 'a-long-enough-password'

    reg = app_ctx.test_client().post('/api/user/register', json={'email': email, 'password': password})
    assert reg.status_code == 201 and reg.get_json()['success'] is True

    client = app_ctx.test_client()                      # новый клиент: настоящий вход без cookie регистрации
    assert client.get('/api/user/me').status_code == 401

    login = client.post('/api/user/login', json={'email': email, 'password': password})
    assert login.status_code == 200 and login.get_json()['success'] is True

    me = client.get('/api/user/me')
    body = me.get_json()
    assert me.status_code == 200 and body['success'] is True
    assert body['user']['name'] == 'auth-pages-contract' and body['user']['id'] is not None
    assert body['subscription']['credits_remaining'] == 2

    out = client.post('/api/user/logout')
    assert out.status_code == 200 and out.get_json()['success'] is True
    assert client.get('/api/user/me').status_code == 401


def test_server_contract_wrong_password_returns_error_text(app_ctx):
    email = 'auth-pages-wrongpass@example.com'
    app_ctx.test_client().post('/api/user/register', json={'email': email, 'password': 'a-long-enough-password'})
    resp = app_ctx.test_client().post('/api/user/login', json={'email': email, 'password': 'wrong-password'})
    data = resp.get_json()
    assert resp.status_code == 401 and data['success'] is False and data['error']   # страница показывает data.error


# ---------------------------------------------------------------------------
# Кнопка «Continue with Google» (серверный OAuth) на login.html
# ---------------------------------------------------------------------------

def test_login_google_button_is_a_link_to_start_only_and_hidden_by_default():
    html = _read('login.html')
    assert 'Continue with Google' in html
    hrefs = set(re.findall(r'href="(/auth/google/[^"]*)"', html))
    assert hrefs == {'/auth/google/start'}, hrefs
    tag = re.search(r'<a [^>]*id="googleBtn"[^>]*>', html).group(0)
    assert 'display:none' in tag and 'href="/auth/google/start"' in tag
    assert re.search(r'id="googleDivider"[^>]*display:none', html)
    assert 'onclick' not in tag


def test_login_shows_google_button_only_when_server_reports_it():
    html = _read('login.html')
    assert '/api/auth/providers' in html and 'd.google === true' in html


def test_login_has_no_google_secrets_or_client_ids():
    low = _read('login.html').lower()
    for bad in ('client_secret', 'google_client_secret', 'client_id', 'googleusercontent', 'oauth2.googleapis.com'):
        assert bad not in low, bad


def test_login_error_codes_are_mapped_through_fixed_table():
    html = _read('login.html')
    for code in ('google_unavailable', 'google_denied', 'google_state', 'google_failed', 'google_email', 'google_disabled'):
        assert code + ':' in html, code
    assert 'hasOwnProperty.call(GOOGLE_ERRORS' in html       # значение параметра из адреса в разметку не попадает
    assert 'e.textContent = GOOGLE_ERRORS[code]' in html
    assert not re.search(r'\.innerHTML\s*=', html)
