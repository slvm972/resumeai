"""
Cycle C2: Creative-ветка в маршрутах DOCX/ODT/RTF/PDF и условия на кнопках сайта.
Без сети. Данные: tests/data/creative_c1/. Запуск: FLASK_ENV=testing python -m pytest tests/test_creative_routes.py -q
"""
import io
import os
import re
import sys
import json
import shutil
import zipfile
import tempfile
import subprocess
import importlib
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

import config as config_module
importlib.reload(config_module)

import PyPDF2
from lxml import etree
from docx import Document
from docx.oxml.ns import qn

from app import create_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'tests', 'data', 'creative_c1')
with open(os.path.join(DATA, 'creative_texts.json'), encoding='utf-8') as f:
    TEXTS = json.load(f)

ODF_NS = {'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
          'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
          'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
          'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'}
ROUTES = ['docx', 'odt', 'rtf', 'pdf']


def orig(name):
    with open(os.path.join(DATA, name), 'rb') as f:
        return f.read()


DESIGNER = 'resume_english_designer.docx'
UA = 'resume_ua.docx'


@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['admin'] = 'admin'
        yield c


def post(client, route, text, original=None, filename='resume.docx', item_ids=None):
    data = {'improved_resume': text}
    if original is not None:
        data['original_file'] = (io.BytesIO(original), filename)
    if item_ids is not None:
        data['item_ids'] = item_ids
    return client.post('/api/improve/' + route, data=data, content_type='multipart/form-data')


def extract_text(route, body):
    """Текст из ответа маршрута для проверки."""
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
        s = re.sub(r'\\u(-?\d+)\?', lambda m: chr(int(m.group(1)) % 65536), s)
        return s
    reader = PyPDF2.PdfReader(io.BytesIO(body))
    return '\n'.join(p.extract_text() or '' for p in reader.pages)


def flat(s):
    return re.sub(r'[\s\u2022\u00b7*\-\u2013\u2014,;|:\\{}]+', '', s)


def has_layout(route, body):
    """Признак оформления оригинала (таблицы/шрифты нового конвертера), а не простого файла из текста."""
    if route == 'docx':
        d = Document(io.BytesIO(body))
        return len(d.tables) >= 1 and d.paragraphs[0].alignment is not None
    if route == 'odt':
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            root = etree.fromstring(z.read('content.xml'))
        return bool(root.findall('.//table:table', ODF_NS))
    if route == 'rtf':
        return b'\\trowd' in body
    return b'FiraSans-Bold' in body or b'LiberationSerif' in body   # прежний PDF из текста их не использует


# --- Creative с оригиналом: оформление оригинала и текст Creative ---------------------------------

@pytest.mark.parametrize('route', ROUTES)
def test_creative_with_original_keeps_layout_and_text(client, route):
    r = post(client, route, TEXTS['designer'], orig(DESIGNER))
    assert r.status_code == 200
    assert has_layout(route, r.data)
    got = flat(extract_text(route, r.data))
    for needle in ('SarahMitchell', 'Redefinedthehostonboardingflow', 'DesignTools', 'Figma'):
        assert needle in got, needle


@pytest.mark.parametrize('route', ROUTES)
def test_creative_text_replaces_old_text(client, route):
    r = post(client, route, TEXTS['designer'], orig(DESIGNER))
    got = flat(extract_text(route, r.data))
    assert flat('Led redesign of the host onboarding flow') not in got      # старая формулировка из оригинала
    assert flat('Redefined the host onboarding flow') in got                # новая из Creative


@pytest.mark.parametrize('route', ROUTES)
def test_creative_ua_paragraph_template(client, route):
    r = post(client, route, TEXTS['ua'], orig(UA), item_ids='[]')
    assert r.status_code == 200
    got = flat(extract_text(route, r.data))
    assert flat('Навчав двох молодших розробників') in got


@pytest.mark.parametrize('route', ROUTES)
def test_empty_item_ids_list_is_creative_too(client, route):
    r = post(client, route, TEXTS['designer'], orig(DESIGNER), item_ids='[]')
    assert r.status_code == 200 and has_layout(route, r.data)


# --- ошибки и обходные пути -----------------------------------------------------------------------

@pytest.mark.parametrize('route', ROUTES)
def test_broken_original_falls_back_with_200(client, route):
    r = post(client, route, TEXTS['designer'], b'this is not a docx')
    assert r.status_code == 200
    assert 'Sarah' in extract_text(route, r.data) or route == 'rtf'
    assert not has_layout(route, r.data)
    assert b'Traceback' not in r.data and b'BadZipFile' not in r.data


@pytest.mark.parametrize('route', ROUTES)
def test_garbage_text_falls_back_with_200(client, route):
    r = post(client, route, '@@@ ### ---', orig(DESIGNER))
    assert r.status_code == 200 and not has_layout(route, r.data)


@pytest.mark.parametrize('route', ROUTES)
def test_builder_exception_falls_back_without_error_text(client, route):
    with patch('app.utils.creative_docx_builder.build_creative_docx', side_effect=RuntimeError('boom /secret/path')):
        r = post(client, route, TEXTS['designer'], orig(DESIGNER))
    assert r.status_code == 200 and b'secret' not in r.data and not has_layout(route, r.data)


