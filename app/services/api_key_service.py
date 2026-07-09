# app/services/api_key_service.py
from app import db
from app.models.api_key import APIKey, APIKeyUsageLog
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class APIKeyService:

    @staticmethod
    def create_key(user, provider, name, key, expires_at=None):
        """Создать новый API ключ."""
        key_hash = APIKey.hash_key(key)

        if APIKey.query.filter_by(key_hash=key_hash).first():
            return {'success': False, 'error': 'This key already exists'}

        try:
            api_key = APIKey(
                user_id=user.id,
                provider=provider,
                name=name,
                key_hash=key_hash,
                key_prefix=key[:12] + '...',
                expires_at=expires_at,
            )

            # Если первый ключ — сделать основным
            existing = APIKey.query.filter_by(user_id=user.id, provider=provider).first()
            if not existing:
                api_key.is_primary = True

            db.session.add(api_key)
            db.session.commit()
            return {'success': True, 'key': api_key}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating API key: {str(e)}")
            return {'success': False, 'error': 'Failed to create key'}

    @staticmethod
    def get_user_keys(user, provider=None, active_only=True):
        """Получить список ключей пользователя."""
        query = APIKey.query.filter_by(user_id=user.id)
        if provider:
            query = query.filter_by(provider=provider)
        if active_only:
            query = query.filter_by(is_active=True)
        return query.order_by(APIKey.created_at.desc()).all()

    @staticmethod
    def get_primary_key(user, provider='openrouter'):
        """Получить основной ключ пользователя."""
        return APIKey.query.filter_by(
            user_id=user.id,
            provider=provider,
            is_primary=True,
            is_active=True,
        ).first()

    @staticmethod
    def set_primary_key(key_id):
        """Установить ключ как основной."""
        try:
            api_key = APIKey.query.get(key_id)
            if not api_key:
                return {'success': False, 'error': 'Key not found'}

            # Снять primary со всех остальных ключей этого провайдера
            APIKey.query.filter_by(
                user_id=api_key.user_id,
                provider=api_key.provider
            ).update({'is_primary': False})

            api_key.is_primary = True
            db.session.commit()
            return {'success': True, 'key': api_key}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error setting primary key: {str(e)}")
            return {'success': False, 'error': 'Failed to set primary key'}

    @staticmethod
    def delete_key(key_id):
        """Удалить ключ."""
        try:
            api_key = APIKey.query.get(key_id)
            if not api_key:
                return {'success': False, 'error': 'Key not found'}
            db.session.delete(api_key)
            db.session.commit()
            return {'success': True}
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    @staticmethod
    def log_usage(api_key_id, request_id, model, tokens, status, error_msg=None, duration_ms=None):
        """Записать использование ключа."""
        try:
            log = APIKeyUsageLog(
                api_key_id=api_key_id,
                request_id=request_id,
                model_used=model,
                tokens_used=tokens,
                status=status,
                error_message=error_msg,
                duration_ms=duration_ms,
            )
            db.session.add(log)

            api_key = APIKey.query.get(api_key_id)
            if api_key:
                api_key.mark_used(tokens)
            else:
                db.session.commit()

        except Exception as e:
            logger.error(f"Error logging API key usage: {str(e)}")

    @staticmethod
    def get_usage_stats(key_id, days=30):
        """Получить статистику использования ключа."""
        from datetime import timedelta
        from sqlalchemy import func

        since = datetime.utcnow() - timedelta(days=days)
        logs = APIKeyUsageLog.query.filter(
            APIKeyUsageLog.api_key_id == key_id,
            APIKeyUsageLog.created_at >= since
        ).all()

        total = len(logs)
        successful = sum(1 for l in logs if l.status == 'success')
        total_tokens = sum(l.tokens_used for l in logs)

        return {
            'total_requests': total,
            'successful': successful,
            'failed': total - successful,
            'success_rate': round((successful / total * 100), 2) if total > 0 else 0,
            'total_tokens': total_tokens,
            'avg_tokens_per_request': round(total_tokens / total, 1) if total > 0 else 0,
        }
