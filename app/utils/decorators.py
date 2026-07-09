# app/utils/decorators.py
from functools import wraps
from flask import request, jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
import logging

logger = logging.getLogger(__name__)

def require_active_user(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
        except Exception as e:
            logger.error(f"JWT verify error: {type(e).__name__}: {str(e)}")
            return jsonify({'success': False, 'error': 'Authentication required', 'error_code': 'unauthorized'}), 401

        try:
            from app.models.user import User
            user_id = int(get_jwt_identity())  # ← конвертируем строку в int
            user = User.query.get(user_id)
            if not user:
                return jsonify({'success': False, 'error': 'User not found', 'error_code': 'user_not_found'}), 404
            if not user.is_active:
                return jsonify({'success': False, 'error': 'Account is disabled', 'error_code': 'account_disabled'}), 403
        except Exception as e:
            logger.error(f"User lookup error: {type(e).__name__}: {str(e)}")
            return jsonify({'success': False, 'error': 'Server error', 'error_code': 'server_error'}), 500

        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
            from app.models.user import User
            user_id = int(get_jwt_identity())
            user = User.query.get(user_id)
            if not user or user.role != 'admin':
                return jsonify({'success': False, 'error': 'Admin access required', 'error_code': 'forbidden'}), 403
        except Exception as e:
            return jsonify({'success': False, 'error': 'Authentication required', 'error_code': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

def json_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not request.is_json:
            return jsonify({'success': False, 'error': 'Content-Type must be application/json', 'error_code': 'invalid_content_type'}), 400
        return f(*args, **kwargs)
    return decorated
