"""
Cycle P1: (1) ODT с правым таб-стопом не падает, tab-stops лежит внутри paragraph-properties;
(2) при повторяющемся styleId побеждает ПОСЛЕДНЕЕ определение (как в Word/LibreOffice) во всех
конвертерах (ODT/RTF/PDF); документы без дублей не меняются.
Без сети и без soffice.

Запуск: FLASK_ENV=testing python -m pytest tests/test_p1_odt_tabs_styles.py -q
"""
import io
import os
import re
import sys
import zipfile

import pytest
from lxml import etree

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

from docx import Document
from docx.oxml.ns import qn

from app.utils.docx_to_odt import docx_bytes_to_odt, _dedupe_styles
from app.utils.docx_to_rtf import docx_bytes_to_rtf
from app.utils.docx_to_pdf import docx_bytes_to_pdf

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'matrix')
STYLE_NS = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'
NEW = (0x1F, 0x4E, 0x79)   # 1F4E79 - последнее определение Heading1
OLD = (0x2E, 0x74, 0xB5)   # 2E74B5 - первое определение


def _bytes(x):
    return x.getvalue() if hasattr(x, 'getvalue') else x


def _read(name):
    with open(os.path.join(DATA, name), 'rb') as f:
        return f.read()


# ---------------- 1. ODT и таб-стопы ----------------

def test_tabdates_odt_no_exception_and_loads():
    from odf.opendocument import load
    odt = _bytes(docx_bytes_to_odt(_read('tabdates.docx')))
    assert zipfile.is_zipfile(io.BytesIO(odt))
    load(io.BytesIO(odt))  # валидный ODT читается odfpy


def test_tabdates_odt_has_right_tab_inside_paragraph_properties():
    odt = _bytes(docx_bytes_to_odt(_read('tabdates.docx')))
    z = zipfile.ZipFile(io.BytesIO(odt))
    found = 0
    for part in ('content.xml', 'styles.xml'):
        root = etree.fromstring(z.read(part))
        for ts in root.iter('{%s}tab-stops' % STYLE_NS):
            assert ts.getparent().tag == '{%s}paragraph-properties' % STYLE_NS
            for t in ts:
                if t.get('{%s}type' % STYLE_NS) == 'right':
                    found += 1
    assert found >= 1


def test_tabdates_route_does_not_fall_back_to_plain_odt(monkeypatch):
    """Конвертер сам не бросает исключение на документе с табами (раньше маршрут откатывался)."""
    docx_bytes_to_odt(_read('tabdates.docx'))


# ---------------- 2. дубли стилей ----------------

def _heading_ids(doc):
    ids = [s.get(qn('w:styleId')) for s in doc.styles.element.findall(qn('w:style'))]
    return [i for i in set(ids) if ids.count(i) > 1]


@pytest.mark.parametrize('name', ['designer', 'engineer'])
def test_corpus_really_has_duplicate_heading_styles(name):
    doc = Document(io.BytesIO(_read(name + '.docx')))
    assert 'Heading1' in _heading_ids(doc)


def test_dedupe_keeps_last_definition_and_is_in_memory_only():
    raw = _read('designer.docx')
    doc = _dedupe_styles(Document(io.BytesIO(raw)))
    assert _heading_ids(doc) == []
    h1 = doc.styles.element.get_by_id('Heading1')
    assert h1.find('.//' + qn('w:color')).get(qn('w:val')) == '1F4E79'
    # исходные байты не тронуты
    assert 'Heading1' in _heading_ids(Document(io.BytesIO(raw)))


@pytest.mark.parametrize('name', ['ua', 'tabdates', 'hebrew_rtl'])
def test_dedupe_is_noop_without_duplicates(name):
    doc = Document(io.BytesIO(_read(name + '.docx')))
    before = etree.tostring(doc.styles.element)
    _dedupe_styles(doc)
    assert etree.tostring(doc.styles.element) == before


def _odt_colors(odt):
    z = zipfile.ZipFile(io.BytesIO(odt))
    text = (z.read('content.xml') + z.read('styles.xml')).decode('utf-8').lower()
    return text


@pytest.mark.parametrize('name', ['designer', 'engineer'])
def test_heading_color_in_odt_is_last_definition(name):
    text = _odt_colors(_bytes(docx_bytes_to_odt(_read(name + '.docx'))))
    assert '#1f4e79' in text
    assert '#2e74b5' not in text


@pytest.mark.parametrize('name', ['designer', 'engineer'])
def test_heading_color_in_rtf_is_last_definition(name):
    rtf = _bytes(docx_bytes_to_rtf(_read(name + '.docx')))
    rtf = rtf.decode('latin-1') if isinstance(rtf, bytes) else rtf
    table = re.search(r'\{\\colortbl;(.*?)\}', rtf, re.S).group(1)
    cols = [tuple(int(x) for x in m) for m in re.findall(r'\\red(\d+)\\green(\d+)\\blue(\d+)', table)]
    assert NEW in cols
    assert OLD not in cols


def _pdf_char_colors(pdf):
    pytest.importorskip('pdfminer', reason='pdfminer.six нужен только для проверки цвета в PDF (requirements-dev.txt)')
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTChar, LTTextContainer
    out = set()

    def walk(o):
        if isinstance(o, LTChar):
            col = o.graphicstate.ncolor
            if isinstance(col, (list, tuple)) and len(col) == 3:
                out.add(tuple(round(c * 255) for c in col))
        elif hasattr(o, '__iter__'):
            for c in o:
                walk(c)
    for page in extract_pages(io.BytesIO(pdf)):
        walk(page)
    return out


@pytest.mark.parametrize('name', ['designer', 'engineer'])
def test_heading_color_in_pdf_is_last_definition(name):
    pdf = _bytes(docx_bytes_to_pdf(_read(name + '.docx')))
    cols = _pdf_char_colors(pdf)
    near = lambda c, t: all(abs(a - b) <= 2 for a, b in zip(c, t))
    assert any(near(c, NEW) for c in cols), cols
    assert not any(near(c, OLD) for c in cols), cols
