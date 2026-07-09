# app/routes/auth.py
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.utils.response import success_response, error_response
from app.utils.decorators import json_required
from app.utils.validators import validate_email, validate_password, validate_required_fields
from app.services.auth_service import AuthService

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['POST'])
@json_required
def register():
    data = request.get_json()
    valid, msg = validate_required_fields(data, ['email', 'password'])
    if not valid:
        return error_response('missing_fields', msg, 400)

    if not validate_email(data['email']):
        return error_response('invalid_email', 'Invalid email format', 400)

    valid, msg = validate_password(data['password'])
    if not valid:
        return error_response('weak_password', msg, 400)

    result = AuthService.register(data['email'], data['password'])
    if not result['success']:
        return error_response('registration_failed', result['error'], 400)

    return success_response(
        data={'user': result['user'].to_dict()},
        message='Registration successful',
        status_code=201
    )

@auth_bp.route('/login', methods=['POST'])
@json_required
def login():
    data = request.get_json()
    valid, msg = validate_required_fields(data, ['email', 'password'])
    if not valid:
        return error_response('missing_fields', msg, 400)

    result = AuthService.login(data['email'], data['password'])
    if not result['success']:
        return error_response('login_failed', result['error'], 401)

    return success_response(data={
        'user': result['user'].to_dict(),
        'access_token': result['access_token'],
        'refresh_token': result['refresh_token'],
    })

@auth_bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    user_id = int(get_jwt_identity())  # ← конвертируем в int
    result = AuthService.refresh_token(user_id)
    if not result['success']:
        return error_response('refresh_failed', result['error'], 401)
    return success_response(data={'access_token': result['access_token']})

@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def me():
    from app.models.user import User
    user_id = int(get_jwt_identity())  # ← конвертируем в int
    user = User.query.get(user_id)
    if not user:
        return error_response('not_found', 'User not found', 404)
    return success_response(data={'user': user.to_dict()})