@pytest.mark.parametrize('route', ROUTES)
def test_converter_exception_falls_back_with_200(client, route):
    if route == 'docx':
        pytest.skip('для DOCX отдельного конвертера нет')
    target = {'odt': 'app.utils.docx_to_odt.docx_bytes_to_odt', 'rtf': 'app.utils.docx_to_rtf.docx_bytes_to_rtf',
              'pdf': 'app.utils.docx_to_pdf.docx_bytes_to_pdf'}[route]
    with patch(target, side_effect=RuntimeError('boom /secret/path')):
        r = post(client, route, TEXTS['designer'], orig(DESIGNER))
    assert r.status_code == 200 and b'secret' not in r.data and not has_layout(route, r.data)


@pytest.mark.parametrize('route', ROUTES)
def test_creative_without_original_works_as_before(client, route):
    r = post(client, route, TEXTS['designer'])
    assert r.status_code == 200 and not has_layout(route, r.data)


@pytest.mark.parametrize('route', ROUTES)
def test_non_docx_original_is_not_used(client, route):
    r = post(client, route, TEXTS['designer'], orig(DESIGNER), filename='resume.pdf')
    assert r.status_code == 200 and not has_layout(route, r.data)


# --- Precise не затронут ---------------------------------------------------------------------------

def _marked(raw):
    from app.missing_routes4 import _extract_structured
    items = _extract_structured(Document(io.BytesIO(raw)))
    ids = [str(i + 1).zfill(3) for i in range(len(items))]
    return "\n\n".join("###ITEM_%s:PLAIN###\n%s" % (ids[i], items[i]['text']) for i in range(len(items))), ids


@pytest.mark.parametrize('route', ROUTES)
def test_precise_branch_still_applies_text_by_item_ids(client, route):
    raw = orig(DESIGNER)
    text, ids = _marked(raw)
    text = text.replace('Conducted 50+ user research sessions', 'Ran 50+ user research sessions')
    r = post(client, route, text, raw, item_ids=json.dumps(ids))
    assert r.status_code == 200
    got = flat(extract_text(route, r.data))
    assert flat('Ran 50+ user research sessions') in got and flat('Conducted 50+ user research') not in got


@pytest.mark.parametrize('route', ['odt', 'rtf', 'pdf'])
def test_precise_with_empty_item_ids_does_not_use_creative_builder(client, route):
    raw = orig(DESIGNER)
    text, _ids = _marked(raw)
    with patch('app.utils.creative_docx_builder.build_creative_docx') as builder:
        r = post(client, route, text, raw, item_ids='[]')
    assert r.status_code == 200
    builder.assert_not_called()


def test_docx_precise_markers_with_empty_item_ids_known_behavior(client):
    # Известное поведение Precise-ветки DOCX (не менялось в C2): метки есть, item_ids пуст ->
    # _apply_improved_text_to_docx возвращает исходный текст. Creative сюда не попадает (меток нет).
    raw = orig(DESIGNER)
    text, _ids = _marked(raw)
    text = text.replace('Conducted 50+ user research sessions', 'Ran 50+ user research sessions')
    r = post(client, 'docx', text, raw, item_ids='[]')
    assert r.status_code == 200
    assert 'Conducted 50+ user research sessions' in extract_text('docx', r.data)


# --- кнопки сайта ----------------------------------------------------------------------------------

def _html():
    with open(os.path.join(ROOT, 'index.html'), encoding='utf-8', newline='') as f:
        return f.read().replace('\r\n', '\n')


def _body(name):
    src = _html()
    m = re.search(r'async function %s\s*\(\)\s*\{' % name, src)
    assert m, name
    i, depth = m.end(), 1
    while depth:
        c = src[i]
        depth += (c == '{') - (c == '}')
        i += 1
    return src[m.start():i]


# N1b: проверка '.docx' заменена общей функцией isNormalizableFile (NORMALIZABLE_EXTS = docx, txt)
CREATIVE_COND = ("else if (isNormalizableFile(selectedFile)"
                 " && lastEngine === 'creative_v2') {")


@pytest.mark.parametrize('fn', ['downloadOdt', 'downloadRtf', 'downloadPdf'])
def test_buttons_send_original_for_creative_without_item_ids(fn):
    b = _body(fn)
    assert CREATIVE_COND in b
    start = b.index(CREATIVE_COND)
    branch = b[start:b.index('}', b.index("append('original_file'", start))]
    assert "append('original_file', selectedFile)" in branch and "append('item_ids'" not in branch
    assert b.index(CREATIVE_COND) < b.index('fetch(')


@pytest.mark.parametrize('fn', ['downloadOdt', 'downloadRtf', 'downloadPdf'])
def test_precise_condition_is_unchanged(fn):
    b = _body(fn)
    pre = ("if (isNormalizableFile(selectedFile)"
           " && Array.isArray(lastItemIds) && lastItemIds.length) {")
    assert pre in b and b.index(pre) < b.index(CREATIVE_COND)
    assert "append('item_ids', JSON.stringify(lastItemIds))" in b


def test_docx_button_already_sends_original_for_any_docx_input():
    b = _body('downloadImproved')
    assert "formData.append('original_file', selectedFile);" in b
    assert "isNormalizableFile(selectedFile)" in b          # N1b: было endsWith('.docx')
    assert "if (lastItemIds) {" in b            # item_ids только когда они есть (в Creative их нет)


def test_inline_scripts_pass_node_check():
    node = shutil.which('node')
    if not node:
        pytest.skip('node не установлен')
    scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', _html(), re.S)
    for i, code in enumerate(scripts):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(code)
        try:
            r = subprocess.run([node, '--check', f.name], capture_output=True, text=True)
            assert r.returncode == 0, 'script %d: %s' % (i, r.stderr[:300])
        finally:
            os.unlink(f.name)
