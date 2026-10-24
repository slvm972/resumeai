# app/utils/plans.py
"""
Определение купленного пакета кредитов (чистые функции: без Flask и без БД).

Используется вебхуком Lemon Squeezy (app/routes/webhooks.py) и регистрацией
(app/services/auth_service.py), чтобы число кредитов определялось пакетом,
а не было зашито в коде.
"""
import json

from app.utils.constants import SUBSCRIPTION_PLANS, PLAN_CREDITS


def _purchasable():
    """Разовые пакеты кредитов: имя -> описание (из SUBSCRIPTION_PLANS)."""
    return {
        name: info
        for name, info in SUBSCRIPTION_PLANS.items()
        if info.get('billing_type') == 'one_time' and info.get('credits_per_purchase')
    }


def credits_for_plan(plan_name):
    """
    Сколько кредитов даёт пакет.
    None, пустая строка и исторический 'credits5' -> 10 (старые платежи без plan_name).
    'credits30' -> 30. Неизвестное имя -> 0.
    """
    if plan_name is None:
        plan_name = PLAN_CREDITS
    if not isinstance(plan_name, str):
        return 0
    plan_name = plan_name.strip() or PLAN_CREDITS
    info = _purchasable().get(plan_name)
    return int(info['credits_per_purchase']) if info else 0


def plan_for_order(attributes, amount, variant_map):
    """
    Определить пакет по заказу Lemon Squeezy. Порядок:
    1) variant_id заказа (data.attributes.first_order_item.variant_id, сравнение
       как строки) по карте variant_map (строка -> имя пакета);
    2) если варианта нет в карте или в payload: точное совпадение amount
       с price_usd одного из пакетов (round(, 2));
    3) иначе None.
    """
    purchasable = _purchasable()

    item = attributes.get('first_order_item') if isinstance(attributes, dict) else None
    variant_id = item.get('variant_id') if isinstance(item, dict) else None
    if variant_id is not None and isinstance(variant_map, dict):
        plan = variant_map.get(str(variant_id).strip())
        if plan in purchasable:
            return plan

    try:
        amount_r = round(float(amount), 2)
    except (TypeError, ValueError):
        return None
    for name, info in purchasable.items():
        if amount_r == round(float(info['price_usd']), 2):
            return name
    return None


def parse_variant_map(raw):
    """
    Разобрать JSON-строку {"123456":"credits30","111111":"credits5"} в dict.
    Пустая строка, невалидный JSON, не словарь -> {}. Плохие элементы
    (не строка, неизвестный пакет, пустой ключ) отбрасываются. Исключений наружу нет.
    """
    if not isinstance(raw, (str, bytes)) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    purchasable = _purchasable()
    result = {}
    for key, value in data.items():
        key = str(key).strip()
        if key and isinstance(value, str) and value in purchasable:
            result[key] = value
    return result
