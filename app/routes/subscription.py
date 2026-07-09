# app/routes/subscription.py
from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity
from app.utils.response import success_response, error_response
from app.utils.decorators import require_active_user
from app.models.user import User
from app.models.subscription import Subscription
from app.utils.constants import SUBSCRIPTION_PLANS

subscription_bp = Blueprint('subscription', __name__)

@subscription_bp.route('/plans', methods=['GET'])
def get_plans():
    """Получить список доступных планов."""
    plans = [
        {
            'plan_name': name,
            'display_name': info['display_name'],
            'price_usd': info['price_usd'],
            'features': info['features'],
            'analysis_quota': info['analysis_quota'],
            'improvement_quota': info['improvement_quota'],
            'custom_api_key': info['custom_api_key'],
        }
        for name, info in SUBSCRIPTION_PLANS.items()
    ]
    return success_response(data={'plans': plans})

@subscription_bp.route('/current', methods=['GET'])
@require_active_user
def get_current():
    """Получить текущую подписку пользователя."""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    subscription = user.get_active_subscription()

    if not subscription:
        return success_response(data={'subscription': None, 'plan': 'free'})

    plan_info = SUBSCRIPTION_PLANS.get(subscription.plan_name, {})
    return success_response(data={
        'subscription': subscription.to_dict(),
        'plan': subscription.plan_name,
        'analysis_quota': plan_info.get('analysis_quota', 2),
        'improvement_quota': plan_info.get('improvement_quota', 0),
        'analysis_used': subscription.analysis_used,
        'improvement_used': subscription.improvement_used,
    })

@subscription_bp.route('/cancel', methods=['POST'])
@require_active_user
def cancel():
    """Отменить подписку."""
    from app import db
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    subscription = user.get_active_subscription()

    if not subscription or subscription.plan_name == 'free':
        return error_response('no_subscription', 'No active paid subscription', 400)

    try:
        subscription.status = 'cancelled'
        subscription.plan_name = 'free'
        db.session.commit()
        return success_response(message='Subscription cancelled successfully')
    except Exception as e:
        return error_response('cancel_failed', 'Failed to cancel subscription', 500)
