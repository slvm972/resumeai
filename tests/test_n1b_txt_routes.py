"""
Cycle N1b: вход TXT в маршрутах /api/improve, /api/improve/{docx,odt,rtf,pdf} и на странице.
TXT нормализуется в DOCX на сервере (app/utils/normalize_input.py), дальше та же цепочка, что для DOCX.
Без сети (Groq подменён), без жёстко прописанных путей. Запуск: FLASK_ENV=testing python -m pytest tests/test_n1b_txt_routes.py -q
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from unittest.mock import patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from app import create_app
from app.missing_routes4 import _extract_structured, _apply_improved_text_to_docx
from app.utils.normalize_input import normalize_to_docx
from app.utils import docx_to_odt, docx_to_rtf, docx_to_pdf
from app.utils.creative_docx_builder import build_creative_docx

DATA = os.path.join(ROOT, 'tests', 'data', 'matrix')
INDEX = os.path.join(ROOT, 'index.html')
ROUTES = ['docx', 'odt', 'rtf', 'pdf']
OLD_WORD, NEW_WORD = 'Led redesign', 'Directed redesign'


def _read(name):
    with open(os.path.join(DATA, name), 'rb') as f:
        return f.read()


@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['GROQ_API_KEY'] = 'test-groq-key-not-used-mocked-out'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['admin'] = 'admin'
        yield c


class _Resp:
    status_code = 200

    def __init__(self, content):
        self._c = content

    def json(self):
        return {'choices': [{'message': {'content': self._c}}], 'usage': {'total_tokens': 7}}


def _fake_groq(url, headers=None, json=None, timeout=None, **kw):
    """Подмена Groq: возвращает блоки как есть, заменяя OLD_WORD на NEW_WORD (одно честное улучшение)."""
    user = json['messages'][-1]['content']
    blocks = re.findall(r'###ITEM_\d+###\n.*?(?=\n\n###ITEM_|\n\nOUTPUT|\Z)', user, re.S)
    return _Resp('\n\n'.join(blocks).replace(OLD_WORD, NEW_WORD))


def _improve(client, name, filename=None, raw=None, mode='precise'):
    raw = raw if raw is not None else _read(name + '.txt')
    with patch('requests.post', side_effect=_fake_groq):
        r = client.post('/api/improve', data={'file': (io.BytesIO(raw), filename or name + '.txt'),
                                              'creativity_mode': mode}, content_type='multipart/form-data')
    return r


def _export(client, route, text, original=None, filename='designer.txt', item_ids=None):
    data = {'improved_resume': text}
    if original is not None:
        data['original_file'] = (io.BytesIO(original), filename)
    if item_ids is not None:
        data['item_ids'] = json.dumps(item_ids)
    return client.post('/api/improve/' + route, data=data, content_type='multipart/form-data')


def _text(route, body):
    if route == 'docx':
        d = Document(io.BytesIO(body))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.extend(c.text for c in row.cells)
        return '\n'.join(parts)
    if route == 'odt':
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            return ''.join(etree.fromstring(z.read('content.xml')).itertext())
    if route == 'rtf':
        s = body.decode('ascii')
        return re.sub(r'\\u(-?\d+)\?', lambda m: chr(int(m.group(1)) % 65536), s)
    pypdf = pytest.importorskip('PyPDF2')
    return '\n'.join(p.extract_text() or '' for p in pypdf.PdfReader(io.BytesIO(body)).pages)


def _flat(s):
    return re.sub(r'[\s\u2022\u00b7*\-\u2013\u2014,;|:\\{}]+', '', s)


def _has_template_layout(route, body):
    """Признак оформления шаблона (центрированное имя, правый таб/линии), а не простого файла из текста."""
    if route == 'docx':
        d = Document(io.BytesIO(body))
        return d.paragraphs[0].alignment is not None and int(d.paragraphs[0].alignment) == 1 \
            and any(p._p.pPr is not None and p._p.pPr.find(qn('w:pBdr')) is not None for p in d.paragraphs)
    if route == 'odt':
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            c = z.read('content.xml') + z.read('styles.xml')
        return b'tab-stop' in c and b'<text:list' in c
    if route == 'rtf':
        return b'\\qc' in body and b'\\brdrb' in body
    return b'FiraSans-Bold' in body or b'LiberationSerif' in body     # прежний PDF из текста их не использует


# ---------------- /api/improve для TXT ----------------

def test_improve_txt_passes_normalized_docx_to_pipeline(client):
    captured = {}
    result = {'success': True, 'improved_resume': 'x', 'display_text': 'x', 'detected_language': 'English',
              'item_ids': ['001'], 'quality_report': None, 'has_original_docx': True, 'tokens_used': 1}

    def fake_pipeline(original_bytes, filename, text, key, mode='precise'):
        captured.update(b=original_bytes, n=filename)
        return dict(result)
    with patch('app.missing_routes4._run_improve_pipeline', side_effect=fake_pipeline):
        r = client.post('/api/improve', data={'file': (io.BytesIO(_read('designer.txt')), 'designer.txt')},
                        content_type='multipart/form-data')
    assert r.status_code == 200
    assert captured['n'] == 'resume.docx' and zipfile.is_zipfile(io.BytesIO(captured['b']))
    body = r.get_json()
    assert body['original_format'] == 'txt' and body['has_original_docx'] is True


def test_improve_txt_item_ids_match_normalized_paragraphs_on_export(client):
    raw = _read('designer.txt')
    r = _improve(client, 'designer')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] and body['original_format'] == 'txt' and body['has_original_docx'] is True
    ids = body['item_ids']
    items = _extract_structured(Document(io.BytesIO(normalize_to_docx(raw, 'designer.txt'))))
    assert len(ids) == len(items) and ids == [str(i + 1).zfill(3) for i in range(len(items))]
    # на экспорте файл нормализуется заново, и текст по item_ids ложится в те же абзацы
    exp = _export(client, 'docx', body['improved_resume'], raw, 'designer.txt', ids)
    assert exp.status_code == 200
    out = [i['text'] for i in _extract_structured(Document(io.BytesIO(exp.data)))]
    assert len(out) == len(items)
    assert any(NEW_WORD in t for t in out) and not any(OLD_WORD in t for t in out)


@pytest.mark.parametrize('route', ROUTES)
def test_precise_txt_export_has_template_layout_and_improved_text(client, route):
    raw = _read('designer.txt')
    body = _improve(client, 'designer').get_json()
    exp = _export(client, route, body['improved_resume'], raw, 'designer.txt', body['item_ids'])
    assert exp.status_code == 200
    assert _flat(NEW_WORD) in _flat(_text(route, exp.data))
    assert _has_template_layout(route, exp.data), route


@pytest.mark.parametrize('route', ROUTES)
def test_creative_txt_export_builds_from_template(client, route):
    raw = _read('designer.txt')
    text = raw.decode('utf-8').replace('Led', 'Directed')            # условный Creative-текст без меток ###ITEM_
    assert 'Directed' in text
    exp = _export(client, route, text, raw, 'designer.txt')            # item_ids пуст, как у creative_v2
    assert exp.status_code == 200
    assert _flat('Directed') in _flat(_text(route, exp.data))
    assert _has_template_layout(route, exp.data), route


def test_creative_txt_goes_through_creative_v2_with_normalized_docx(client):
    seen = {}

    def fake_creative(original_bytes, filename, text, cfg):
        seen.update(n=filename, docx=zipfile.is_zipfile(io.BytesIO(original_bytes)))
        return {'attempted': True, 'acceptable': True, 'status': 'ok', 'improved_resume': 'Creative text',
                'display_text': 'Creative text', 'detected_language': 'English', 'rounds': 1, 'calls': 1}
    client.application.config['CREATIVE_V2_ENABLED'] = True
    with patch('app.creative_bridge.creative_improve', side_effect=fake_creative):
        r = client.post('/api/improve', data={'file': (io.BytesIO(_read('designer.txt')), 'designer.txt'),
                                              'creativity_mode': 'creative'}, content_type='multipart/form-data')
    assert r.status_code == 200 and r.get_json()['engine'] == 'creative_v2'
    assert seen == {'n': 'resume.docx', 'docx': True}
    assert r.get_json()['original_format'] == 'txt'


# ---------------- прежнее поведение ----------------

@pytest.mark.parametrize('route', ROUTES)
def test_export_without_original_file_unchanged_200_and_text(client, route):
    exp = _export(client, route, 'Jordan Ellis\nManaged inventory reports')
    assert exp.status_code == 200
    assert _flat('Managed inventory reports') in _flat(_text(route, exp.data))


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('ext,blob', [('odt', b'PK\x03\x04odt-not-docx'), ('rtf', b'{\\rtf1\\ansi Jordan Ellis}'),
                                      ('pdf', b'%PDF-1.4 fake')])
def test_odt_rtf_pdf_original_gives_previous_text_path(client, route, ext, blob):
    exp = _export(client, route, '###ITEM_001###\nJordan Ellis\n\n###ITEM_002###\nManaged reports', blob,
                  'resume.' + ext, ['001', '002'])
    assert exp.status_code == 200
    assert _flat('Managed reports') in _flat(_text(route, exp.data))


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('kind', ['cp1251', 'garbage', 'empty'])
def test_non_utf8_or_garbage_txt_gives_previous_path_without_error(client, route, kind):
    blob = {'cp1251': 'Иван Петров\nИнженер'.encode('cp1251'), 'garbage': b'\x00\x01\x02\xff\xfe',
            'empty': b''}[kind]
    exp = _export(client, route, '###ITEM_001###\nIvan Petrov\n\n###ITEM_002###\nEngineer', blob, 'cv.txt', ['001', '002'])
    assert exp.status_code == 200
    assert b'Traceback' not in exp.data[:300] and not exp.data.startswith(b'{"')      # файл, а не JSON с ошибкой
    assert _flat('Engineer') in _flat(_text(route, exp.data))


def test_improve_non_utf8_txt_keeps_old_path(client):
    seen = {}

    def fake_pipeline(original_bytes, filename, text, key, mode='precise'):
        seen.update(b=original_bytes, n=filename)
        return {'success': True, 'improved_resume': 'x', 'display_text': 'x', 'detected_language': 'English',
                'item_ids': [], 'quality_report': None, 'has_original_docx': False, 'tokens_used': 1}
    raw = 'Иван Петров'.encode('cp1251')
    with patch('app.missing_routes4._run_improve_pipeline', side_effect=fake_pipeline):
        r = client.post('/api/improve', data={'file': (io.BytesIO(raw), 'cv.txt')}, content_type='multipart/form-data')
    assert r.status_code == 200 and seen == {'b': raw, 'n': 'cv.txt'}
    assert r.get_json()['has_original_docx'] is False


@pytest.mark.parametrize('name', ['ua', 'designer'])
def test_docx_input_unchanged(client, name):
    raw = _read(name + '.docx')
    items = _extract_structured(Document(io.BytesIO(raw)))
    ids = [str(i + 1).zfill(3) for i in range(len(items))]
    text = '\n\n'.join('###ITEM_%s###\n%s' % (ids[i], items[i]['text']) for i in range(len(items)))
    ref_docx = _apply_improved_text_to_docx(raw, text, ids).getvalue()
    # DOCX: то же, что дала бы прямая цепочка
    exp = _export(client, 'docx', text, raw, name + '.docx', ids)
    assert exp.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(exp.data)).read('word/document.xml') == \
        zipfile.ZipFile(io.BytesIO(ref_docx)).read('word/document.xml')
    odt = _export(client, 'odt', text, raw, name + '.docx', ids)
    ref_odt = docx_to_odt.docx_bytes_to_odt(ref_docx)
    ref_odt = ref_odt.getvalue() if hasattr(ref_odt, 'getvalue') else ref_odt
    assert zipfile.ZipFile(io.BytesIO(odt.data)).read('content.xml') == zipfile.ZipFile(io.BytesIO(ref_odt)).read('content.xml')
    rtf = _export(client, 'rtf', text, raw, name + '.docx', ids)
    ref_rtf = docx_to_rtf.docx_bytes_to_rtf(ref_docx)
    ref_rtf = ref_rtf.getvalue() if hasattr(ref_rtf, 'getvalue') else ref_rtf
    assert rtf.data == ref_rtf
    # Creative для DOCX: прежняя сборка по оригиналу
    ctext = '\n'.join(i['text'] for i in items)
    cr = _export(client, 'docx', ctext, raw, name + '.docx')
    assert cr.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(cr.data)).read('word/document.xml') == \
        zipfile.ZipFile(io.BytesIO(build_creative_docx(raw, ctext))).read('word/document.xml')


def test_improve_docx_input_keeps_original_name_and_bytes(client):
    seen = {}

    def fake_pipeline(original_bytes, filename, text, key, mode='precise'):
        seen.update(b=original_bytes, n=filename)
        return {'success': True, 'improved_resume': 'x', 'display_text': 'x', 'detected_language': 'English',
                'item_ids': ['001'], 'quality_report': None, 'has_original_docx': True, 'tokens_used': 1}
    raw = _read('ua.docx')
    with patch('app.missing_routes4._run_improve_pipeline', side_effect=fake_pipeline):
        r = client.post('/api/improve', data={'file': (io.BytesIO(raw), 'ua.docx')}, content_type='multipart/form-data')
    assert r.status_code == 200 and seen == {'b': raw, 'n': 'ua.docx'}


# ---------------- страница ----------------

def _html():
    with open(INDEX, encoding='utf-8', newline='') as f:
        return f.read().replace('\r\n', '\n')


def test_page_uses_single_constant_for_normalizable_formats():
    h = _html()
    assert "const NORMALIZABLE_EXTS = ['docx', 'txt'];" in h
    assert h.count('isNormalizableFile(selectedFile)') >= 9
    # проверка по расширению .docx осталась только в баннере Creative (текст для входа DOCX), а не в условиях кнопок
    assert h.count("selectedFile.name.toLowerCase().endsWith('.docx')") == 1


def test_page_helper_in_node_txt_in_odt_pdf_rtf_out():
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден')
    h = _html()
    src = re.search(r"const NORMALIZABLE_EXTS = .*?;\n\s*function isNormalizableFile\(f\) \{.*?\n\s*\}\n", h, re.S).group(0)
    cases = {'cv.docx': True, 'CV.DOCX': True, 'cv.txt': True, 'My Resume.Txt': True, 'cv.pdf': False, 'cv.odt': False,
             'cv.rtf': False, 'cv.doc': False, 'cv.docx.pdf': False, 'docx': False, '': False}
    script = src + '\nconst cases = %s;\nconst out = {};\nfor (const k of Object.keys(cases)) out[k] = isNormalizableFile({name: k});\n' \
                   'out.__null = isNormalizableFile(null);\nconsole.log(JSON.stringify(out));\n' % json.dumps(cases)
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tmp:
        tmp.write(script)
    try:
        res = subprocess.run([node, tmp.name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(tmp.name)
    assert res.returncode == 0, res.stderr[:300]
    out = json.loads(res.stdout)
    assert out.pop('__null') is False and out == cases


def test_page_creative_notice_variants_and_node_check():
    h = _html()
    assert 'The layout of your DOCX file is kept' in h and 'A clean default layout is applied' in h
    assert 'The layout of the original file is not preserved.' in h
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден')
    for i, code in enumerate(re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', h, re.S)):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(code)
        try:
            r = subprocess.run([node, '--check', f.name], capture_output=True, text=True)
            assert r.returncode == 0, 'script %d: %s' % (i, r.stderr[:300])
        finally:
            os.unlink(f.name)


def test_page_keeps_crlf():
    with open(INDEX, 'rb') as f:
        d = f.read()
    assert d.count(b'\r\n') == d.count(b'\n') > 2000
