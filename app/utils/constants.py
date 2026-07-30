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
        'features': ['2 free credits — use for analyses and improvements', 'Basic feedback', 'Server API key'],
    },
    PLAN_CREDITS: {
        'display_name': 'Improve Pack',
        'price_usd': 9.99,
        'billing_type': 'one_time',       # маркер: не подписка, без автопродления
        'credits_per_purchase': 10,       # сколько кредитов единого пула добавляет одна покупка
        'analysis_quota': -1,             # безлимит
        'custom_api_key': False,
        'features': [
            '10 credits per purchase — use for analyses and improvements',
            'Credits never expire, stack with each purchase',
            'One-time payment — no subscription, no auto-renewal',
        ],
    },
}

# Провайдеры API ключей
API_KEY_PROVIDERS = ['openrouter', 'anthropic']

# Роли пользователей
ROLE_USER = 'user'
ROLE_ADMIN = 'admin'
