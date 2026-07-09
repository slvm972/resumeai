# app/routes/analysis.py
from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity
from app.utils.response import success_response, error_response
from app.utils.decorators import require_active_user, json_required
from app.models.user import User
from app.models.subscription import Subscription
from app.models.usage_log import UsageLog
from app.services.openrouter_service import OpenRouterService
from app import db

analysis_bp = Blueprint('analysis', __name__)

@analysis_bp.route('/analyze', methods=['POST'])
@require_active_user
@json_required
def analyze_resume():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    data = request.get_json()

    resume_text = data.get('resume_text', '').strip()
    if not resume_text:
        return error_response('missing_field', 'resume_text is required', 400)

    if len(resume_text) < 50:
        return error_response('too_short', 'Resume text is too short', 400)

    # Проверить квоту
    subscription = user.get_active_subscription()
    if subscription and subscription.plan_name == 'free':
        if subscription.analysis_used >= 2:
            return error_response('quota_exceeded', 'Monthly analysis limit reached. Upgrade to Pro.', 403)

    # Выполнить анализ
    result = OpenRouterService.analyze_resume(user, resume_text)
    if not result['success']:
        return error_response('analysis_failed', result.get('error', 'Analysis failed'), 500)

    # Обновить использование
    if subscription:
        subscription.analysis_used += 1
        db.session.commit()

    # Записать в лог
    UsageLog.log(
        user_id=user_id,
        action='analysis',
        tokens=result.get('tokens_used', 0),
        status='success',
        ip=request.remote_addr,
    )

    return success_response(data={
        'analysis': result['analysis'],
        'tokens_used': result.get('tokens_used', 0),
    })

@analysis_bp.route('/improve', methods=['POST'])
@require_active_user
@json_required
def improve_resume():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    data = request.get_json()

    resume_text = data.get('resume_text', '').strip()
    improvement_type = data.get('improvement_type', 'both')

    if not resume_text:
        return error_response('missing_field', 'resume_text is required', 400)

    # Проверить квоту улучшений
    subscription = user.get_active_subscription()
    if subscription and subscription.plan_name == 'free':
        return error_response('quota_exceeded', 'Improvements require Pro or Enterprise plan.', 403)

    result = OpenRouterService.improve_resume(user, resume_text, improvement_type)
    if not result['success']:
        return error_response('improvement_failed', result.get('error', 'Improvement failed'), 500)

    if subscription:
        subscription.improvement_used += 1
        db.session.commit()

    UsageLog.log(
        user_id=user_id,
        action='improvement',
        tokens=result.get('tokens_used', 0),
        status='success',
        ip=request.remote_addr,
    )

    return success_response(data={
        'suggestion': result['suggestion'],
        'improvement_type': improvement_type,
        'tokens_used': result.get('tokens_used', 0),
    })
