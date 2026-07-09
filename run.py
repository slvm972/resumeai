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

    print(f"✅ ResumeAI запускается на http://localhost:{port}")
    print(f"   Debug mode: {debug}")
    print(f"   Database: {os.environ.get('DATABASE_URL', 'не задана')[:30]}...")

    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug
    )
