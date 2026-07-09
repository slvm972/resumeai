# app/routes/user.py
from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity
from app.utils.response import success_response, error_response
from app.utils.decorators import require_active_user, json_required
from app.models.user import User
from app.models.subscription import Subscription
from app import db

user_bp = Blueprint('user', __name__)

@user_bp.route('/profile', methods=['GET'])
@require_active_user
def get_profile():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    subscription = user.get_active_subscription()
    return success_response(data={
        'user': user.to_dict(),
        'subscription': subscription.to_dict() if subscription else None,
    })

@user_bp.route('/profile', methods=['PUT'])
@require_active_user
@json_required
def update_profile():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    data = request.get_json()

    if 'password' in data and data['password']:
        from app.utils.validators import validate_password
        valid, msg = validate_password(data['password'])
        if not valid:
            return error_response('weak_password', msg, 400)
        user.set_password(data['password'])

    db.session.commit()
    return success_response(data={'user': user.to_dict()}, message='Profile updated')

@user_bp.route('/usage', methods=['GET'])
@require_active_user
def get_usage():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    subscription = user.get_active_subscription()

    return success_response(data={
        'plan': user.get_plan_name(),
        'analysis_used': subscription.analysis_used if subscription else 0,
        'improvement_used': subscription.improvement_used if subscription else 0,
        'analysis_quota': subscription.analysis_quota if subscription else 2,
    })
