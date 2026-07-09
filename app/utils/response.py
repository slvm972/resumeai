# ============================================================================
# app/utils/response.py - Стандартизированные ответы API
# ============================================================================

from flask import jsonify


def success_response(data=None, message=None, status_code=200):
    """Успешный ответ API."""
    response = {'success': True}

    if data is not None:
        response['data'] = data
    if message:
        response['message'] = message

    return jsonify(response), status_code


def error_response(error_code, message, status_code=400):
    """Ответ с ошибкой API."""
    response = {
        'success': False,
        'error': message,
        'error_code': error_code,
    }
    return jsonify(response), status_code
