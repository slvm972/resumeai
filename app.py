"""
ResumeAI - Single File Deployment (Gemini Version)
"""

from flask import Flask, request, jsonify, send_from_directory
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

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    print("WARNING: GEMINI_API_KEY not set")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def init_db():
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analyses
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  file_hash TEXT,
                  analysis_result TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(file_path):
    text = ""
    try:
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() or ''
    except Exception as e:
        print(f"PDF error: {e}")
    return text

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        print(f"DOCX error: {e}")
        return ""

def get_file_hash(file_path):
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        hasher.update(f.read())
    return hasher.hexdigest()

def check_cached_analysis(file_hash):
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute("SELECT analysis_result FROM analyses WHERE file_hash = ? ORDER BY created_at DESC LIMIT 1", (file_hash,))
    result = c.fetchone()
    conn.close()
    return json.loads(result[0]) if result else None

def save_analysis(file_hash, analysis_result):
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute("INSERT INTO analyses (file_hash, analysis_result) VALUES (?, ?)",
              (file_hash, json.dumps(analysis_result)))
    conn.commit()
    conn.close()

def analyze_with_gemini(resume_text):
    if not GEMINI_KEY:
        return generate_demo_analysis(resume_text)

    prompt = f"""You are an expert resume reviewer. Analyze this resume and respond ONLY with valid JSON, no extra text:

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
        response = model.generate_content(prompt)
        clean = response.text.replace('```json', '').replace('```', '').strip()
        start = clean.find('{')
        end = clean.rfind('}') + 1
        return json.loads(clean[start:end])
    except Exception as e:
        print(f"Gemini error: {e}")
        return generate_demo_analysis(resume_text)

def generate_demo_analysis(resume_text):
    word_count = len(resume_text.split())
    score = min(65 + (10 if word_count > 300 else 0) + (5 if '@' in resume_text else 0), 85)
    return {
        "overall_score": score,
        "summary": f"Resume contains {word_count} words. Shows potential with room for improvement in quantifiable achievements and keywords.",
        "strengths": [
            "Contains professional contact information",
            "Appropriate resume length",
            "Uses professional terminology"
        ],
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

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze():
    # поле называется 'file' — как в index.html
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded. Send field named "file".'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Use PDF, DOC or DOCX.'}), 400

    try:
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        file_hash = get_file_hash(file_path)
        cached = check_cached_analysis(file_hash)
        if cached:
            os.remove(file_path)
            return jsonify(cached)

        ext = filename.rsplit('.', 1)[1].lower()
        if ext == 'pdf':
            text = extract_text_from_pdf(file_path)
        elif ext in ('doc', 'docx'):
            text = extract_text_from_docx(file_path)
        else:
            os.remove(file_path)
            return jsonify({'error': 'Unsupported format'}), 400

        os.remove(file_path)

        if not text or len(text.strip()) < 50:
            return jsonify({'error': 'Could not extract text. Make sure the PDF is not a scanned image.'}), 422

        result = analyze_with_gemini(text)
        save_analysis(file_hash, result)

        return jsonify(result)

    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'error': 'Processing error. Please try again.'}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'engine': 'gemini', 'timestamp': datetime.now().isoformat()})

@app.route('/api/stats', methods=['GET'])
def stats():
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM analyses")
    total = c.fetchone()[0]
    conn.close()
    return jsonify({'total_analyses': total})

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"ResumeAI (Gemini) starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
