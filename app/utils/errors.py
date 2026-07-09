# app/utils/errors.py
from flask import jsonify

class APIError(Exception):
    def __init__(self, message, status_code=400, error_code=None):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or 'api_error'
        super().__init__(message)

def register_error_handlers(app):
    @app.errorhandler(APIError)
    def handle_api_error(e):
        return jsonify({'success': False, 'error': e.message, 'error_code': e.error_code}), e.status_code

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'success': False, 'error': 'Not found', 'error_code': 'not_found'}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({'success': False, 'error': 'Method not allowed', 'error_code': 'method_not_allowed'}), 405

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({'success': False, 'error': 'Internal server error', 'error_code': 'internal_error'}), 500
