"""
ResumeAI - Single File Deployment (Gemini Version)
"""

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
import google.generativeai as genai
import os
import json
from werkzeug.utils import secure_filename
import PyPDF2
import docx
from datetime import datetime
import sqlite3
import hashlib

# ── Загрузка .env вручную (без python-dotenv) ──────────────────────────────
def load_env_file():
    """Читает config.env рядом со скриптом и устанавливает переменные окружения."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for name in (".env", "config.env"):
        path = os.path.join(script_dir, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        print(f"[.env] Загружен файл: {path}")
        return
    print("[.env] Файл .env / config.env не найден — используются переменные окружения системы")

load_env_file()

# ── API ключ ───────────────────────────────────────────────────────────────
API_KEY = (
    os.environ.get("GOOGLE_API_KEY")
    or os.environ.get("GEMINI_API_KEY")
    or None
)

# Flask app initialization
app = Flask(__name__)
CORS(app)
app.secret_key = 'super secret key'

# Admin credentials (hardcoded for security)
ADMIN_CREDENTIALS = {
    'admin': 'admin123'  # username: password
}

# Session storage for admin (in production, use proper session management)
admin_sessions = {}

# ── Инициализация базы данных ──────────────────────────────────────────────
def init_db():
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resume_analyzer.db'))
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS analyses
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      file_hash TEXT,
                      analysis_result TEXT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      username TEXT,
                      password TEXT)''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ОШИБКА] БД: {e}")

init_db()

# ── Middleware для аутентификации ───────────────────────────────────────────
from functools import wraps
def authenticate_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ── Роут для входа в систему ────────────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    if 'username' not in data or 'password' not in data:
        return jsonify({'error': 'Invalid request'}), 400
    username = data['username']
    password = data['password']
    if username in ADMIN_CREDENTIALS and ADMIN_CREDENTIALS[username] == password:
        session['admin'] = username
        return jsonify({'message': 'Logged in successfully'}), 200
    return jsonify({'error': 'Invalid credentials'}), 401

# ── Роут для выхода из системы ──────────────────────────────────────────────
@app.route('/api/logout', methods=['POST'])
@authenticate_admin
def logout():
    session.pop('admin', None)
    return jsonify({'message': 'Logged out successfully'}), 200

base_dir = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(base_dir, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}

# ── Инициализация Gemini клиента ───────────────────────────────────────────
gemini_client = None
if API_KEY:
    try:
        genai.configure(api_key=API_KEY)
        gemini_client = genai.GenerativeModel('gemini-2.0-flash-lite')
    except Exception as e:
        print(f"[ERROR] Failed to initialize Gemini: {e}")

# ── База данных ────────────────────────────────────────────────────────────
def init_db():
    try:
        conn = sqlite3.connect(os.path.join(base_dir, 'resume_analyzer.db'))
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS analyses
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      file_hash TEXT,
                      analysis_result TEXT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ОШИБКА] БД: {e}")

init_db()

# ── Утилиты ────────────────────────────────────────────────────────────────
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text(file_path, filename):
    ext = filename.rsplit('.', 1)[1].lower()
    text = ""
    try:
        if ext == 'pdf':
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text += page.extract_text() or ''
        elif ext in ('doc', 'docx'):
            doc = docx.Document(file_path)
            text = "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        print(f"[ОШИБКА] Чтение файла: {e}")
    return text

def get_file_hash(file_path):
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        hasher.update(f.read())
    return hasher.hexdigest()

def check_cache(file_hash):
    try:
        conn = sqlite3.connect(os.path.join(base_dir, 'resume_analyzer.db'))
        c = conn.cursor()
        c.execute("SELECT analysis_result FROM analyses WHERE file_hash = ? ORDER BY created_at DESC LIMIT 1", (file_hash,))
        row = c.fetchone()
        conn.close()
        return json.loads(row[0]) if row else None
    except:
        return None

def save_cache(file_hash, result):
    try:
        conn = sqlite3.connect(os.path.join(base_dir, 'resume_analyzer.db'))
        c = conn.cursor()
        c.execute("INSERT INTO analyses (file_hash, analysis_result) VALUES (?, ?)",
                  (file_hash, json.dumps(result)))
        conn.commit()
        conn.close()
    except:
        pass

# ── Анализ ─────────────────────────────────────────────────────────────────
def analyze_with_gemini(resume_text):
    if not gemini_client:
        print("[ДЕМО] Gemini ключ не задан — используется демо-анализ")
        return generate_demo_analysis(resume_text)

    prompt = f"""You are an expert resume reviewer. Analyze this resume and respond ONLY with valid JSON, no extra text, no markdown.

{resume_text[:8000]}

