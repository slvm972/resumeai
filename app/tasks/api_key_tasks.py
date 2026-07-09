# app/tasks/api_key_tasks.py
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

def get_celery():
    from app import create_app
    from app.tasks.celery_config import make_celery
    app = create_app()
    return make_celery(app), app

celery, app = get_celery()

@celery.task(bind=True, max_retries=3)
def cleanup_expired_api_keys(self):
    """Деактивировать все истекшие API ключи."""
    try:
        from app.models.api_key import APIKey
        from app import db

        now = datetime.utcnow()
        expired = APIKey.query.filter(
            APIKey.expires_at.isnot(None),
            APIKey.expires_at < now,
            APIKey.is_active == True
        ).all()

        count = 0
        for key in expired:
            key.is_active = False
            count += 1

        db.session.commit()
        logger.info(f"Deactivated {count} expired API keys")
        return {'status': 'success', 'deactivated_count': count}

    except Exception as exc:
        logger.error(f"Error in cleanup_expired_api_keys: {str(exc)}")
        raise self.retry(exc=exc, countdown=60)

@celery.task(bind=True, max_retries=3)
def update_api_key_stats(self):
    """Обновить статистику использования API ключей."""
    try:
        from app.models.api_key import APIKey, APIKeyUsageLog
        from app import db

        keys = APIKey.query.filter_by(is_active=True).all()
        since = datetime.utcnow() - timedelta(days=30)
        updated = 0

        for key in keys:
            logs = APIKeyUsageLog.query.filter(
                APIKeyUsageLog.api_key_id == key.id,
                APIKeyUsageLog.created_at >= since,
            ).all()
            key.usage_count = len(logs)
            key.total_tokens_used = sum(l.tokens_used for l in logs)
            updated += 1

        db.session.commit()
        logger.info(f"Updated stats for {updated} API keys")
        return {'status': 'success', 'updated_count': updated}

    except Exception as exc:
        logger.error(f"Error in update_api_key_stats: {str(exc)}")
        raise self.retry(exc=exc, countdown=60)
