"""
ResumeAI - Single File Deployment
Serves both frontend and API on the same domain (no CORS issues)
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import anthropic
import os
import json
from werkzeug.utils import secure_filename
import PyPDF2
import docx
from datetime import datetime
import sqlite3
import hashlib
import sys

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Create upload folder
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Database initialization
def init_db():
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analyses
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  file_hash TEXT,
                  analysis_result TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  email TEXT UNIQUE,
                  plan TEXT DEFAULT 'free',
                  analyses_count INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# Utility functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(file_path):
    text = ""
    try:
        with open(file_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            for page in pdf_reader.pages:
                text += page.extract_text() or ''
    except Exception as e:
        print(f"Error extracting PDF: {e}")
    return text

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        print(f"Error extracting DOCX: {e}")
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

def analyze_resume_with_claude(resume_text):
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    
    if not api_key or api_key == 'YOUR_API_KEY_HERE':
        return generate_demo_analysis(resume_text)
    
    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        prompt = f"""You are an expert resume reviewer. Analyze this resume:

{resume_text}

Provide JSON response:
{{
    "overall_score": <60-95>,
    "summary": "<2-3 sentence assessment>",
    "strengths": ["s1", "s2", "s3"],
    "improvements": ["i1", "i2", "i3", "i4"],
    "keywords": ["k1", "k2", "k3", "k4", "k5"],
    "ats_score": <60-95>,
    "formatting_score": <60-95>,
    "content_score": <60-95>
}}"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = message.content[0].text
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        return json.loads(response_text[start:end])
            
    except Exception as e:
        print(f"Claude API error: {e}")
        return generate_demo_analysis(resume_text)

def generate_demo_analysis(resume_text):
    """Demo mode - works without API key"""
    word_count = len(resume_text.split())
    
    base_score = 65
    if word_count > 300:
        base_score += 10
    if '@' in resume_text:
        base_score += 5
    if any(char.isdigit() for char in resume_text[:300]):
        base_score += 5
    
    return {
        "overall_score": min(base_score, 85),
        "summary": f"Resume has {word_count} words. Shows potential with room for improvement in quantifiable achievements and keywords.",
        "strengths": [
            "Contains professional contact information",
            "Appropriate length for resume",
            "Uses professional terminology"
        ],
        "improvements": [
            "Add quantifiable achievements (e.g., 'increased sales by 30%')",
            "Include more industry-specific keywords for ATS",
            "Add a professional summary at the top",
            "Use stronger action verbs (led, achieved, drove)"
        ],
        "keywords": ["leadership", "project management", "data analysis", "team collaboration", "strategic planning"],
        "ats_score": 72,
        "formatting_score": 68,
        "content_score": base_score
    }

# Routes
@app.route('/')
def index():
    """Serve the frontend"""
    return send_from_directory('.', 'index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze():
    """Main analysis endpoint"""
    if 'resume' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['resume']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. PDF, DOC, DOCX only'}), 400
    
    try:
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Check cache
        file_hash = get_file_hash(file_path)
        cached = check_cached_analysis(file_hash)
        if cached:
            os.remove(file_path)
            return jsonify(cached)
        
        # Extract text
        if filename.endswith('.pdf'):
            text = extract_text_from_pdf(file_path)
        elif filename.endswith('.docx'):
            text = extract_text_from_docx(file_path)
        else:
            os.remove(file_path)
            return jsonify({'error': 'Unsupported format'}), 400
        
        if not text or len(text.strip()) < 50:
            os.remove(file_path)
            return jsonify({'error': 'Could not extract text. Ensure file is not image-based PDF.'}), 400
        
        # Analyze
        result = analyze_resume_with_claude(text)
        save_analysis(file_hash, result)
        os.remove(file_path)
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'error': 'Processing error. Please try again.'}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/stats', methods=['GET'])
def stats():
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM analyses")
    total = c.fetchone()[0]
    conn.close()
    return jsonify({'total_analyses': total})

# Static files
@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"ResumeAI starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
