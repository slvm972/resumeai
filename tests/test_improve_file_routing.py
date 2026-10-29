"""
Улучшение резюме: файл отправляется на /api/improve только для DOCX и TXT. Для PDF, ODT и RTF страница
использует текст, извлечённый при анализе (сервер читает такие файлы при улучшении как «сырой текст»).
Без сети. Запуск: FLASK_ENV=testing python -m pytest tests/test_improve_file_routing.py -q
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

from app import create_app

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _index():
    with open(os.path.join(_ROOT, 'index.html'), encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------
# Страница: что именно отправляется на /api/improve
# ---------------------------------------------------------------------------

def test_file_branch_is_guarded_by_docx_txt_helper():
    html = _index()
    start = html.index('async function improveResume')
    body = html[start:start + 3500]
    assert 'if (canImproveFromFile(selectedFile))' in body
    assert 'if (selectedFile) {' not in body                      # прежнего безусловного условия больше нет
    file_branch = body[body.index('canImproveFromFile(selectedFile)'):body.index('} else {')]
    assert "formData.append('file', selectedFile)" in file_branch
    fallback = body[body.index('} else {'):]
    assert 'resume_text: lastResumeText' in fallback and 'original_format: lastOriginalFormat' in fallback
    assert 'creativity_mode: selectedCreativityMode' in fallback


def test_helper_decides_by_extension_in_node():
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден в окружении: проверка функции canImproveFromFile пропущена')
    html = _index()
    src = re.search(r'function canImproveFromFile\(f\) \{.*?\n\s*\}\n', html, re.S).group(0)
    cases = {
        'cv.docx': True, 'CV.DOCX': True, 'cv.txt': True, 'My Resume.Txt': True,
        'cv.pdf': False, 'cv.odt': False, 'cv.rtf': False, 'cv.doc': False,
        'cv.docx.pdf': False, 'docx': False, '': False,
    }
    script = src + '\nconst cases = %s;\nconst out = {};\nfor (const k of Object.keys(cases)) out[k] = canImproveFromFile({name: k});\n' \
                   'out.__null = canImproveFromFile(null);\nconsole.log(JSON.stringify(out));\n' % json.dumps(cases)
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tmp:
        tmp.write(script)
    try:
        res = subprocess.run([node, tmp.name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(tmp.name)
    assert res.returncode == 0, res.stderr[:300]
    out = json.loads(res.stdout)
    assert out.pop('__null') is False
    assert out == cases


# ---------------------------------------------------------------------------
# Сервер: путь «текст» (JSON) принимает текст для любого исходного формата
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        c = app.test_client()
        assert c.post('/api/user/register', json={'email': 'improve-routing@example.com',
                                                   'password': 'a-long-enough-password'}).status_code == 201
        yield c


_PIPELINE_RESULT = {'success': True, 'improved_resume': 'x', 'display_text': 'Jordan Ellis improved',
                    'detected_language': 'English', 'item_ids': [], 'quality_report': None,
                    'has_original_docx': False, 'tokens_used': 1}


@pytest.mark.parametrize('fmt', ['pdf', 'odt', 'rtf'])
def test_text_path_passes_extracted_text_to_pipeline_not_file_bytes(client, fmt):
    text = 'Jordan Ellis\nManaged weekly inventory reports for 40 stores, cutting reporting time by 35%'
    with patch('app.missing_routes4._run_improve_pipeline', return_value=dict(_PIPELINE_RESULT)) as pipe:
        resp = client.post('/api/improve', json={'resume_text': text, 'original_format': fmt,
                                                  'creativity_mode': 'precise'})
    assert resp.status_code == 200 and resp.get_json()['success'] is True
    args = pipe.call_args[0]
    assert args[0] is None and args[1] is None            # файл не передаётся: нет сырых байтов
    assert args[2] == text                                # передаётся извлечённый текст (без кодов RTF и мусора)
    assert '\\rtf' not in args[2] and '\\par' not in args[2]
