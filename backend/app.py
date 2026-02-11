from flask import Flask, request, jsonify
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

app = Flask(__name__)
CORS(app)

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Initialize database
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

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(file_path):
    """Extract text from PDF file"""
    text = ""
    try:
        with open(file_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            for page in pdf_reader.pages:
                text += page.extract_text()
    except Exception as e:
        print(f"Error extracting PDF: {e}")
    return text

def extract_text_from_docx(file_path):
    """Extract text from DOCX file"""
    try:
        doc = docx.Document(file_path)
        text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
        return text
    except Exception as e:
        print(f"Error extracting DOCX: {e}")
        return ""

def get_file_hash(file_path):
    """Generate hash of file for caching"""
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()

def check_cached_analysis(file_hash):
    """Check if we have a cached analysis for this file"""
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute("SELECT analysis_result FROM analyses WHERE file_hash = ? ORDER BY created_at DESC LIMIT 1", (file_hash,))
    result = c.fetchone()
    conn.close()
    if result:
        return json.loads(result[0])
    return None

def save_analysis(file_hash, analysis_result):
    """Save analysis to database"""
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute("INSERT INTO analyses (file_hash, analysis_result) VALUES (?, ?)",
              (file_hash, json.dumps(analysis_result)))
    conn.commit()
    conn.close()

def analyze_resume_with_claude(resume_text):
    """Analyze resume using Claude AI"""
    
    # Use API key from environment or use a placeholder
    api_key = os.environ.get('ANTHROPIC_API_KEY', 'YOUR_API_KEY_HERE')
    
    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        prompt = f"""You are an expert resume reviewer and career coach. Analyze the following resume and provide detailed, actionable feedback.

Resume text:
{resume_text}

Provide your analysis in the following JSON format:
{{
    "overall_score": <number 0-100>,
    "summary": "<2-3 sentence overall assessment>",
    "strengths": ["strength 1", "strength 2", "strength 3"],
    "improvements": ["improvement 1", "improvement 2", "improvement 3", "improvement 4"],
    "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
    "ats_score": <number 0-100>,
    "formatting_score": <number 0-100>,
    "content_score": <number 0-100>
}}

Focus on:
1. ATS compatibility and keyword optimization
2. Content quality and impact statements
3. Formatting and structure
4. Missing sections or information
5. Industry-specific recommendations"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        # Extract JSON from response
        response_text = message.content[0].text
        
        # Try to parse JSON from response
        try:
            # Find JSON in response (it might have text before/after)
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            json_str = response_text[start:end]
            analysis = json.loads(json_str)
            return analysis
        except:
            # If parsing fails, return a structured error
            return {
                "overall_score": 0,
                "summary": "Error parsing AI response",
                "strengths": [],
                "improvements": ["Please try again"],
                "keywords": [],
                "ats_score": 0,
                "formatting_score": 0,
                "content_score": 0
            }
            
    except Exception as e:
        print(f"Error with Claude API: {e}")
        # Return demo data if API fails
        return generate_demo_analysis(resume_text)

def generate_demo_analysis(resume_text):
    """Generate demo analysis when API is not available"""
    word_count = len(resume_text.split())
    has_email = '@' in resume_text
    has_phone = any(char.isdigit() for char in resume_text[:200])
    
    base_score = 60
    if word_count > 200:
        base_score += 10
    if has_email:
        base_score += 5
    if has_phone:
        base_score += 5
    
    return {
        "overall_score": min(base_score, 85),
        "summary": f"Your resume shows promise with {word_count} words of content. There are several areas where strategic improvements could significantly increase your interview callback rate.",
        "strengths": [
            "Resume contains contact information" if has_email else "Clear structure detected",
            "Appropriate length for professional resume",
            "Uses professional language and terminology"
        ],
        "improvements": [
            "Add more quantifiable achievements with specific metrics (e.g., 'increased sales by 30%')",
            "Include more industry-specific keywords for better ATS compatibility",
            "Consider adding a professional summary section at the top",
            "Use stronger action verbs to start bullet points (led, achieved, drove, etc.)"
        ],
        "keywords": [
            "leadership",
            "project management", 
            "data analysis",
            "team collaboration",
            "strategic planning"
        ],
        "ats_score": 72,
        "formatting_score": 68,
        "content_score": base_score
    }

@app.route('/api/analyze', methods=['POST'])
def analyze_resume():
    """Main endpoint for resume analysis"""
    
    if 'resume' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['resume']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Please upload PDF, DOC, or DOCX'}), 400
    
    try:
        # Save file
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Get file hash for caching
        file_hash = get_file_hash(file_path)
        
        # Check cache
        cached_result = check_cached_analysis(file_hash)
        if cached_result:
            os.remove(file_path)  # Delete file after reading
            return jsonify(cached_result)
        
        # Extract text based on file type
        if filename.endswith('.pdf'):
            resume_text = extract_text_from_pdf(file_path)
        elif filename.endswith('.docx'):
            resume_text = extract_text_from_docx(file_path)
        else:
            os.remove(file_path)
            return jsonify({'error': 'Unsupported file format'}), 400
        
        if not resume_text or len(resume_text.strip()) < 50:
            os.remove(file_path)
            return jsonify({'error': 'Could not extract text from resume. Please ensure the file is not scanned/image-based.'}), 400
        
        # Analyze with Claude
        analysis = analyze_resume_with_claude(resume_text)
        
        # Cache the result
        save_analysis(file_hash, analysis)
        
        # Delete uploaded file
        os.remove(file_path)
        
        return jsonify(analysis)
        
    except Exception as e:
        print(f"Error processing resume: {e}")
        return jsonify({'error': 'Error processing resume. Please try again.'}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get usage statistics"""
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM analyses")
    total_analyses = c.fetchone()[0]
    conn.close()
    
    return jsonify({
        'total_analyses': total_analyses,
        'avg_score': 75  # Mock data
    })

if __name__ == '__main__':
    print("Resume Analyzer API starting...")
    print("Access the API at http://localhost:5000")
    print("Don't forget to set ANTHROPIC_API_KEY environment variable!")
    app.run(debug=True, host='0.0.0.0', port=5000)
