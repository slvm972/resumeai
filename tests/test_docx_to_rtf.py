"""
Cycle X3: конвертер DOCX -> RTF с сохранением оформления и маршрут /api/improve/rtf.
Без сети и без внешних программ. Запуск: FLASK_ENV=testing python -m pytest tests/test_docx_to_rtf.py -q
"""
import io
import os
import re
import sys
import json
import importlib
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

import config as config_module
importlib.reload(config_module)

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app import create_app
from app.utils.docx_to_rtf import docx_bytes_to_rtf, _esc

HEBREW = "מהנדס תוכנה בכיר"


def make_docx():
    d = Document()
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Anna Smith")
    r.font.size = Pt(26)
    r.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
    p = d.add_paragraph()
    r = p.add_run("EXPERIENCE")
    r.bold = True
    pPr = p._p.get_or_add_pPr()
    b = OxmlElement('w:pBdr')
    e = OxmlElement('w:bottom')
    for k, v in (('val', 'single'), ('sz', '8'), ('space', '1'), ('color', '1F3864')):
        e.set(qn('w:' + k), v)
    b.append(e)
    pPr.append(b)
    p = d.add_paragraph("Lead Engineer, Acme\t2020 - 2024")
    p.paragraph_format.tab_stops.add_tab_stop(Pt(400), WD_TAB_ALIGNMENT.RIGHT)
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Lead Engineer, Acme"
    t.cell(0, 1).text = "2020 - 2024"
    for s in ("Led a team of 5", "Built internal tools"):
        d.add_paragraph(s, style='List Bullet')
    t2 = d.add_table(rows=2, cols=2)
    t2.style = 'Table Grid'
    for i, (a, c) in enumerate((("Tools", "Python, SQL"), ("Languages", "Русский, Українська"))):
        t2.cell(i, 0).text = a
        t2.cell(i, 1).text = c
    p = d.add_paragraph()
    p._p.get_or_add_pPr().append(OxmlElement('w:bidi'))
    r = p.add_run(HEBREW)
    r._r.get_or_add_rPr().append(OxmlElement('w:rtl'))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture(scope='module')
def rtf():
    return docx_bytes_to_rtf(make_docx()).decode('ascii')


def test_starts_with_rtf1(rtf):
    assert rtf.startswith('{\\rtf1')
    assert '\\ansicpg1252' in rtf and '\\deff0' in rtf and '\\uc1' in rtf


def test_braces_balanced(rtf):
    clean = re.sub(r'\\[\\{}]', '', rtf)
    assert clean.count('{') == clean.count('}')
    depth = 0
    for ch in clean:
        depth += (ch == '{') - (ch == '}')
        assert depth >= 0
    assert depth == 0


def test_output_is_pure_ascii(rtf):
    rtf.encode('ascii')


def test_hebrew_is_unicode_escaped_and_rtl(rtf):
    assert '\\u1502?' in rtf      # מ
    assert '\\rtlch' in rtf and '\\rtlpar' in rtf
    assert HEBREW not in rtf


def test_cyrillic_and_ukrainian_escaped(rtf):
    assert '\\u1056?' in rtf      # Р
    assert '\\u1111?' in rtf      # ї (укр.)


def test_code_above_32767_is_negative():
    assert _esc('\ufb1d') == '\\u-1251?'


def test_surrogate_pair_for_astral_symbol():
    assert _esc('\U0001F600') == '\\u-10179?\\u-8704?'


def test_special_chars_escaped():
    assert _esc('a\\b{c}') == 'a\\\\b\\{c\\}'
    assert '\\tab ' in _esc('a\tb')


def test_tables_produce_trowd_and_cellx(rtf):
    assert rtf.count('\\trowd') == 3          # 1 строка + 2 строки
    assert rtf.count('\\row') >= 3
    assert '\\cellx' in rtf and '\\intbl' in rtf


def test_grid_table_has_cell_borders(rtf):
    assert '\\clbrdrt\\brdrs' in rtf


def test_right_tab_stop(rtf):
    assert re.search(r'\\tqr\\tx\d+', rtf)


def test_color_in_colortbl(rtf):
    assert '\\red31\\green56\\blue100;' in rtf
    assert '\\cf1' in rtf


def test_center_alignment_and_size(rtf):
    assert '\\qc' in rtf
    assert '\\fs52' in rtf


def test_bottom_border_under_heading(rtf):
    assert re.search(r'\\brdrb\\brdrs\\brdrw\d+\\brdrcf\d+', rtf)


def test_bullets_printed_as_labels(rtf):
    assert rtf.count('\\u8226?') >= 2


def test_gridspan_merged_cell_has_one_cellx():
    d = Document()
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).merge(t.cell(0, 1))
    t.cell(0, 0).text = "Merged"
    buf = io.BytesIO()
    d.save(buf)
    out = docx_bytes_to_rtf(buf.getvalue()).decode('ascii')
    assert out.count('\\cellx') == 1


def test_nested_table_does_not_crash():
    d = Document()
    t = d.add_table(rows=1, cols=1)
    t.cell(0, 0).add_table(rows=1, cols=1)
    d.add_paragraph("after")
    buf = io.BytesIO()
    d.save(buf)
    assert b'after' in docx_bytes_to_rtf(buf.getvalue())


# --- маршрут -----------------------------------------------------------------------------------

@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['admin'] = 'admin'
        yield c


def _marked(raw):
    from app.missing_routes4 import _extract_structured
    items = _extract_structured(Document(io.BytesIO(raw)))
    ids = [str(i + 1).zfill(3) for i in range(len(items))]
    text = "\n\n".join("###ITEM_%s:PLAIN###\n%s" % (ids[i], items[i]['text']) for i in range(len(items)))
    return text, ids


def _post(client, raw, with_file=True, ids=True, file_bytes=None, item_ids=None):
    text, item_ids_ok = _marked(raw)
    data = {'improved_resume': text}
    if with_file:
        data['original_file'] = (io.BytesIO(file_bytes if file_bytes is not None else raw), 'resume.docx')
    if ids:
        data['item_ids'] = item_ids if item_ids is not None else json.dumps(item_ids_ok)
    return client.post('/api/improve/rtf', data=data, content_type='multipart/form-data')


def test_route_with_original_file_keeps_tables(client):
    r = _post(client, make_docx())
    assert r.status_code == 200
    assert r.mimetype == 'application/rtf'
    body = r.data.decode('ascii')
    assert body.startswith('{\\rtf1') and '\\trowd' in body and '###ITEM' not in body


def test_route_without_original_uses_old_path(client):
    r = _post(client, make_docx(), with_file=False)
    assert r.status_code == 200
    assert b'\\trowd' not in r.data


def test_route_broken_docx_falls_back_with_200(client):
    r = _post(client, make_docx(), file_bytes=b'this is not a docx at all')
    assert r.status_code == 200
    assert r.data.startswith(b'{\\rtf1') and b'\\trowd' not in r.data
    assert b'Traceback' not in r.data and b'BadZipFile' not in r.data


def test_route_bad_item_ids_falls_back_with_200(client):
    r = _post(client, make_docx(), item_ids='{not json')
    assert r.status_code == 200
    assert b'JSON' not in r.data and b'Expecting' not in r.data


def test_route_converter_failure_falls_back_with_200(client):
    with patch('app.utils.docx_to_rtf.docx_bytes_to_rtf', side_effect=RuntimeError('boom /secret/path')):
        r = _post(client, make_docx())
    assert r.status_code == 200
    assert b'secret' not in r.data and r.data.startswith(b'{\\rtf1')


# --- Creative (X2b): item_ids пуст/отсутствует -> улучшенный текст не должен теряться -----------

@pytest.mark.parametrize('ids_value', [None, '[]'])
def test_route_creative_empty_item_ids_keeps_improved_text(client, ids_value):
    raw = make_docx()
    text, _ = _marked(raw)
    data = {'improved_resume': text.replace('Lead Engineer, Acme', 'Principal Engineer, Acme'),
            'original_file': (io.BytesIO(raw), 'resume.docx')}
    if ids_value is not None:
        data['item_ids'] = ids_value
    r = client.post('/api/improve/rtf', data=data, content_type='multipart/form-data')
    assert r.status_code == 200
    body = r.data.decode('ascii')
    assert 'Principal Engineer, Acme' in body and 'Lead Engineer, Acme' not in body


def test_route_non_list_item_ids_uses_text_path(client):
    raw = make_docx()
    text, _ = _marked(raw)
    r = client.post('/api/improve/rtf', data={
        'improved_resume': text.replace('Lead Engineer, Acme', 'Principal Engineer, Acme'),
        'item_ids': '{"a": 1}', 'original_file': (io.BytesIO(raw), 'resume.docx')},
        content_type='multipart/form-data')
    assert r.status_code == 200
    assert 'Principal Engineer, Acme' in r.data.decode('ascii')
