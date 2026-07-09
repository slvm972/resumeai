# app/utils/constants.py

# Планы подписки
PLAN_FREE = 'free'
PLAN_PRO = 'pro'
PLAN_ENTERPRISE = 'enterprise'

SUBSCRIPTION_PLANS = {
    PLAN_FREE: {
        'display_name': 'Free',
        'price_usd': 0,
        'analysis_quota': 2,
        'improvement_quota': 0,
        'custom_api_key': False,
        'features': ['2 resume analyses per month', 'Basic feedback', 'Server API key'],
    },
    PLAN_PRO: {
        'display_name': 'Pro',
        'price_usd': 19.99,
        'analysis_quota': -1,  # безлимит
        'improvement_quota': 50,
        'custom_api_key': False,
        'features': ['Unlimited analyses', '50 improvements/month', 'Priority support'],
    },
    PLAN_ENTERPRISE: {
        'display_name': 'Enterprise',
        'price_usd': 9.99,
        'analysis_quota': -1,
        'improvement_quota': -1,
        'custom_api_key': True,
        'features': ['Unlimited everything', 'Custom API key', 'Dedicated support'],
    },
}

# Провайдеры API ключей
API_KEY_PROVIDERS = ['openrouter', 'anthropic']

# Роли пользователей
ROLE_USER = 'user'
ROLE_ADMIN = 'admin'
