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
    # Реалистичный quality_report (форма — 1:1 с тем, что реально строит
    # _run_improve_pipeline в app/missing_routes4.py) с summary.accepted=1:
    # None здесь читался бы нашим же фиксом no_real_improvement (коммит
    # 4af09e6) как "0 improvements accepted" и не списывал бы кредит — эти
    # тесты проверяют механику пула/счётчиков именно при РЕАЛЬНОМ
    # улучшении, а не пустой результат.
    'quality_report': {
        'total_blocks': 1,
        'retry_triggered': 0,
        'retry_ids': [],
        'blocks': [{
            'id': '001',
            'attempt': 'attempt_1',
            'strategy': 'improve',
            'decision': 'accepted',
            'reason': 'accepted similarity=0.800 changed_words=2 len_delta=10.0%',
            'similarity': 0.8,
            'original_text': 'original text',
            'improved_text': 'improved text',
        }],
        'changes': [{
            'id': '001',
            'original_text': 'original text',
            'improved_text': 'improved text',
        }],
        'summary': {
            'accepted': 1,
            'kept_original': 0,
            'rejected_facts': 0,
            'rejected_role_escalation': 0,
            'needs_retry': 0,
            'avg_similarity': 0.8,
        },
    },
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


def test_improve_docx_not_blocked_when_pool_exhausted(client_app):
    """Экспорт результата уже оплаченного улучшения не зависит от остатка
    кредитов: пул исчерпан -> /api/improve/docx всё равно отдаёт 200
    (раньше здесь был 403 "No credits remaining")."""
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
    assert resp.status_code == 200
    assert 'wordprocessingml' in resp.content_type


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


# ===========================================================================
# Шаг 4/6 — webhooks.py пополняет единый пул на +10
# ===========================================================================

def test_lemonsqueezy_webhook_tops_up_unified_pool(client_app, monkeypatch):
    """
    Реальный HMAC (не мок compare_digest) над телом запроса тестовым
    секретом -> POST /webhooks/lemonsqueezy имитирует оплаченный
    order_created для существующего пользователя с credits_granted=2.
    Ожидание: credits_granted==12 (2 стартовых + 10 за покупку),
    improvement_credits==5 (легаси-поле тоже выросло, не заменено).
    """
    import hmac
    import hashlib
    import json as json_lib

    from app.services.auth_service import AuthService

    app = client_app
    client = app.test_client()

    webhook_secret = 'test-webhook-secret'
    app.config['LEMONSQUEEZY_WEBHOOK_SECRET'] = webhook_secret

    result = AuthService.register('webhook-pool-test@example.com', 'somepassword123')
    user = result['user']
    sub = user.get_active_subscription()
    assert sub.credits_granted == 2
    assert sub.improvement_credits == 0

    payload = {
        'data': {
            'attributes': {
                'identifier': 'order-webhook-pool-test-001',
                'user_email': 'webhook-pool-test@example.com',
                'status': 'paid',
                'total': 999,  # центы -> $9.99, ожидаемая цена пакета
                'currency': 'usd',
            }
        }
    }
    raw_body = json_lib.dumps(payload).encode('utf-8')
    signature = hmac.new(webhook_secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()

    resp = client.post(
        '/webhooks/lemonsqueezy',
        data=raw_body,
        content_type='application/json',
        headers={'X-Signature': signature, 'X-Event-Name': 'order_created'},
    )
    assert resp.status_code == 200

    sub = user.get_active_subscription()
    assert sub.credits_granted == 12
    assert sub.improvement_credits == 5
    assert sub.credits_remaining() == 12


def test_lemonsqueezy_webhook_rejects_bad_signature(client_app):
    """Regression guard: неверная подпись -> 401, пул не трогается."""
    import json as json_lib

    from app.services.auth_service import AuthService

    app = client_app
    client = app.test_client()
    app.config['LEMONSQUEEZY_WEBHOOK_SECRET'] = 'test-webhook-secret'

    result = AuthService.register('webhook-badsig-test@example.com', 'somepassword123')
    user = result['user']

    payload = {
        'data': {
            'attributes': {
                'identifier': 'order-bad-sig-001',
                'user_email': 'webhook-badsig-test@example.com',
                'status': 'paid',
                'total': 999,
                'currency': 'usd',
            }
        }
    }
    raw_body = json_lib.dumps(payload).encode('utf-8')

    resp = client.post(
        '/webhooks/lemonsqueezy',
        data=raw_body,
        content_type='application/json',
        headers={'X-Signature': 'not-a-real-signature', 'X-Event-Name': 'order_created'},
    )
    assert resp.status_code == 401

    sub = user.get_active_subscription()
    assert sub.credits_granted == 2
    assert sub.improvement_credits == 0


# ===========================================================================
# Шаг 5/6 — /api/analyze возвращает is_admin и credits_remaining
# ===========================================================================

def test_analyze_response_includes_credits_remaining_for_regular_user(client_app):
    """
    Regression-тест на найденный и подтверждённый в Шаге 5 баг: ответ
    /api/analyze не содержал is_admin/credits_remaining, из-за чего
    фронтенд показывал зарегистрированному пользователю тот же урезанный
    вид, что и гостю.
    """
    from unittest.mock import patch

    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'analyze-response-fields-test@example.com')

    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        return_value=_FAKE_ANALYZE_RESULT,
    ):
        resp = client.post('/api/analyze', json={'resume_text': 'A' * 30})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['is_admin'] is False
    # Свежий пользователь: 2 кредита выданы, 1 потрачен на этот же analyze
    sub = user.get_active_subscription()
    assert body['credits_remaining'] == sub.credits_remaining() == 1


