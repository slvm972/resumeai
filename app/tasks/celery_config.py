# app/tasks/celery_config.py
from celery import Celery
from celery.schedules import crontab

def make_celery(app):
    """Создать Celery с Flask контекстом."""
    celery = Celery(
        app.import_name,
        backend=app.config.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2'),
        broker=app.config.get('CELERY_BROKER_URL', 'redis://localhost:6379/1'),
    )

    celery.conf.update(app.config)

    # Расписание фоновых задач
    celery.conf.beat_schedule = {
        'cleanup-expired-api-keys': {
            'task': 'app.tasks.api_key_tasks.cleanup_expired_api_keys',
            'schedule': crontab(hour=3, minute=0),
        },
        'update-api-key-stats': {
            'task': 'app.tasks.api_key_tasks.update_api_key_stats',
            'schedule': crontab(hour=4, minute=0),
        },
    }

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery
