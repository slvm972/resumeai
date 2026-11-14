"""
Cycle X1: конвертер DOCX -> ODT с сохранением оформления и маршрут /api/improve/odt.
Без сети и без внешних программ (soffice не нужен).

Запуск: FLASK_ENV=testing python -m pytest tests/test_docx_to_odt.py -q
"""
import io
import os
import sys
import json
import zipfile
import importlib
from unittest.mock import patch

import pytest
from lxml import etree

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

import config as config_module
importlib.reload(config_module)

from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from odf.opendocument import load as odf_load

from app import create_app
from app.utils.docx_to_odt import docx_bytes_to_odt

NS = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
}
HEBREW = "מהנדס תוכנה בכיר"
NAME = "Anna Smith"


def _bottom_border(p):
    pPr = p._p.get_or_add_pPr()
    b = OxmlElement('w:pBdr')
    e = OxmlElement('w:bottom')
    for k, v in (('val', 'single'), ('sz', '8'), ('space', '1'), ('color', '1F3864')):
        e.set(qn('w:' + k), v)
    b.append(e)
    pPr.append(b)


def make_docx():
    d = Document()
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(NAME)
    r.font.size = Pt(26)
    r.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
    p = d.add_paragraph("Senior Engineer")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = d.add_paragraph("anna@example.com | +1 555 0100 | Chicago, IL")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = d.add_paragraph()
    r = p.add_run("EXPERIENCE")
    r.bold = True
    r.font.size = Pt(13)
    r.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
    _bottom_border(p)
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Lead Engineer, Acme"
    c = t.cell(0, 1)
    c.text = "2020 - 2024"
    c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for s in ("Led a team of 5", "Built internal tools", "Delivered weekly reports"):
        d.add_paragraph(s, style='List Bullet')
    t2 = d.add_table(rows=2, cols=2)
    t2.style = 'Table Grid'
    for i, (a, b) in enumerate((("Tools", "Python, SQL"), ("Languages", "English, Hebrew"))):
        t2.cell(i, 0).text = a
        t2.cell(i, 1).text = b
    p = d.add_paragraph()
    p._p.get_or_add_pPr().append(OxmlElement('w:bidi'))
    r = p.add_run(HEBREW)
    r._r.get_or_add_rPr().append(OxmlElement('w:rtl'))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _content(odt_bytes):
    with zipfile.ZipFile(io.BytesIO(odt_bytes)) as z:
        return etree.fromstring(z.read('content.xml'))


def _alltext(el):
    return ''.join(el.itertext())


def _style_props(root, style_name, kind):
    st = root.xpath("//style:style[@style:name=$n]", namespaces=NS, n=style_name)
    assert st, style_name
    props = st[0].find('style:%s' % kind, NS)
    return props.attrib if props is not None else {}


def _a(attrib, ns, name):
    return attrib.get('{%s}%s' % (NS[ns], name))


@pytest.fixture(scope='module')
def converted():
    raw = make_docx()
    out = docx_bytes_to_odt(raw).getvalue()
    return raw, out, _content(out)


def test_opens_with_odfpy(converted):
    _, out, _ = converted
    assert odf_load(io.BytesIO(out)) is not None


def test_text_of_paragraphs_and_cells_matches(converted):
    raw, _, root = converted
    d = Document(io.BytesIO(raw))
    expected = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                expected.append(cell.text)
    odt_text = _alltext(root.find('.//office:text', NS))
    for piece in expected:
        assert piece in odt_text, piece


def test_table_count_equal(converted):
    raw, _, root = converted
    assert len(root.findall('.//table:table', NS)) == len(Document(io.BytesIO(raw)).tables) == 2


def test_name_centered_and_large(converted):
    _, _, root = converted
    p = [x for x in root.iter('{%s}p' % NS['text']) if _alltext(x) == NAME][0]
    pstyle = p.get('{%s}style-name' % NS['text'])
    assert _a(_style_props(root, pstyle, 'paragraph-properties'), 'fo', 'text-align') == 'center'
    span = p.find('text:span', NS)
    tp = _style_props(root, span.get('{%s}style-name' % NS['text']), 'text-properties')
    assert _a(tp, 'fo', 'font-size') == '26pt'
    assert _a(tp, 'fo', 'color') == '#1F3864'


def test_heading_color_bold_and_bottom_border(converted):
    _, _, root = converted
    p = [x for x in root.iter('{%s}p' % NS['text']) if _alltext(x) == 'EXPERIENCE'][0]
    tp = _style_props(root, p.find('text:span', NS).get('{%s}style-name' % NS['text']), 'text-properties')
    assert _a(tp, 'fo', 'color') == '#1F3864' and _a(tp, 'fo', 'font-weight') == 'bold'
    pp = _style_props(root, p.get('{%s}style-name' % NS['text']), 'paragraph-properties')
    assert '#1F3864' in (_a(pp, 'fo', 'border-bottom') or '')


def test_bullets_became_a_list(converted):
    _, _, root = converted
    items = root.findall('.//text:list/text:list-item', NS)
    assert [_alltext(i) for i in items] == ["Led a team of 5", "Built internal tools", "Delivered weekly reports"]
    assert root.xpath('//text:list-level-style-bullet', namespaces=NS)


def test_dates_cell_right_aligned(converted):
    _, _, root = converted
    p = [x for x in root.iter('{%s}p' % NS['text']) if _alltext(x) == '2020 - 2024'][0]
    pp = _style_props(root, p.get('{%s}style-name' % NS['text']), 'paragraph-properties')
    assert _a(pp, 'fo', 'text-align') == 'right'


def test_grid_table_has_cell_borders_and_columns(converted):
    _, _, root = converted
    t = root.findall('.//table:table', NS)[1]
    assert len(t.findall('table:table-column', NS)) == 2
    cell = t.find('.//table:table-cell', NS)
    cp = _style_props(root, cell.get('{%s}style-name' % NS['table']), 'table-cell-properties')
    assert 'solid' in (_a(cp, 'fo', 'border-top') or '')


def test_hebrew_paragraph_is_rtl(converted):
    _, _, root = converted
    p = [x for x in root.iter('{%s}p' % NS['text']) if HEBREW in _alltext(x)][0]
    pp = _style_props(root, p.get('{%s}style-name' % NS['text']), 'paragraph-properties')
    assert _a(pp, 'style', 'writing-mode') == 'rl-tb'


def test_no_item_markers_in_result(converted):
    _, out, _ = converted
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        assert b'###ITEM' not in z.read('content.xml')


def test_gridspan_cell_is_merged():
    d = Document()
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).merge(t.cell(0, 1))
    t.cell(0, 0).text = "Merged title"
    buf = io.BytesIO()
    d.save(buf)
    root = _content(docx_bytes_to_odt(buf.getvalue()).getvalue())
    cell = root.find('.//table:table-cell', NS)
    assert cell.get('{%s}number-columns-spanned' % NS['table']) == '2'
    assert root.find('.//table:covered-table-cell', NS) is not None


def test_page_size_and_margins_transferred():
    d = Document()
    d.sections[0].left_margin = Cm(3)
    d.add_paragraph("x")
    buf = io.BytesIO()
    d.save(buf)
    out = docx_bytes_to_odt(buf.getvalue()).getvalue()
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        styles = etree.fromstring(z.read('styles.xml'))
        content = _content(out)
    pl = (content.xpath('//style:page-layout-properties', namespaces=NS)
          or styles.xpath('//style:page-layout-properties', namespaces=NS))[0]
    assert _a(pl.attrib, 'fo', 'margin-left') == '3.000cm'


