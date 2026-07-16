# run.py — точка входа Flask приложения ResumeAI
import os
from dotenv import load_dotenv

# Загрузить .env файл ДО всего остального
load_dotenv()

from app import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'

    # Локальная разработка: alembic upgrade head выполняется автоматически
    # только на проде (см. Procfile), а не при запуске `python run.py`.
    # Поэтому здесь создаём таблицы через create_all() для удобства —
    # только в этом блоке, только для локального запуска. Под gunicorn
    # (`gunicorn run:app`) этот блок не исполняется, на прод не влияет.
    from app import db
    with app.app_context():
        from app import models  # noqa: F401 — регистрирует все модели в metadata
        db.create_all()

    print(f"✅ ResumeAI запускается на http://localhost:{port}")
    print(f"   Debug mode: {debug}")
    print(f"   Database: {os.environ.get('DATABASE_URL', 'не задана')[:30]}...")

    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug
    )
