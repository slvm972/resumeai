"""
Второй тарифный пакет (credits30), ЧАСТЬ 1: бэкенд. Без сети.
  - app/utils/plans.py: credits_for_plan, plan_for_order, parse_variant_map
  - webhooks.py: кредиты по купленному пакету (карта variant_id -> пакет, затем точная сумма)
  - auth_service.py: зачисление pending-платежей по plan_name при регистрации
Запуск: FLASK_ENV=testing python -m pytest tests/test_second_plan.py -q
"""
import hashlib
import hmac
import json as json_lib
import logging
import os
import sys
import importlib

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import config as config_module
importlib.reload(config_module)

from app import create_app
from app.utils.plans import credits_for_plan, parse_variant_map, plan_for_order

_WH_SECRET = 'test-webhook-secret'
_COUNTER = {'n': 0}


# ---------------------------------------------------------------------------
# credits_for_plan
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name, expected', [
    (None, 10), ('', 10), ('   ', 10), ('credits5', 10),
    ('credits30', 30),
    ('unknown', 0), ('free', 0), ('credits999', 0), (123, 0),
])
def test_credits_for_plan(name, expected):
    assert credits_for_plan(name) == expected


# ---------------------------------------------------------------------------
# plan_for_order
# ---------------------------------------------------------------------------

def _attrs(variant_id=None):
    return {'first_order_item': {'variant_id': variant_id}} if variant_id is not None else {}


def test_plan_for_order_variant_from_map_even_if_amount_differs():
    vmap = {'123456': 'credits30'}
    assert plan_for_order(_attrs(123456), 5.00, vmap) == 'credits30'     # int в payload, str в карте
    assert plan_for_order(_attrs('123456'), 5.00, vmap) == 'credits30'


def test_plan_for_order_variant_has_priority_over_amount():
    assert plan_for_order(_attrs(111), 24.99, {'111': 'credits5'}) == 'credits5'


def test_plan_for_order_variant_outside_map_falls_back_to_exact_amount():
    vmap = {'123456': 'credits30'}
    assert plan_for_order(_attrs(999), 9.99, vmap) == 'credits5'
    assert plan_for_order(_attrs(999), 24.99, vmap) == 'credits30'


def test_plan_for_order_no_variant_in_payload_uses_amount():
    assert plan_for_order({}, 9.99, {}) == 'credits5'
    assert plan_for_order({}, 24.99, {}) == 'credits30'
    assert plan_for_order({}, 24.990000001, {}) == 'credits30'            # round(, 2)


def test_plan_for_order_unknown_everything_is_none():
    assert plan_for_order({}, 5.00, {}) is None
    assert plan_for_order(_attrs(999), 5.00, {'1': 'credits30'}) is None
    assert plan_for_order({}, 24.98, {}) is None
    assert plan_for_order({}, 0, {}) is None


def test_plan_for_order_garbage_input_does_not_raise():
    assert plan_for_order(None, 9.99, None) == 'credits5'
    assert plan_for_order('str', 24.99, 'not-a-dict') == 'credits30'
    assert plan_for_order({'first_order_item': None}, 5, {}) is None
    assert plan_for_order({'first_order_item': 'x'}, 5, {}) is None
    assert plan_for_order({}, None, {}) is None
    assert plan_for_order({}, 'abc', {}) is None


def test_plan_for_order_ignores_map_value_with_unknown_plan():
    assert plan_for_order(_attrs(1), 9.99, {'1': 'bogus'}) == 'credits5'  # вариант отброшен, сработала сумма
    assert plan_for_order(_attrs(1), 5.00, {'1': 'bogus'}) is None


# ---------------------------------------------------------------------------
# parse_variant_map
# ---------------------------------------------------------------------------

def test_parse_variant_map_valid():
    raw = '{"123456":"credits30","111111":"credits5"}'
    assert parse_variant_map(raw) == {'123456': 'credits30', '111111': 'credits5'}
    assert parse_variant_map(raw.encode('utf-8')) == {'123456': 'credits30', '111111': 'credits5'}


@pytest.mark.parametrize('raw', ['', '   ', None, 'garbage', '{bad json', '[1, 2]', '"text"', '123', 42, ['x']])
def test_parse_variant_map_bad_input_gives_empty(raw):
    assert parse_variant_map(raw) == {}


