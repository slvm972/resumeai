# app/routes/api_keys.py
from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity
from app.utils.response import success_response, error_response
from app.utils.decorators import require_active_user, json_required
from app.models.user import User
from app.models.api_key import APIKey
from app.services.api_key_service import APIKeyService
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
api_keys_bp = Blueprint('api_keys', __name__)

@api_keys_bp.route('', methods=['POST'])
@require_active_user
@json_required
def create_api_key():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    data = request.get_json()

    provider = data.get('provider', '').strip()
    name = data.get('name', '').strip()
    key = data.get('key', '').strip()

    if not provider or not name or not key:
        return error_response('missing_fields', 'provider, name and key are required', 400)
    if len(key) < 10:
        return error_response('invalid_key', 'API key is too short', 400)

    expires_at = None
    if data.get('expires_at'):
        try:
            expires_at = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00'))
        except:
            return error_response('invalid_date', 'Invalid expires_at format', 400)

    result = APIKeyService.create_key(user, provider, name, key, expires_at)
    if not result['success']:
        return error_response('create_failed', result['error'], 400)

    return success_response(data=result['key'].to_dict(), message='API key created', status_code=201)

@api_keys_bp.route('', methods=['GET'])
@require_active_user
def list_api_keys():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    provider = request.args.get('provider')
    active_only = request.args.get('active_only', 'true').lower() == 'true'
    keys = APIKeyService.get_user_keys(user, provider, active_only)
    return success_response(data={'keys': [k.to_dict() for k in keys]})

@api_keys_bp.route('/<int:key_id>', methods=['GET'])
@require_active_user
def get_api_key(key_id):
    user_id = get_jwt_identity()
    api_key = APIKey.query.filter_by(id=key_id, user_id=user_id).first()
    if not api_key:
        return error_response('not_found', 'API key not found', 404)
    return success_response(data=api_key.to_dict())

@api_keys_bp.route('/<int:key_id>', methods=['DELETE'])
@require_active_user
def delete_api_key(key_id):
    user_id = get_jwt_identity()
    api_key = APIKey.query.filter_by(id=key_id, user_id=user_id).first()
    if not api_key:
        return error_response('not_found', 'API key not found', 404)
    result = APIKeyService.delete_key(key_id)
    if not result['success']:
        return error_response('delete_failed', 'Failed to delete key', 500)
    return success_response(message='API key deleted')

@api_keys_bp.route('/<int:key_id>/set-primary', methods=['POST'])
@require_active_user
def set_primary(key_id):
    user_id = get_jwt_identity()
    api_key = APIKey.query.filter_by(id=key_id, user_id=user_id).first()
    if not api_key:
        return error_response('not_found', 'API key not found', 404)
    result = APIKeyService.set_primary_key(key_id)
    if not result['success']:
        return error_response('update_failed', 'Failed to set primary key', 500)
    return success_response(data=result['key'].to_dict(), message='Primary key updated')

@api_keys_bp.route('/<int:key_id>/stats', methods=['GET'])
@require_active_user
def get_stats(key_id):
    user_id = get_jwt_identity()
    api_key = APIKey.query.filter_by(id=key_id, user_id=user_id).first()
    if not api_key:
        return error_response('not_found', 'API key not found', 404)
    days = request.args.get('days', 30, type=int)
    stats = APIKeyService.get_usage_stats(key_id, days)
    return success_response(data=stats)
