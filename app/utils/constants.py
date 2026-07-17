# app/utils/constants.py

# Планы подписки
PLAN_FREE = 'free'
PLAN_CREDITS = 'credits5'  # разовая покупка (НЕ подписка) — пакет из 5 Improve-кредитов

SUBSCRIPTION_PLANS = {
    PLAN_FREE: {
        'display_name': 'Free',
        'price_usd': 0,
        'analysis_quota': 2,
        'improvement_quota': 0,
        'custom_api_key': False,
        'features': ['2 resume analyses per month', 'Basic feedback', 'Server API key'],
    },
    PLAN_CREDITS: {
        'display_name': 'Improve Pack',
        'price_usd': 9.99,
        'billing_type': 'one_time',       # маркер: не подписка, без автопродления
        'credits_per_purchase': 5,        # сколько Improve-кредитов добавляет одна покупка
        'analysis_quota': -1,             # безлимит
        'custom_api_key': False,
        'features': [
            '5 resume improvements per purchase',
            'Unlimited resume analyses',
            'One-time payment — no subscription, no auto-renewal',
        ],
    },
}

# Провайдеры API ключей
API_KEY_PROVIDERS = ['openrouter', 'anthropic']

# Роли пользователей
ROLE_USER = 'user'
ROLE_ADMIN = 'admin'