def test_parse_variant_map_drops_bad_items_only():
    raw = '{"1":"nope","2":"credits30","":"credits5","3":5,"4":null," 5 ":"credits5"}'
    assert parse_variant_map(raw) == {'2': 'credits30', '5': 'credits5'}


# ---------------------------------------------------------------------------
# Пакеты в constants и /subscription/plans
# ---------------------------------------------------------------------------

def test_constants_second_plan_fields():
    from app.utils.constants import PLAN_CREDITS, PLAN_CREDITS_30, SUBSCRIPTION_PLANS
    assert PLAN_CREDITS == 'credits5' and PLAN_CREDITS_30 == 'credits30'
    first, second = SUBSCRIPTION_PLANS[PLAN_CREDITS], SUBSCRIPTION_PLANS[PLAN_CREDITS_30]
    assert (first['credits_per_purchase'], first['price_usd']) == (10, 9.99)
    assert (second['credits_per_purchase'], second['price_usd']) == (30, 24.99)
    assert second['billing_type'] == 'one_time'
    assert set(first) == set(second)                                       # те же поля
    assert any('30 credits' in f for f in second['features'])


@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app


def test_subscription_plans_endpoint_lists_both_packs(app_ctx):
    resp = app_ctx.test_client().get('/subscription/plans')
    assert resp.status_code == 200
    plans = {p['plan_name']: p for p in resp.get_json()['data']['plans']}
    assert {'free', 'credits5', 'credits30'} <= set(plans)
    assert plans['credits30']['price_usd'] == 24.99


# ---------------------------------------------------------------------------
# Вебхук и регистрация
# ---------------------------------------------------------------------------

def _email(tag):
    _COUNTER['n'] += 1
    return 'second-plan-%s-%03d@example.com' % (tag, _COUNTER['n'])


def _post(app, email, total, variant_id=None, order_id=None):
    """Подписанный оплаченный order_created. Возвращает (response, order_id)."""
    app.config['LEMONSQUEEZY_WEBHOOK_SECRET'] = _WH_SECRET
    _COUNTER['n'] += 1
    order_id = order_id or 'order-second-plan-%03d' % _COUNTER['n']
    attributes = {'identifier': order_id, 'user_email': email,
                  'status': 'paid', 'total': total, 'currency': 'usd'}
    if variant_id is not None:
        attributes['first_order_item'] = {'variant_id': variant_id}
    raw = json_lib.dumps({'data': {'attributes': attributes}}).encode('utf-8')
    sig = hmac.new(_WH_SECRET.encode('utf-8'), raw, hashlib.sha256).hexdigest()
    resp = app.test_client().post('/webhooks/lemonsqueezy', data=raw, content_type='application/json',
                                  headers={'X-Signature': sig, 'X-Event-Name': 'order_created'})
    return resp, order_id


def _register(email):
    from app.services.auth_service import AuthService
    result = AuthService.register(email, 'somepassword123')
    assert result['success'] is True
    return result['user']


def _payment(order_id):
    from app.models.payment import Payment
    return Payment.query.filter_by(external_id=order_id).first()


def test_webhook_credits30_by_variant_map_grants_30(app_ctx):
    app_ctx.config['LEMONSQUEEZY_VARIANT_PLANS'] = '{"777":"credits30","111":"credits5"}'
    email = _email('variant')
    user = _register(email)
    resp, order_id = _post(app_ctx, email, total=2499, variant_id=777)
    assert resp.status_code == 200
    sub = user.get_active_subscription()
    assert sub.credits_granted == 2 + 30
    assert sub.improvement_credits == 15
    pay = _payment(order_id)
    assert pay.plan_name == 'credits30' and pay.status == 'completed' and pay.amount == 24.99


def test_webhook_variant_map_wins_over_amount(app_ctx):
    app_ctx.config['LEMONSQUEEZY_VARIANT_PLANS'] = '{"777":"credits30"}'
    email = _email('variant-amount')
    user = _register(email)
    _post(app_ctx, email, total=500, variant_id=777)                       # сумма не совпадает (скидка/валюта)
    assert user.get_active_subscription().credits_granted == 2 + 30


def test_webhook_credits30_by_exact_amount_without_map(app_ctx):
    app_ctx.config['LEMONSQUEEZY_VARIANT_PLANS'] = ''
    email = _email('amount30')
    user = _register(email)
    resp, order_id = _post(app_ctx, email, total=2499)
    assert resp.status_code == 200
    assert user.get_active_subscription().credits_granted == 2 + 30
    assert _payment(order_id).plan_name == 'credits30'


def test_webhook_999_without_map_grants_10_backward_compatible(app_ctx):
    app_ctx.config['LEMONSQUEEZY_VARIANT_PLANS'] = ''
    email = _email('compat')
    user = _register(email)
    resp, order_id = _post(app_ctx, email, total=999)
    assert resp.status_code == 200
    sub = user.get_active_subscription()
    assert sub.credits_granted == 2 + 10
    assert sub.improvement_credits == 5
    assert _payment(order_id).plan_name == 'credits5'


def test_webhook_unknown_plan_records_payment_without_credits_and_without_email_in_logs(app_ctx, caplog):
    app_ctx.config['LEMONSQUEEZY_VARIANT_PLANS'] = '{"777":"credits30"}'
    email = _email('unknown')
    user = _register(email)
    caplog.set_level(logging.DEBUG)
    caplog.clear()                                                          # без логов регистрации
    resp, order_id = _post(app_ctx, email, total=500, variant_id=999)
    assert resp.status_code == 200
    sub = user.get_active_subscription()
    assert sub.credits_granted == 2 and sub.improvement_credits == 0
    pay = _payment(order_id)
    assert pay is not None and pay.status == 'completed' and pay.plan_name == 'unknown'
    assert 'credits NOT applied' in pay.description and 'manual review' in pay.description
    assert email not in caplog.text
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(order_id in m and '5.0' in m for m in errors), errors


def test_unclaimed_credits30_then_registration_grants_30(app_ctx):
    app_ctx.config['LEMONSQUEEZY_VARIANT_PLANS'] = ''
    email = _email('unclaimed30')
    resp, order_id = _post(app_ctx, email, total=2499)                     # пользователя ещё нет
    assert resp.status_code == 200
    pay = _payment(order_id)
    assert pay.status == 'completed_unclaimed' and pay.user_id is None and pay.plan_name == 'credits30'
    user = _register(email)
    sub = user.get_active_subscription()
    assert sub.credits_granted == 2 + 30
    assert sub.improvement_credits == 15
    pay = _payment(order_id)
    assert pay.user_id == user.id and pay.status == 'completed'


def test_unclaimed_legacy_payment_without_plan_name_gives_10(app_ctx):
    from app import db
    from app.models.payment import Payment
    email = _email('legacy')
    _COUNTER['n'] += 1
    db.session.add(Payment(user_id=None, payer_email=email, amount=9.99, currency='usd',
                           status='completed_unclaimed', provider='lemonsqueezy', plan_name=None,
                           external_id='legacy-second-plan-%03d' % _COUNTER['n'],
                           description='Lemon Squeezy order (unmatched user)'))
    db.session.commit()
    sub = _register(email).get_active_subscription()
    assert sub.credits_granted == 2 + 10
    assert sub.improvement_credits == 5


def test_unclaimed_unknown_plan_is_bound_but_grants_nothing(app_ctx):
    app_ctx.config['LEMONSQUEEZY_VARIANT_PLANS'] = ''
    email = _email('unclaimed-unknown')
    resp, order_id = _post(app_ctx, email, total=500)
    assert resp.status_code == 200
    assert _payment(order_id).plan_name == 'unknown'
    user = _register(email)
    assert user.get_active_subscription().credits_granted == 2
    pay = _payment(order_id)
    assert pay.user_id == user.id and 'claimed at registration' in pay.description


def test_webhook_replay_does_not_double_credits(app_ctx):
    app_ctx.config['LEMONSQUEEZY_VARIANT_PLANS'] = ''
    email = _email('replay')
    user = _register(email)
    r1, order_id = _post(app_ctx, email, total=2499)
    r2, _ = _post(app_ctx, email, total=2499, order_id=order_id)
    assert r1.status_code == 200 and r2.status_code == 200
    assert user.get_active_subscription().credits_granted == 2 + 30