def test_analyze_response_credits_remaining_null_for_admin_session(client_app):
    """Admin-сессия: is_admin=True, credits_remaining=None (квота не считается)."""
    from unittest.mock import patch

    app = client_app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['admin'] = 'admin'

    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        return_value=_FAKE_ANALYZE_RESULT,
    ):
        resp = client.post('/api/analyze', json={'resume_text': 'A' * 30})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['is_admin'] is True
    assert body['credits_remaining'] is None


def test_admin_analyze_endpoint_response_includes_is_admin_true(client_app):
    """/api/admin/analyze (панель admin.html) тоже отдаёт is_admin=True."""
    from unittest.mock import patch

    app = client_app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['admin'] = 'admin'

    with patch(
        'app.services.openrouter_service.OpenRouterService.analyze_resume',
        return_value=_FAKE_ANALYZE_RESULT,
    ):
        resp = client.post('/api/admin/analyze', json={'resume_text': 'A' * 30})

    assert resp.status_code == 200
    assert resp.get_json()['is_admin'] is True


# ===========================================================================
# Regression — legacy_admin_improve больше не пишет original_docx_token
# в session для не-.docx загрузок (session['original_docx_token'] нужен
# только legacy_admin_improve_docx для .docx-реконструкции; для PDF/DOC он
# бесполезно тратил бы disk-write, и потенциально привёл бы к попытке
# распарсить не-docx байты через python-docx, если бы дошло до
# _apply_improved_text_to_docx — по факту кода до этого не доходит, см.
# docstring теста ниже, но условие сужено на источнике).
# ===========================================================================

def test_admin_improve_non_docx_upload_does_not_set_session_token(client_app):
    """
    /api/admin/improve с .pdf-файлом -> session['original_docx_token'] НЕ
    устанавливается (сужение условия: session-запись срабатывает только для
    filename.lower().endswith('.docx')).
    """
    from unittest.mock import patch
    import io as _io

    app = client_app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['admin'] = 'admin'

    with patch(
        'app.missing_routes4._run_improve_pipeline',
        return_value=_FAKE_IMPROVE_RESULT,
    ):
        resp = client.post(
            '/api/admin/improve',
            data={'file': (_io.BytesIO(b'%PDF-1.4 fake pdf content'), 'resume.pdf')},
            content_type='multipart/form-data',
        )

    assert resp.status_code == 200, resp.get_json()

    with client.session_transaction() as sess:
        assert 'original_docx_token' not in sess
        # item_ids тоже не должен записываться в session для не-docx —
        # он пишется в той же ветке if, что и token.
        assert 'item_ids' not in sess