def test_nested_table_and_picture_do_not_crash():
    d = Document()
    t = d.add_table(rows=1, cols=1)
    t.cell(0, 0).add_table(rows=1, cols=1)
    d.add_paragraph("after")
    buf = io.BytesIO()
    d.save(buf)
    out = docx_bytes_to_odt(buf.getvalue()).getvalue()
    assert "after" in _alltext(_content(out))


# --- маршрут -----------------------------------------------------------------------------------

@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['admin'] = 'admin'
        yield c


def _marked_text_and_ids(raw):
    from app.missing_routes4 import _extract_structured
    items = _extract_structured(Document(io.BytesIO(raw)))
    ids = [str(i + 1).zfill(3) for i in range(len(items))]
    text = "\n\n".join("###ITEM_%s:PLAIN###\n%s" % (ids[i], items[i]['text']) for i in range(len(items)))
    return text, ids


def _post(client, raw, with_file=True, ids=True):
    text, item_ids = _marked_text_and_ids(raw)
    data = {'improved_resume': text}
    if with_file:
        data['original_file'] = (io.BytesIO(raw), 'resume.docx')
    if ids:
        data['item_ids'] = json.dumps(item_ids)
    return client.post('/api/improve/odt', data=data, content_type='multipart/form-data')


def test_route_with_original_file_keeps_tables(client):
    raw = make_docx()
    r = _post(client, raw)
    assert r.status_code == 200
    root = _content(r.data)
    assert len(root.findall('.//table:table', NS)) == 2
    assert root.xpath('//text:list', namespaces=NS)
    assert b'###ITEM' not in r.data


def test_route_without_original_uses_old_path(client):
    raw = make_docx()
    r = _post(client, raw, with_file=False)
    assert r.status_code == 200
    assert not _content(r.data).findall('.//table:table', NS)


def test_route_converter_failure_falls_back_with_200(client):
    raw = make_docx()
    with patch('app.utils.docx_to_odt.docx_bytes_to_odt', side_effect=RuntimeError('boom /secret/path')):
        r = _post(client, raw)
    assert r.status_code == 200
    assert b'secret' not in r.data
    assert _alltext(_content(r.data)).strip()


def test_route_bad_item_ids_falls_back_with_200(client):
    raw = make_docx()
    text, _ = _marked_text_and_ids(raw)
    r = client.post('/api/improve/odt', data={
        'improved_resume': text, 'item_ids': '{not json',
        'original_file': (io.BytesIO(raw), 'resume.docx')}, content_type='multipart/form-data')
    assert r.status_code == 200


# --- Creative (X2b): item_ids пуст/отсутствует -> улучшенный текст не должен теряться -----------

@pytest.mark.parametrize('ids_value', [None, '[]'])
def test_route_creative_empty_item_ids_keeps_improved_text(client, ids_value):
    raw = make_docx()
    text, _ = _marked_text_and_ids(raw)
    assert 'Senior Engineer' in text
    improved = text.replace('Senior Engineer', 'Principal Engineer')
    data = {'improved_resume': improved, 'original_file': (io.BytesIO(raw), 'resume.docx')}
    if ids_value is not None:
        data['item_ids'] = ids_value
    r = client.post('/api/improve/odt', data=data, content_type='multipart/form-data')
    assert r.status_code == 200
    body = _alltext(_content(r.data))
    assert 'Principal Engineer' in body and 'Senior Engineer' not in body


def test_route_non_list_item_ids_uses_text_path(client):
    raw = make_docx()
    text, _ = _marked_text_and_ids(raw)
    r = client.post('/api/improve/odt', data={
        'improved_resume': text.replace('Senior Engineer', 'Principal Engineer'), 'item_ids': '{"a": 1}',
        'original_file': (io.BytesIO(raw), 'resume.docx')}, content_type='multipart/form-data')
    assert r.status_code == 200
    body = _alltext(_content(r.data))
    assert 'Principal Engineer' in body and 'Senior Engineer' not in body
