"""
Форматы загрузки резюме: отклонение неизвестных форматов (до обращения к ИИ, кредит не списывается)
и чтение RTF (пятый входной формат). Без сети: ИИ подменён.
Запуск: FLASK_ENV=testing python -m pytest tests/test_upload_formats.py -q
"""
import io
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import app as app_module
import app.missing_routes4 as mr
from app import create_app

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MSG = 'Unsupported file type. Please upload PDF, DOCX, ODT, RTF or TXT.'
_TEXT = 'Jordan Ellis\nOperations Analyst\nManaged weekly inventory reports for 40 stores, cutting reporting time by 35%'
_FAKE_RESULT = {'success': True, 'analysis': 'a', 'overall_score': 70, 'ats_score': 70, 'formatting': 70,
                'content': 70, 'summary': 's', 'strengths': [], 'improvements': [], 'key_skills': [],
                'detected_language': 'en', 'tokens_used': 1}
_COUNTER = {'n': 0}


def _bytes(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return value.getvalue() if hasattr(value, 'getvalue') else str(value).encode('utf-8')


@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        c = app.test_client()
        _COUNTER['n'] += 1
        resp = c.post('/api/user/register', json={'email': 'upload-fmt-%03d@example.com' % _COUNTER['n'],
                                                   'password': 'a-long-enough-password'})
        assert resp.status_code == 201
        yield c


def _credits(client):
    return client.get('/api/user/me').get_json()['subscription']['credits_remaining']


def _analyze(client, filename, data):
    """POST /api/analyze с файлом. Возвращает (response, mock анализатора)."""
    with patch('app.services.openrouter_service.OpenRouterService.analyze_resume',
               return_value=dict(_FAKE_RESULT)) as analyzer:
        resp = client.post('/api/analyze', data={'file': (io.BytesIO(data), filename)},
                           content_type='multipart/form-data')
    return resp, analyzer


# ---------------------------------------------------------------------------
# Шаг 1: отклонение неизвестных форматов
# ---------------------------------------------------------------------------

def test_allowed_extensions_constant():
    assert app_module._ALLOWED_UPLOAD_EXTENSIONS == ('.pdf', '.docx', '.odt', '.rtf', '.txt')
    assert app_module._UNSUPPORTED_FILE_MESSAGE == _MSG


@pytest.mark.parametrize('filename', ['resume.doc', 'resume.exe', 'resume', 'resume.docx.exe', '.hidden', 'resume.png', 'archive.zip'])
def test_unsupported_files_are_rejected_before_ai_and_credit_untouched(client, filename):
    before = _credits(client)
    resp, analyzer = _analyze(client, filename, b'plain text that looks like a resume ' * 5)
    data = resp.get_json()
    assert resp.status_code == 400 and data['success'] is False and data['error'] == _MSG
    assert analyzer.call_count == 0
    assert _credits(client) == before


def test_old_fallback_does_not_pass_garbage_to_ai(client):
    """Раньше неизвестный файл читался «как текст» и уходил в ИИ."""
    resp, analyzer = _analyze(client, 'binary.bin', b'\x00\x01\x02 garbage ' * 20)
    assert resp.status_code == 400 and analyzer.call_count == 0


@pytest.mark.parametrize('filename', ['RESUME.TXT', 'Resume.Txt', 'resume.txt'])
def test_extension_check_is_case_insensitive(client, filename):
    resp, analyzer = _analyze(client, filename, _TEXT.encode('utf-8'))
    assert resp.status_code == 200 and analyzer.call_count == 1


# ---------------------------------------------------------------------------
# Шаг 2: чтение RTF
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('text', [
    _TEXT,
    'Иван Петров\nУправлял командой из 5 человек, сократил отчётность на 35%',
    'דוד כהן\nניהל צוות של 5 עובדים',
])
def test_rtf_upload_is_read_as_text_without_rtf_codes(client, text):
    rtf = _bytes(mr._generate_rtf(text))
    before = _credits(client)
    resp, analyzer = _analyze(client, 'resume.rtf', rtf)
    assert resp.status_code == 200 and analyzer.call_count == 1
    sent = analyzer.call_args[0][1]                              # текст, который ушёл бы в ИИ
    assert text.split('\n')[0] in sent and text.split('\n')[-1] in sent
    for code in ('\\rtf', '\\par', '\\ansi', '\\fonttbl', '{\\'):
        assert code not in sent, code
    assert _credits(client) == before - 1                        # кредит списан только за успешный анализ


def test_rtf_extension_in_upper_case_is_accepted(client):
    resp, analyzer = _analyze(client, 'CV.RTF', _bytes(mr._generate_rtf(_TEXT)))
    assert resp.status_code == 200 and analyzer.call_count == 1


@pytest.mark.parametrize('payload, expected', [
    (b'This is plain text with an rtf extension', 'Cannot read RTF file'),
    (b'', 'Cannot read RTF file'),
    (b'{\\rtf1\\ansi {\\fonttbl{\\f0 Arial;}} }', 'contains no readable text'),
])
def test_broken_or_empty_rtf_is_rejected_with_clear_error_and_no_charge(client, payload, expected):
    before = _credits(client)
    resp, analyzer = _analyze(client, 'broken.rtf', payload)
    data = resp.get_json()
    assert resp.status_code == 400 and data['success'] is False and expected in data['error']
    assert analyzer.call_count == 0
    assert _credits(client) == before


# ---------------------------------------------------------------------------
# Остальные форматы продолжают читаться
# ---------------------------------------------------------------------------

def _docx_bytes():
    from docx import Document
    d = Document()
    for line in _TEXT.split('\n'):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize('filename, make', [
    ('r.txt', lambda: _TEXT.encode('utf-8')),
    ('r.docx', _docx_bytes),
    ('r.pdf', lambda: _bytes(mr._generate_pdf(_TEXT))),
    ('r.odt', lambda: _bytes(mr._generate_odt(_TEXT))),
    ('r.rtf', lambda: _bytes(mr._generate_rtf(_TEXT))),
])
def test_all_five_formats_are_read(client, filename, make):
    resp, analyzer = _analyze(client, filename, make())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert 'Jordan Ellis' in analyzer.call_args[0][1]


# ---------------------------------------------------------------------------
# Страница: проверка формата до отправки и тексты
# ---------------------------------------------------------------------------

def _index():
    with open(os.path.join(_ROOT, 'index.html'), encoding='utf-8') as f:
        return f.read()


def test_index_lists_rtf_among_input_formats():
    html = _index()
    assert 'accept=".pdf,.docx,.odt,.rtf,.txt"' in html
    assert 'Supports PDF, DOCX, ODT, RTF, TXT formats' in html
    assert 'Upload your resume as PDF, DOCX, ODT, RTF or TXT.' in html
    assert 'accept=".pdf,.docx,.odt,.txt"' not in html


def test_index_checks_file_type_before_upload_with_same_message_as_server():
    html = _index()
    start = html.index('function setFile(f)')
    body = html[start:start + 700]
    assert "['pdf', 'docx', 'odt', 'rtf', 'txt'].indexOf(ext) === -1" in body
    assert "showError('" + _MSG + "')" in body
    assert body.index('indexOf(ext)') < body.index('selectedFile = f')   # проверка раньше, чем файл принят