def test_admin_improve_docx_upload_still_sets_session_token(client_app):
    """
    Regression guard: для реального .docx-сценария поведение не изменилось —
    session['original_docx_token'] по-прежнему устанавливается.
    """
    from unittest.mock import patch
    import io as _io

    app = client_app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['admin'] = 'admin'

    with patch(
        'app.missing_routes4._run_improve_pipeline',
        return_value=_FAKE_IMPROVE_RESULT,
    ):
        resp = client.post(
            '/api/admin/improve',
            data={'file': (_io.BytesIO(b'PK\x03\x04 fake docx content'), 'resume.docx')},
            content_type='multipart/form-data',
        )

    assert resp.status_code == 200, resp.get_json()

    with client.session_transaction() as sess:
        assert 'original_docx_token' in sess
        assert sess['item_ids'] == _FAKE_IMPROVE_RESULT['item_ids']


# ===========================================================================
# Экспорт не зависит от остатка кредитов (docx / odt / pdf / rtf)
# ===========================================================================
# Контекст: /api/improve списывает последний кредит, после чего все четыре
# маршрута экспорта отдавали 403 "No credits remaining" — пользователь не мог
# скачать результат, за который уже заплатил. Блок проверки остатка удалён из
# экспортов; кредит по-прежнему списывается только в /api/improve.

_EXPORT_ENDPOINTS = [
    ('/api/improve/docx', 'wordprocessingml'),
    ('/api/improve/odt', 'opendocument.text'),
    ('/api/improve/pdf', 'application/pdf'),
    ('/api/improve/rtf', 'rtf'),
]
_EXPORT_TEXT = '###ITEM_001###\nJohn Doe\n\n###ITEM_002###\nSoftware Engineer'


@pytest.mark.parametrize('url,ctype', _EXPORT_ENDPOINTS)
def test_export_returns_200_with_zero_credits(client_app, url, ctype):
    """credits_remaining()==0 -> каждый из четырёх экспортов отдаёт 200,
    credits_used не меняется."""
    from app import db

    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'zero-credit-export-%s@example.com' % url.rsplit('/', 1)[1])

    sub = user.get_active_subscription()
    sub.credits_used = sub.credits_granted
    db.session.commit()
    assert sub.credits_remaining() == 0
    used_before = sub.credits_used

    resp = client.post(url, data={'improved_resume': _EXPORT_TEXT})

    assert resp.status_code == 200, (url, resp.get_json(silent=True))
    assert ctype in resp.content_type
    assert resp.data
    assert user.get_active_subscription().credits_used == used_before


@pytest.mark.parametrize('url,ctype', _EXPORT_ENDPOINTS)
def test_export_still_requires_login_401(client_app, url, ctype):
    """Без входа (и не admin/ADMIN_MODE) экспорт по-прежнему даёт 401."""
    client = client_app.test_client()
    resp = client.post(url, data={'improved_resume': _EXPORT_TEXT})
    assert resp.status_code == 401
    assert resp.get_json()['success'] is False


def test_improve_still_debits_blocks_at_empty_pool_and_export_survives(client_app):
    """Регрессия на исходный баг целиком: ровно 1 кредит -> /api/improve
    списывает его (credits_used 0->1), все четыре экспорта после этого
    отдают 200, а повторный /api/improve по-прежнему 403, без списания."""
    from unittest.mock import patch
    from app import db

    app = client_app
    client = app.test_client()
    user = _register_and_login(app, client, 'one-credit-flow-test@example.com')

    sub = user.get_active_subscription()
    sub.credits_granted = 1
    db.session.commit()
    assert sub.credits_remaining() == 1

    with patch(
        'app.missing_routes4._run_improve_pipeline',
        return_value=_FAKE_IMPROVE_RESULT,
    ):
        resp = client.post('/api/improve', json={'resume_text': 'A' * 30})
        assert resp.status_code == 200, resp.get_json()

        sub = user.get_active_subscription()
        assert sub.credits_used == 1
        assert sub.credits_remaining() == 0

        for url, ctype in _EXPORT_ENDPOINTS:
            r = client.post(url, data={'improved_resume': _EXPORT_TEXT})
            assert r.status_code == 200, (url, r.get_json(silent=True))
            assert ctype in r.content_type

        resp = client.post('/api/improve', json={'resume_text': 'A' * 30})
        assert resp.status_code == 403
        assert resp.get_json()['error'] == 'No credits remaining. Buy a credit pack to continue.'

    assert user.get_active_subscription().credits_used == 1


