# app/services/jwt_service.py
from flask_jwt_extended import create_access_token, create_refresh_token
from datetime import timedelta

class JWTService:

    @staticmethod
    def create_access_token(user_id, expires_delta=None):
        """Создать access token. Identity должна быть строкой."""
        return create_access_token(
            identity=str(user_id),  # ← строка!
            expires_delta=expires_delta or timedelta(days=1)
        )

    @staticmethod
    def create_refresh_token(user_id):
        """Создать refresh token."""
        return create_refresh_token(
            identity=str(user_id),  # ← строка!
            expires_delta=timedelta(days=30)
        )