Required JSON format:
{{
    "overall_score": <integer 60-95>,
    "summary": "<2-3 sentence assessment>",
    "strengths": ["strength1", "strength2", "strength3"],
    "improvements": ["improvement1", "improvement2", "improvement3", "improvement4"],
    "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
    "ats_score": <integer 60-95>,
    "formatting_score": <integer 60-95>,
    "content_score": <integer 60-95>
}}"""

    try:
        response = gemini_client.generate_content(prompt)
        clean = response.text.replace('```json', '').replace('```', '').strip()
        start = clean.find('{')
        end = clean.rfind('}') + 1
        return json.loads(clean[start:end])
    except Exception as e:
        import traceback
        print(f"\n{'='*50}")
        print(f"[ERROR] Gemini API: {e}")
        print(f"[FULL STACKTRACE]:")
        traceback.print_exc()
        print(f"{'='*50}\n")
        return generate_demo_analysis(resume_text)

def generate_demo_analysis(resume_text):
    word_count = len(resume_text.split())
    score = min(65 + (10 if word_count > 300 else 0) + (5 if '@' in resume_text else 0), 85)
    return {
        "overall_score": score,
        "summary": f"Демо-режим: ваш ключ GOOGLE_API_KEY не найден или недействителен. Получите новый ключ на aistudio.google.com и добавьте его в config.env. Резюме содержит {word_count} слов.",
        "strengths": ["Contains contact information", "Appropriate length", "Professional language"],
        "improvements": [
            "Add quantifiable achievements (e.g., 'increased sales by 30%')",
            "Include more industry-specific keywords for ATS",
            "Add a professional summary at the top",
            "Use stronger action verbs: led, achieved, drove, built"
        ],
        "keywords": ["leadership", "project management", "data analysis", "team collaboration", "strategic planning"],
        "ats_score": 72,
        "formatting_score": 68,
        "content_score": score
    }

# ── Роуты ──────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(base_dir, 'index.html')

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'engine': 'gemini',
        'api_key_loaded': gemini_client is not None,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/analyze', methods=['POST'])
def analyze():
    file = request.files.get('file') or request.files.get('resume')

    if not file or file.filename == '':
        return jsonify({'error': 'File not selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file format. Supported: PDF, DOC, DOCX'}), 400

    try:
        filename = secure_filename(file.filename)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_path = os.path.join(UPLOAD_FOLDER, f"{ts}_{filename}")
        file.save(file_path)

        file_hash = get_file_hash(file_path)
        cached = check_cache(file_hash)
        if cached:
            os.remove(file_path)
            return jsonify(cached)

        text = extract_text(file_path, filename)
        os.remove(file_path)

        if not text or len(text.strip()) < 50:
            return jsonify({'error': 'Could not extract text. Make sure PDF is not scanned.'}), 422

        result = analyze_with_gemini(text)
        save_cache(file_hash, result)
        return jsonify(result)

    except Exception as e:
        print(f"[ERROR] analyze(): {e}")
        return jsonify({'error': 'Processing error. Try again.'}), 500

@app.route('/api/admin/analyze', methods=['POST'])
@authenticate_admin
def admin_analyze():
    """Admin route for free resume analysis"""
    file = request.files.get('file') or request.files.get('resume')

    if not file or file.filename == '':
        return jsonify({'error': 'File not selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file format. Supported: PDF, DOC, DOCX'}), 400

    try:
        filename = secure_filename(file.filename)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        admin_prefix = "admin_"
        file_path = os.path.join(UPLOAD_FOLDER, f"{admin_prefix}{ts}_{filename}")
        file.save(file_path)

        file_hash = get_file_hash(file_path)
        
        # Check cache first
        cached = check_cache(file_hash)
        if cached:
            os.remove(file_path)
            return jsonify({
                **cached,
                'admin_mode': True,
                'payment_bypassed': True
            })

        text = extract_text(file_path, filename)
        os.remove(file_path)

        if not text or len(text.strip()) < 50:
            return jsonify({'error': 'Could not extract text. Make sure PDF is not scanned.'}), 422

        result = analyze_with_gemini(text)
        save_cache(file_hash, result)
        
        # Add admin metadata
        result['admin_mode'] = True
        result['payment_bypassed'] = True
        result['analysis_type'] = 'admin_free'
        
        return jsonify(result)

    except Exception as e:
        print(f"[ERROR] admin_analyze(): {e}")
        return jsonify({'error': 'Processing error. Try again.'}), 500

@app.route('/api/admin/status', methods=['GET'])
@authenticate_admin
def admin_status():
    """Check admin authentication status"""
    return jsonify({
        'authenticated': True,
        'admin_user': session.get('admin'),
        'features': ['free_analysis', 'unlimited_uploads', 'priority_processing']
    })

@app.route('/api/stats', methods=['GET'])
def stats():
    try:
        conn = sqlite3.connect(os.path.join(base_dir, 'resume_analyzer.db'))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM analyses")
        total = c.fetchone()[0]
        conn.close()
        return jsonify({'total_analyses': total})
    except:
        return jsonify({'total_analyses': 0})

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(base_dir, filename)

# ── Запуск ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("\n" + "="*45)
    if gemini_client:
        print(" OK  СТАТУС: КЛЮЧ ЗАГРУЖЕН — Gemini активен")
    else:
        print(" !!  СТАТУС: КЛЮЧ НЕ НАЙДЕН — демо-режим")
        print("     Добавьте в config.env: GOOGLE_API_KEY=ваш_ключ")
        print("     Получить бесплатный ключ: https://aistudio.google.com/app/apikey")
    print(f"     ССЫЛКА: http://127.0.0.1:5000")
    print("="*45 + "\n")
    app.run(debug=True, host='127.0.0.1', port=5000)