# ===========================================================================
# Платёж до регистрации: unclaimed-платёж начисляется при регистрации
# ===========================================================================
# Контекст: если email заказа не совпал с аккаунтом (или пользователь ещё не
# зарегистрирован), вебхук раньше создавал Payment без кредитов, и при
# регистрации их никто не начислял. Теперь вебхук пишет status
# 'completed_unclaimed', а AuthService.register начисляет +10 / +5 за каждый
# такой платёж в той же транзакции.

_WH_SECRET = 'test-webhook-secret'
_WH_COUNTER = {'n': 0}


def _post_ls_webhook(app, client, email, total=999):
    """Подписанный оплаченный order_created (подпись — как в существующих тестах)."""
    import hmac
    import hashlib
    import json as json_lib

    app.config['LEMONSQUEEZY_WEBHOOK_SECRET'] = _WH_SECRET
    _WH_COUNTER['n'] += 1
    order_id = 'order-claim-%03d' % _WH_COUNTER['n']
    payload = {'data': {'attributes': {
        'identifier': order_id, 'user_email': email,
        'status': 'paid', 'total': total, 'currency': 'usd',
    }}}
    raw = json_lib.dumps(payload).encode('utf-8')
    sig = hmac.new(_WH_SECRET.encode('utf-8'), raw, hashlib.sha256).hexdigest()
    resp = client.post('/webhooks/lemonsqueezy', data=raw, content_type='application/json',
                       headers={'X-Signature': sig, 'X-Event-Name': 'order_created'})
    assert resp.status_code == 200
    return order_id


def _add_payment(email, status='completed_unclaimed', provider='lemonsqueezy',
                 description='Lemon Squeezy order (unmatched user)', user_id=None):
    from app import db
    from app.models.payment import Payment

    _WH_COUNTER['n'] += 1
    p = Payment(user_id=user_id, payer_email=email, amount=9.99, currency='usd',
                status=status, provider=provider,
                external_id='manual-claim-%03d' % _WH_COUNTER['n'], description=description)
    db.session.add(p)
    db.session.commit()
    return p


def _get_payment(external_id):
    from app.models.payment import Payment
    return Payment.query.filter_by(external_id=external_id).first()


def test_CLAIM1_webhook_unknown_email_saves_unclaimed_and_changes_no_credits(client_app):
    from app.services.auth_service import AuthService

    app = client_app
    client = app.test_client()
    other = AuthService.register('claim1-other@example.com', 'somepassword123')['user']
    before = other.get_active_subscription().credits_granted

    order = _post_ls_webhook(app, client, 'claim1-nobody@example.com')

    p = _get_payment(order)
    assert p.status == 'completed_unclaimed'
    assert p.user_id is None
    assert p.payer_email == 'claim1-nobody@example.com'
    assert p.description == 'Lemon Squeezy order (unclaimed, waiting for registration)'
    assert other.get_active_subscription().credits_granted == before == 2


def test_CLAIM2_register_claims_unclaimed_payment(client_app):
    from app.services.auth_service import AuthService

    app = client_app
    client = app.test_client()
    order = _post_ls_webhook(app, client, 'claim2-buyer@example.com')

    result = AuthService.register('claim2-buyer@example.com', 'somepassword123')
    assert result['success']
    user = result['user']
    sub = user.get_active_subscription()
    assert sub.credits_granted == 2 + 10
    assert sub.improvement_credits == 5
    assert sub.credits_remaining() == 12

    p = _get_payment(order)
    assert p.user_id == user.id
    assert p.status == 'completed'
    assert p.description.endswith('(claimed at registration)')


