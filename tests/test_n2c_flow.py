"""
Cycle N2c: страница шлёт в /api/improve ФАЙЛ для всех форматов из NORMALIZABLE_EXTS (docx, txt, odt), а не только для
docx и txt; харнесс матрицы повторяет это условие. Сквозной сценарий Precise и Creative для ODT в том виде, как его
выполняет страница: загрузка, анализ (извлечение текста), улучшение из файла, экспорт с original_file и item_ids.
Без сети (Groq подменён), пути от __file__. Запуск: FLASK_ENV=testing python -m pytest tests/test_n2c_flow.py -q
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

from app import create_app
from app.missing_routes4 import _extract_structured
from app.utils.normalize_input import normalize_to_docx
from app.utils.resume_text import extract_resume_text
from app.utils.creative_docx_builder import build_creative_docx

DATA = os.path.join(ROOT, 'tests', 'data', 'matrix')
INDEX = os.path.join(ROOT, 'index.html')
TOOLS = os.path.join(ROOT, 'tools')
ROUTES = ['docx', 'odt', 'rtf', 'pdf']
OLD_WORD, NEW_WORD = 'Led redesign', 'Directed redesign'


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def _html():
    with open(INDEX, encoding='utf-8', newline='') as f:
        return f.read().replace('\r\n', '\n')


def _node():
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден')
    return node


def _run_node(script):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tmp:
        tmp.write(script)
    try:
        res = subprocess.run([_node(), tmp.name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(tmp.name)
    assert res.returncode == 0, res.stderr[:300]
    return json.loads(res.stdout)


def _page_functions():
    h = _html()
    return re.search(r"const NORMALIZABLE_EXTS = .*?;\n\s*function isNormalizableFile\(f\) \{.*?\n\s*\}\n\s*"
                     r"(?:\s*//[^\n]*\n)*\s*function canImproveFromFile\(f\) \{.*?\n\s*\}\n", h, re.S).group(0)


# ---------------- страница ----------------

def test_can_improve_from_file_has_no_second_extension_list():
    h = _html()
    m = re.search(r"function canImproveFromFile\(f\) \{(.*?)\n\s*\}", h, re.S)
    assert m and m.group(1).strip() == 'return isNormalizableFile(f);'
    assert '(docx|txt)' not in h


def test_can_improve_equals_is_normalizable_for_many_names_in_node():
    names = ['a.docx', 'A.DOCX', 'a.txt', 'a.odt', 'A.ODT', 'a.rtf', 'a.pdf', 'a.doc', 'a.odt.pdf', 'a.pdf.odt', 'odt', '',
             'my resume.Odt', 'a.docx.rtf']
    script = _page_functions() + '\nconst names = %s;\nconst out = {};\nfor (const n of names) out[n] = [canImproveFromFile({name: n}), ' \
                                 'isNormalizableFile({name: n})];\nout.__null = [canImproveFromFile(null), isNormalizableFile(null)];\n' \
                                 'console.log(JSON.stringify(out));\n' % json.dumps(names)
    out = _run_node(script)
    for k, (a, b) in out.items():
        assert a == b, k
    assert out['a.odt'][0] is True and out['A.ODT'][0] is True and out['my resume.Odt'][0] is True
    assert out['a.docx'][0] is True and out['a.txt'][0] is True
    assert out['a.rtf'][0] is False and out['a.pdf'][0] is False and out['a.doc'][0] is False
    assert out['__null'] == [False, False]


def test_page_improve_branch_and_export_use_the_same_condition():
    h = _html()
    start = h.index('async function improveResume')
    body = h[start:start + 3500]
    assert 'if (canImproveFromFile(selectedFile)) {' in body
    # экспорт: оригинал только для тех же форматов
    assert h.count('isNormalizableFile(selectedFile) && Array.isArray(lastItemIds)') >= 4


def test_analyze_text_for_odt_is_real_text_not_raw_bytes():
    """Страница берёт lastResumeText из /api/analyze (extract_resume_text): для ODT это настоящий текст."""
    raw = _read(os.path.join(DATA, 'designer.odt'))
    text = extract_resume_text('designer.odt', raw)
    assert 'Sarah Mitchell' in text and 'PK' not in text[:20]


# ---------------- сквозной сценарий (как на странице) ----------------

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


def _page_improve_request(client, raw, filename, mode='precise'):
    """Ровно то, что делает improveResume(): файл при canImproveFromFile, иначе JSON с текстом анализа."""
    can = filename.rsplit('.', 1)[-1].lower() in ('docx', 'txt', 'odt')        # значение canImproveFromFile после N2c
    with patch('requests.post', side_effect=_fake_groq):
        if can:
            return client.post('/api/improve', data={'file': (io.BytesIO(raw), filename), 'creativity_mode': mode},
                               content_type='multipart/form-data')
        return client.post('/api/improve', json={'resume_text': extract_resume_text(filename.lower(), raw), 'analysis': {},
                                                 'original_format': 'txt', 'creativity_mode': mode})


def _export(client, route, text, original, filename, item_ids=None):
    data = {'improved_resume': text, 'original_file': (io.BytesIO(original), filename)}
    if item_ids:
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
        return body.decode('ascii')
    pypdf = pytest.importorskip('PyPDF2')
    return '\n'.join(p.extract_text() or '' for p in pypdf.PdfReader(io.BytesIO(body)).pages)


def _has_layout(route, body, creative=False):
    """Оформление из исходного ODT: центрированное имя и линия под заголовком (DOCX и ODT), \\qc (RTF)."""
    if route == 'docx':
        d = Document(io.BytesIO(body))
        name_ok = int(d.paragraphs[0].alignment or 0) == 1 and d.paragraphs[0].runs[0].font.size.pt >= 20
        if creative:        # Creative собирает текст по образцам: линии под заголовками (отдельные пустые абзацы) не воспроизводятся
            return name_ok
        return name_ok and any(p._p.pPr is not None and p._p.pPr.find(qn('w:pBdr')) is not None for p in d.paragraphs)
    if route == 'odt':
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            c = z.read('content.xml') + z.read('styles.xml')
        return b'fo:text-align="center"' in c and b'fo:border-bottom' in c
    if route == 'rtf':
        return b'\\qc' in body
    return b'FiraSans-Bold' in body or b'LiberationSerif' in body


@pytest.mark.parametrize('route', ROUTES)
def test_precise_flow_odt_file_improve_then_export_with_layout_and_improved_word(client, route):
    raw = _read(os.path.join(DATA, 'designer.odt'))
    r = _page_improve_request(client, raw, 'designer.odt')
    assert r.status_code == 200
    body = r.get_json()
    assert body['has_original_docx'] is True and body['original_format'] == 'odt'
    ids = body['item_ids']
    assert ids, 'страница получила бы пустой item_ids и не включила Precise-экспорт с оформлением'
    items = _extract_structured(Document(io.BytesIO(normalize_to_docx(raw, 'designer.odt'))))
    assert len(ids) == len(items)
    exp = _export(client, route, body['improved_resume'], raw, 'designer.odt', ids)
    assert exp.status_code == 200
    assert _flat(NEW_WORD) in _flat(_text(route, exp.data)) and _flat(OLD_WORD) not in _flat(_text(route, exp.data))
    assert _has_layout(route, exp.data)


def test_text_path_item_ids_would_not_match_the_file_and_misplace_text(client):
    """Почему страница обязана слать файл: item_ids из JSON-текста считаются по строкам текста, а экспорт применяет их
    к абзацам нормализованного DOCX. Они расходятся, и улучшенный текст ложится не на свои места."""
    raw = _read(os.path.join(DATA, 'designer.odt'))
    with patch('requests.post', side_effect=_fake_groq):
        r = client.post('/api/improve', json={'resume_text': extract_resume_text('designer.odt', raw), 'analysis': {},
                                              'original_format': 'txt', 'creativity_mode': 'precise'})
    ids_text = r.get_json()['item_ids']
    items = _extract_structured(Document(io.BytesIO(normalize_to_docx(raw, 'designer.odt'))))
    assert len(ids_text) != len(items) or r.get_json().get('has_original_docx') is False


@pytest.mark.parametrize('route', ROUTES)
def test_creative_flow_odt_export_builds_from_original_layout(client, route):
    """Creative: /api/improve возвращает engine creative_v2 и пустой item_ids, страница шлёт только original_file."""
    raw = _read(os.path.join(DATA, 'designer.odt'))
    seen = {}

    def fake_creative(original_bytes, filename, text, cfg):
        seen.update(n=filename)
        norm_items = _extract_structured(Document(io.BytesIO(original_bytes)))
        t = '\n'.join(i['text'] for i in norm_items).replace(OLD_WORD, NEW_WORD)
        return {'attempted': True, 'acceptable': True, 'status': 'ok', 'improved_resume': t, 'display_text': t,
                'detected_language': 'English', 'rounds': 1, 'calls': 1}
    client.application.config['CREATIVE_V2_ENABLED'] = True
    with patch('app.creative_bridge.creative_improve', side_effect=fake_creative):
        r = client.post('/api/improve', data={'file': (io.BytesIO(raw), 'designer.odt'), 'creativity_mode': 'creative'},
                        content_type='multipart/form-data')
    body = r.get_json()
    assert r.status_code == 200 and body['engine'] == 'creative_v2' and seen['n'] == 'resume.docx'
    assert not body.get('item_ids')
    exp = _export(client, route, body['improved_resume'], raw, 'designer.odt')
    assert exp.status_code == 200
    assert _flat(NEW_WORD) in _flat(_text(route, exp.data))
    assert _has_layout(route, exp.data, creative=True)
    if route == 'docx':
        direct = build_creative_docx(normalize_to_docx(raw, 'designer.odt'), body['improved_resume'])
        assert zipfile.ZipFile(io.BytesIO(exp.data)).read('word/document.xml') == \
            zipfile.ZipFile(io.BytesIO(direct)).read('word/document.xml')


# ---------------- харнесс матрицы повторяет страницу ----------------

@pytest.fixture(scope='module')
def fm():
    pytest.importorskip('pdfminer')
    sys.path.insert(0, TOOLS)
    import format_matrix
    return format_matrix


def test_harness_reads_can_improve_exts_from_current_page(fm):
    assert fm.page_can_improve_exts() == {'docx', 'txt', 'odt'}
    assert fm.page_can_improve_exts() == fm.page_normalizable_exts()


def test_harness_reads_old_regex_condition_from_old_page(fm, tmp_path, monkeypatch):
    old = ("const NORMALIZABLE_EXTS = ['docx', 'txt', 'odt'];\nfunction canImproveFromFile(f) {\n"
           "    return !!f && /\\.(docx|txt)$/i.test(f.name || '');\n  }\n")
    (tmp_path / 'index.html').write_text(old, encoding='utf-8')
    monkeypatch.setattr(fm, 'ROOT', str(tmp_path))
    assert fm.page_can_improve_exts() == {'docx', 'txt'}
    assert fm.page_normalizable_exts() == {'docx', 'txt', 'odt'}


def test_harness_sends_file_or_json_text_by_page_condition(fm, monkeypatch):
    calls = []

    class _R:
        status_code = 500

        @staticmethod
        def get_json(silent=True):
            return {}

    class _C:
        def post(self, url, **kw):
            calls.append(kw)
            return _R()
    monkeypatch.setattr(fm, 'page_can_improve_exts', lambda: {'docx', 'txt', 'odt'})
    fm.precise_generate(_C(), 'designer', 'odt')
    assert 'data' in calls[-1] and 'json' not in calls[-1] and 'file' in calls[-1]['data']
    monkeypatch.setattr(fm, 'page_can_improve_exts', lambda: {'docx', 'txt'})
    fm.precise_generate(_C(), 'designer', 'odt')
    assert 'json' in calls[-1] and 'data' not in calls[-1]
    assert 'Sarah Mitchell' in calls[-1]['json']['resume_text'] and calls[-1]['json']['original_format'] == 'txt'
    fm.precise_generate(_C(), 'designer', 'rtf')
    assert 'json' in calls[-1]
