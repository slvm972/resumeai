"""
Cycle N2b: вход ODT в /api/improve и /api/improve/{docx,odt,rtf,pdf} и на странице.
ODT нормализуется в DOCX адаптером app/utils/odt_to_docx.py, дальше работает цепочка DOCX.
Без сети (Groq подменён), без жёстко прописанных путей. Запуск: FLASK_ENV=testing python -m pytest tests/test_n2b_odt_routes.py -q
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

from app import create_app
from app.missing_routes4 import _extract_structured, _apply_improved_text_to_docx
from app.utils.normalize_input import normalize_to_docx
from app.utils import docx_to_odt, docx_to_rtf
from app.utils.creative_docx_builder import build_creative_docx

DATA = os.path.join(ROOT, 'tests', 'data', 'matrix')
EXTRA = os.path.join(ROOT, 'tests', 'data', 'n2_odt')
INDEX = os.path.join(ROOT, 'index.html')
ROUTES = ['docx', 'odt', 'rtf', 'pdf']
CORPUS = ['ua', 'designer', 'engineer', 'tabdates', 'hebrew_rtl']
FILES = [(n, os.path.join(DATA, n + '.odt')) for n in CORPUS] + \
        [(n, os.path.join(EXTRA, n + '.odt')) for n in ('two_columns', 'bold_italic')]
OLD_WORD, NEW_WORD = 'Led redesign', 'Directed redesign'


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def _b(x):
    return x.getvalue() if hasattr(x, 'getvalue') else x


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
    user = json['messages'][-1]['content']
    blocks = re.findall(r'###ITEM_\d+###\n.*?(?=\n\n###ITEM_|\n\nOUTPUT|\Z)', user, re.S)
    return _Resp('\n\n'.join(blocks).replace(OLD_WORD, NEW_WORD))


def _improve(client, raw, filename, mode='precise'):
    with patch('requests.post', side_effect=_fake_groq):
        return client.post('/api/improve', data={'file': (io.BytesIO(raw), filename), 'creativity_mode': mode},
                           content_type='multipart/form-data')


def _export(client, route, text, original=None, filename='x.odt', item_ids=None):
    data = {'improved_resume': text}
    if original is not None:
        data['original_file'] = (io.BytesIO(original), filename)
    if item_ids is not None:
        data['item_ids'] = json.dumps(item_ids)
    return client.post('/api/improve/' + route, data=data, content_type='multipart/form-data')


def _flat(s):
    return re.sub(r'[\s\u2022\u00b7*\-\u2013\u2014,;|:\\{}]+', '', s)


def _text(route, body):
    if route == 'docx':
        d = Document(io.BytesIO(body))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.extend(c.text for c in row.cells)
        return '\n'.join(parts)
    if route == 'odt':
        from lxml import etree
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            return ''.join(etree.fromstring(z.read('content.xml')).itertext())
    if route == 'rtf':
        s = body.decode('ascii')
        return re.sub(r'\\u(-?\d+)\?', lambda m: chr(int(m.group(1)) % 65536), s)
    pypdf = pytest.importorskip('PyPDF2')
    return '\n'.join(p.extract_text() or '' for p in pypdf.PdfReader(io.BytesIO(body)).pages)


def _doc_zip(body, part):
    return zipfile.ZipFile(io.BytesIO(body)).read(part)


# ---------------- /api/improve для ODT ----------------

@pytest.mark.parametrize('name,path', FILES)
def test_improve_odt_passes_normalized_docx_to_pipeline(client, name, path):
    seen = {}
    result = {'success': True, 'improved_resume': 'x', 'display_text': 'x', 'detected_language': 'English',
              'item_ids': ['001'], 'quality_report': None, 'has_original_docx': True, 'tokens_used': 1}

    def fake_pipeline(original_bytes, filename, text, key, mode='precise'):
        seen.update(b=original_bytes, n=filename)
        return dict(result)
    with patch('app.missing_routes4._run_improve_pipeline', side_effect=fake_pipeline):
        r = client.post('/api/improve', data={'file': (io.BytesIO(_read(path)), name + '.odt')},
                        content_type='multipart/form-data')
    assert r.status_code == 200
    assert seen['n'] == 'resume.docx' and zipfile.is_zipfile(io.BytesIO(seen['b']))
    # байты архива зависят от времени записи в zip, поэтому сравниваем содержимое документа
    assert _doc_zip(seen['b'], 'word/document.xml') == _doc_zip(normalize_to_docx(_read(path), name + '.odt'), 'word/document.xml')
    body = r.get_json()
    assert body['original_format'] == 'odt' and body['has_original_docx'] is True


def test_improve_odt_creative_goes_through_creative_v2_with_normalized_docx(client):
    seen = {}

    def fake_creative(original_bytes, filename, text, cfg):
        seen.update(n=filename, docx=zipfile.is_zipfile(io.BytesIO(original_bytes)))
        return {'attempted': True, 'acceptable': True, 'status': 'ok', 'improved_resume': 'Creative text',
                'display_text': 'Creative text', 'detected_language': 'English', 'rounds': 1, 'calls': 1}
    client.application.config['CREATIVE_V2_ENABLED'] = True
    with patch('app.creative_bridge.creative_improve', side_effect=fake_creative):
        r = client.post('/api/improve', data={'file': (io.BytesIO(_read(FILES[1][1])), 'designer.odt'),
                                              'creativity_mode': 'creative'}, content_type='multipart/form-data')
    assert r.status_code == 200 and r.get_json()['engine'] == 'creative_v2'
    assert seen == {'n': 'resume.docx', 'docx': True}
    assert r.get_json()['original_format'] == 'odt'


@pytest.mark.parametrize('ext,expected', [('odt', 'odt'), ('rtf', 'rtf'), ('txt', 'txt'), ('docx', 'docx'), ('pdf', 'pdf'),
                                          ('doc', 'doc')])
def test_original_format_is_explicit_by_extension(client, ext, expected):
    blob = {'odt': _read(FILES[0][1]), 'txt': b'Jane Doe\nEngineer with many years of experience',
            'rtf': b'{\\rtf1\\ansi Jane Doe}', 'pdf': b'%PDF-1.4 fake', 'doc': b'binary-doc',
            'docx': _read(os.path.join(DATA, 'ua.docx'))}[ext]
    with patch('app.missing_routes4._run_improve_pipeline', return_value={
            'success': True, 'improved_resume': 'x', 'display_text': 'x', 'detected_language': 'English', 'item_ids': [],
            'quality_report': None, 'has_original_docx': False, 'tokens_used': 1}):
        r = client.post('/api/improve', data={'file': (io.BytesIO(blob), 'cv.' + ext)}, content_type='multipart/form-data')
    assert r.status_code == 200 and r.get_json()['original_format'] == expected


@pytest.mark.parametrize('ext,blob', [('rtf', b'{\\rtf1\\ansi Jane Doe Engineer with long experience}'),
                                      ('pdf', b'%PDF-1.4 fake pdf content that is long enough here')])
def test_improve_rtf_pdf_keep_old_path(client, ext, blob):
    seen = {}

    def fake_pipeline(original_bytes, filename, text, key, mode='precise'):
        seen.update(b=original_bytes, n=filename)
        return {'success': True, 'improved_resume': 'x', 'display_text': 'x', 'detected_language': 'English',
                'item_ids': [], 'quality_report': None, 'has_original_docx': False, 'tokens_used': 1}
    with patch('app.missing_routes4._run_improve_pipeline', side_effect=fake_pipeline):
        r = client.post('/api/improve', data={'file': (io.BytesIO(blob), 'cv.' + ext)}, content_type='multipart/form-data')
    assert r.status_code == 200 and seen == {'b': blob, 'n': 'cv.' + ext}
    assert r.get_json()['has_original_docx'] is False


# ---------------- Precise: item_ids и экспорт ----------------

@pytest.mark.parametrize('name,path', FILES)
def test_improve_odt_item_ids_match_normalized_paragraphs_on_export(client, name, path):
    raw = _read(path)
    r = _improve(client, raw, name + '.odt')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] and body['original_format'] == 'odt' and body['has_original_docx'] is True
    ids = body['item_ids']
    items = _extract_structured(Document(io.BytesIO(normalize_to_docx(raw, name + '.odt'))))
    assert len(ids) == len(items) and ids == [str(i + 1).zfill(3) for i in range(len(items))]
    exp = _export(client, 'docx', body['improved_resume'], raw, name + '.odt', ids)
    assert exp.status_code == 200
    out = [i['text'] for i in _extract_structured(Document(io.BytesIO(exp.data)))]
    assert len(out) == len(items)
    if name == 'designer':
        assert any(NEW_WORD in t for t in out) and not any(OLD_WORD in t for t in out)


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('name,path', FILES)
def test_precise_odt_export_equals_direct_docx_chain(client, name, path, route):
    """Экспорт для .odt должен быть тем же, что даёт прямая цепочка DOCX над нормализованным файлом."""
    raw = _read(path)
    body = _improve(client, raw, name + '.odt').get_json()
    ids = body['item_ids']
    exp = _export(client, route, body['improved_resume'], raw, name + '.odt', ids)
    assert exp.status_code == 200
    direct_docx = _apply_improved_text_to_docx(normalize_to_docx(raw, name + '.odt'), body['improved_resume'], ids).getvalue()
    if route == 'docx':
        assert _doc_zip(exp.data, 'word/document.xml') == _doc_zip(direct_docx, 'word/document.xml')
    elif route == 'odt':
        assert _doc_zip(exp.data, 'content.xml') == _doc_zip(_b(docx_to_odt.docx_bytes_to_odt(direct_docx)), 'content.xml')
    elif route == 'rtf':
        assert exp.data == _b(docx_to_rtf.docx_bytes_to_rtf(direct_docx))
    else:
        assert exp.data[:4] == b'%PDF'
        pypdf = pytest.importorskip('PyPDF2')
        assert len(pypdf.PdfReader(io.BytesIO(exp.data)).pages) >= 1
    if name == 'designer':
        assert _flat(NEW_WORD) in _flat(_text(route, exp.data))


# ---------------- Creative: экспорт ----------------

@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('name,path', FILES)
def test_creative_odt_export_builds_from_original_layout(client, name, path, route):
    raw = _read(path)
    norm = normalize_to_docx(raw, name + '.odt')
    items = _extract_structured(Document(io.BytesIO(norm)))
    ctext = '\n'.join(i['text'] for i in items)                      # Creative-текст без меток ###ITEM_
    try:
        direct = build_creative_docx(norm, ctext)
    except Exception:
        pytest.skip('сборщик Creative не принимает этот файл (прежний путь)')
    exp = _export(client, route, ctext, raw, name + '.odt')
    assert exp.status_code == 200
    if route == 'docx':
        assert _doc_zip(exp.data, 'word/document.xml') == _doc_zip(direct, 'word/document.xml')
    elif route == 'odt':
        assert _doc_zip(exp.data, 'content.xml') == _doc_zip(_b(docx_to_odt.docx_bytes_to_odt(direct)), 'content.xml')
    elif route == 'rtf':
        assert exp.data == _b(docx_to_rtf.docx_bytes_to_rtf(direct))
    else:
        assert exp.data[:4] == b'%PDF'


# ---------------- прежнее поведение ----------------

@pytest.mark.parametrize('route', ROUTES)
def test_export_without_original_file_unchanged(client, route):
    exp = _export(client, route, 'Jordan Ellis\nManaged inventory reports')
    assert exp.status_code == 200
    assert _flat('Managed inventory reports') in _flat(_text(route, exp.data))


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('ext,blob', [('rtf', b'{\\rtf1\\ansi Jordan Ellis}'), ('pdf', b'%PDF-1.4 fake')])
def test_rtf_pdf_original_gives_previous_text_path(client, route, ext, blob):
    exp = _export(client, route, '###ITEM_001###\nJordan Ellis\n\n###ITEM_002###\nManaged reports', blob, 'resume.' + ext,
                  ['001', '002'])
    assert exp.status_code == 200
    assert _flat('Managed reports') in _flat(_text(route, exp.data))


def _odt_variants():
    ok = _read(FILES[0][1])
    ent = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
           '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
           'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text><text:p>&x;</text:p>'
           '</office:text></office:body></office:document-content>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
        z.writestr('content.xml', ent)
    return {
        'broken': ok[:len(ok) // 2], 'empty': b'', 'xxe': buf.getvalue(),
        'docx_named_odt': _read(os.path.join(DATA, 'ua.docx')),
        'txt_named_odt': b'Jane Doe\nEngineer with a long history of experience in many fields',
        'garbage': b'\x00\x01\x02\xff\xfe' * 10,
    }


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('kind', ['broken', 'empty', 'xxe', 'docx_named_odt', 'txt_named_odt', 'garbage'])
def test_bad_or_fake_odt_gives_previous_path_200_without_error_text(client, route, kind):
    blob = _odt_variants()[kind]
    exp = _export(client, route, '###ITEM_001###\nIvan Petrov\n\n###ITEM_002###\nEngineer', blob, 'cv.odt', ['001', '002'])
    assert exp.status_code == 200
    assert not exp.data.startswith(b'{"') and b'Traceback' not in exp.data[:300]
    assert _flat('Engineer') in _flat(_text(route, exp.data))


@pytest.mark.parametrize('kind', ['broken', 'xxe', 'docx_named_odt', 'txt_named_odt'])
def test_improve_bad_or_fake_odt_keeps_old_path(client, kind):
    seen = {}

    def fake_pipeline(original_bytes, filename, text, key, mode='precise'):
        seen.update(b=original_bytes, n=filename)
        return {'success': True, 'improved_resume': 'x', 'display_text': 'x', 'detected_language': 'English',
                'item_ids': [], 'quality_report': None, 'has_original_docx': False, 'tokens_used': 1}
    blob = _odt_variants()[kind]
    with patch('app.missing_routes4._run_improve_pipeline', side_effect=fake_pipeline):
        r = client.post('/api/improve', data={'file': (io.BytesIO(blob), 'cv.odt')}, content_type='multipart/form-data')
    assert r.status_code == 200 and seen == {'b': blob, 'n': 'cv.odt'}
    assert r.get_json()['has_original_docx'] is False and r.get_json()['original_format'] == 'odt'


def test_docx_and_txt_inputs_unchanged(client):
    raw = _read(os.path.join(DATA, 'ua.docx'))
    items = _extract_structured(Document(io.BytesIO(raw)))
    ids = [str(i + 1).zfill(3) for i in range(len(items))]
    text = '\n\n'.join('###ITEM_%s###\n%s' % (ids[i], items[i]['text']) for i in range(len(items)))
    exp = _export(client, 'docx', text, raw, 'ua.docx', ids)
    ref = _apply_improved_text_to_docx(raw, text, ids).getvalue()
    assert _doc_zip(exp.data, 'word/document.xml') == _doc_zip(ref, 'word/document.xml')
    txt = _read(os.path.join(DATA, 'designer.txt'))
    r = _improve(client, txt, 'designer.txt')
    body = r.get_json()
    assert body['original_format'] == 'txt' and body['has_original_docx'] is True
    exp2 = _export(client, 'docx', body['improved_resume'], txt, 'designer.txt', body['item_ids'])
    d = Document(io.BytesIO(exp2.data))
    assert d.paragraphs[0].alignment is not None and int(d.paragraphs[0].alignment) == 1


# ---------------- страница ----------------

def _html():
    with open(INDEX, encoding='utf-8', newline='') as f:
        return f.read().replace('\r\n', '\n')


def test_page_constant_contains_odt_but_not_rtf_pdf():
    h = _html()
    m = re.search(r"const NORMALIZABLE_EXTS = \[(.*?)\];", h)
    assert m
    exts = re.findall(r"'([a-z]+)'", m.group(1))
    assert exts == ['docx', 'txt', 'odt']


def test_page_helper_in_node_odt_in_rtf_pdf_out():
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден')
    h = _html()
    src = re.search(r"const NORMALIZABLE_EXTS = .*?;\n\s*function isNormalizableFile\(f\) \{.*?\n\s*\}\n", h, re.S).group(0)
    cases = {'x.odt': True, 'CV.ODT': True, 'x.docx': True, 'x.txt': True, 'x.rtf': False, 'x.pdf': False, 'x.doc': False,
             'x.odt.pdf': False, 'odt': False}
    script = src + '\nconst cases = %s;\nconst out = {};\nfor (const k of Object.keys(cases)) out[k] = isNormalizableFile({name: k});\n' \
                   'console.log(JSON.stringify(out));\n' % json.dumps(cases)
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tmp:
        tmp.write(script)
    try:
        res = subprocess.run([node, tmp.name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(tmp.name)
    assert res.returncode == 0, res.stderr[:300]
    assert json.loads(res.stdout) == cases


def test_page_banner_variants_for_docx_odt_txt_other_and_node_check():
    h = _html()
    flat = re.sub(r"'\s*\+\s*\n\s*'", '', h)
    assert "The layout of your file is kept in the downloads (DOCX, ODT, RTF, PDF); complex elements" in flat
    assert 'A clean default layout is applied in the downloads (DOCX, ODT, RTF, PDF). ' in flat
    assert 'The layout of the original file is not preserved. ' in flat
    assert "const isDocxInput = !!(selectedFile && isNormalizableFile(selectedFile) && !/\\.txt$/i.test(selectedFile.name));" in h
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