@pytest.mark.parametrize('url', ['/auth/register', '/api/user/register'])
def test_CLAIM3_both_http_register_paths_claim_payment(client_app, url):
    from app.models.user import User

    app = client_app
    client = app.test_client()
    email = 'claim3-%s@example.com' % url.strip('/').replace('/', '-')
    order = _post_ls_webhook(app, client, email)

    resp = client.post(url, json={'email': email, 'password': 'somepassword123'})
    assert resp.status_code == 201, resp.get_json()

    user = User.query.filter_by(email=email).first()
    sub = user.get_active_subscription()
    assert sub.credits_granted == 12
    assert sub.improvement_credits == 5
    p = _get_payment(order)
    assert p.user_id == user.id and p.status == 'completed'


def test_CLAIM4_legacy_unmatched_completed_payment_is_claimed(client_app):
    """Старая запись (status 'completed', user_id None, '(unmatched user)')."""
    from app.services.auth_service import AuthService

    p = _add_payment('claim4-legacy@example.com', status='completed',
                     description='Lemon Squeezy order (unmatched user)')
    user = AuthService.register('claim4-legacy@example.com', 'somepassword123')['user']
    sub = user.get_active_subscription()
    assert sub.credits_granted == 12 and sub.improvement_credits == 5
    from app import db
    db.session.refresh(p)
    assert p.user_id == user.id and p.status == 'completed'
    assert p.description.endswith('(claimed at registration)')


def test_CLAIM5_two_unpaid_payments_give_plus_20(client_app):
    from app.services.auth_service import AuthService

    app = client_app
    client = app.test_client()
    _post_ls_webhook(app, client, 'claim5-two@example.com')
    _post_ls_webhook(app, client, 'claim5-two@example.com')

    user = AuthService.register('claim5-two@example.com', 'somepassword123')['user']
    sub = user.get_active_subscription()
    assert sub.credits_granted == 2 + 20
    assert sub.improvement_credits == 10


def test_CLAIM6_other_email_refunded_other_provider_not_claimed_case_insensitive_is(client_app):
    from app import db
    from app.services.auth_service import AuthService

    other = _add_payment('claim6-someone-else@example.com')
    refunded = _add_payment('claim6-me@example.com', status='refunded')
    stripe = _add_payment('claim6-me@example.com', provider='stripe')
    mixed = _add_payment('  Claim6-ME@Example.com ', status='completed_unclaimed')

    user = AuthService.register('claim6-me@example.com', 'somepassword123')['user']
    sub = user.get_active_subscription()
    assert sub.credits_granted == 2 + 10          # только mixed (регистр/пробелы не мешают)
    assert sub.improvement_credits == 5

    for p in (other, refunded, stripe, mixed):
        db.session.refresh(p)
    assert other.user_id is None and other.status == 'completed_unclaimed'
    assert refunded.user_id is None and refunded.status == 'refunded'
    assert stripe.user_id is None
    assert mixed.user_id == user.id and mixed.status == 'completed'


def test_CLAIM7_payment_cannot_be_claimed_twice(client_app):
    from app.services.auth_service import AuthService

    app = client_app
    client = app.test_client()
    order = _post_ls_webhook(app, client, 'claim7-once@example.com')

    first = AuthService.register('claim7-once@example.com', 'somepassword123')
    assert first['success']
    sub = first['user'].get_active_subscription()
    assert sub.credits_granted == 12

    again = AuthService.register('claim7-once@example.com', 'somepassword123')
    assert again['success'] is False
    assert again['error'] == 'Email already registered'
    assert first['user'].get_active_subscription().credits_granted == 12
    assert _get_payment(order).user_id == first['user'].id


def test_CLAIM8_known_user_webhook_unchanged_and_plain_register_gives_two(client_app):
    from app.services.auth_service import AuthService

    app = client_app
    client = app.test_client()

    plain = AuthService.register('claim8-plain@example.com', 'somepassword123')['user']
    sub = plain.get_active_subscription()
    assert sub.credits_granted == 2 and sub.improvement_credits == 0

    order = _post_ls_webhook(app, client, 'claim8-plain@example.com')
    p = _get_payment(order)
    assert p.status == 'completed' and p.user_id == plain.id
    assert p.description == 'Lemon Squeezy order'
    sub = plain.get_active_subscription()
    assert sub.credits_granted == 12 and sub.improvement_credits == 5
