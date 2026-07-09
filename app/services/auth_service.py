# app/services/auth_service.py
from app import db
from app.models.user import User
from app.models.subscription import Subscription
from app.services.jwt_service import JWTService
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class AuthService:

    @staticmethod
    def register(email, password):
        """Зарегистрировать нового пользователя."""
        if User.query.filter_by(email=email.lower()).first():
            return {'success': False, 'error': 'Email already registered'}

        try:
            user = User(email=email.lower())
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            # Создать бесплатную подписку
            subscription = Subscription(
                user_id=user.id,
                plan_name='free',
                status='active',
            )
            db.session.add(subscription)
            db.session.commit()

            logger.info(f"New user registered: {email}")
            return {'success': True, 'user': user}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Registration error: {str(e)}")
            return {'success': False, 'error': 'Registration failed'}

    @staticmethod
    def login(email, password):
        """Войти в систему."""
        user = User.query.filter_by(email=email.lower()).first()

        if not user or not user.check_password(password):
            return {'success': False, 'error': 'Invalid email or password'}

        if not user.is_active:
            return {'success': False, 'error': 'Account is disabled'}

        user.last_login_at = datetime.utcnow()
        db.session.commit()

        access_token = JWTService.create_access_token(user.id)
        refresh_token = JWTService.create_refresh_token(user.id)

        return {
            'success': True,
            'user': user,
            'access_token': access_token,
            'refresh_token': refresh_token,
        }

    @staticmethod
    def refresh_token(user_id):
        """Обновить access token."""
        user = User.query.get(user_id)
        if not user or not user.is_active:
            return {'success': False, 'error': 'User not found'}

        access_token = JWTService.create_access_token(user.id)
        return {'success': True, 'access_token': access_token}
