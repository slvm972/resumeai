"""
Cycle P2: (1) шрифт по умолчанию: без rFonts/темы это Times New Roman (serif), с темой шрифт темы;
(2) PDF, иврит: маркеры списка справа, таблица bidiVisual зеркалится (даты слева);
(3) Creative-сборщик опознаёт заголовок по оформлению образца (линия/жирный крупный), не только по словарю.
Без сети и без soffice; тесты с PDF-разбором пропускаются, если нет pdfminer.

Запуск: FLASK_ENV=testing python -m pytest tests/test_p2_font_rtl_pdf_heading.py -q
"""
import io
import os
import re
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

from docx import Document

from app.utils.docx_to_odt import docx_bytes_to_odt, _Converter
from app.utils.docx_to_rtf import docx_bytes_to_rtf
from app.utils.docx_to_pdf import docx_bytes_to_pdf
from app.utils import creative_docx_builder as cdb

DATA = os.path.join(ROOT, 'tests', 'data', 'matrix')


def _raw(name):
    with open(os.path.join(DATA, name + '.docx'), 'rb') as f:
        return f.read()


def _b(x):
    return x.getvalue() if hasattr(x, 'getvalue') else x


# ---------------- 1. шрифт по умолчанию ----------------

def test_default_font_times_when_nothing_defined():
    assert _Converter(_raw('ua'))._default_font == 'Times New Roman'


@pytest.mark.parametrize('name', ['tabdates', 'hebrew_rtl'])
def test_default_font_is_theme_font_when_theme_exists(name):
    c = _Converter(_raw(name))
    assert c._theme.get('minor')
    assert c._default_font == c._theme['minor']


def test_ua_odt_is_serif_times():
    z = zipfile.ZipFile(io.BytesIO(_b(docx_bytes_to_odt(_raw('ua')))))
    c = (z.read('content.xml') + z.read('styles.xml')).decode('utf-8')
    assert 'Times New Roman' in c and 'Calibri' not in c
    assert 'style:font-family-generic="roman"' in c


def test_ua_rtf_is_roman_times():
    rtf = _b(docx_bytes_to_rtf(_raw('ua'))).decode('latin-1')
    assert re.search(r'\\froman\\fcharset0 Times New Roman;', rtf) and 'Calibri' not in rtf


def _pdf_fonts(pdf):
    pytest.importorskip('pdfminer')
    from pdfminer.high_level import extract_pages
    fs = set()

    def walk(o):
        if hasattr(o, 'fontname'):
            fs.add(o.fontname)
        if hasattr(o, '__iter__'):
            for c in o:
                walk(c)
    for page in extract_pages(io.BytesIO(pdf)):
        walk(page)
    return fs


def test_ua_pdf_uses_serif_font():
    fs = _pdf_fonts(_b(docx_bytes_to_pdf(_raw('ua'))))
    assert fs and all('Serif' in f for f in fs), fs


@pytest.mark.parametrize('name,font', [('designer', 'Arial'), ('engineer', 'Arial')])
def test_files_with_explicit_font_unchanged(name, font):
    z = zipfile.ZipFile(io.BytesIO(_b(docx_bytes_to_odt(_raw(name)))))
    c = (z.read('content.xml') + z.read('styles.xml')).decode('utf-8')
    assert font in c and 'Times New Roman' not in c
    assert not any('Serif' in f for f in _pdf_fonts(_b(docx_bytes_to_pdf(_raw(name)))))


# ---------------- 2. PDF иврит ----------------

def _pdf_lines(pdf):
    pytest.importorskip('pdfminer')
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextLine, LTChar
    out = []
    pw = None

    def walk(o):
        if isinstance(o, LTTextLine):
            out.append(o)
        elif hasattr(o, '__iter__') and not isinstance(o, LTChar):
            for c in o:
                walk(c)
    for page in extract_pages(io.BytesIO(pdf)):
        pw = page.width
        walk(page)
    return pw, out


def test_hebrew_pdf_has_bullets_on_right_side():
    pw, lines = _pdf_lines(_b(docx_bytes_to_pdf(_raw('hebrew_rtl'))))
    bullets = [l for l in lines if l.get_text().strip() == '\u2022']
    assert len(bullets) >= 3, [l.get_text() for l in lines][:20]
    assert all(b.x0 > pw / 2 for b in bullets)


def test_hebrew_pdf_dates_in_table_on_left_side():
    pw, lines = _pdf_lines(_b(docx_bytes_to_pdf(_raw('hebrew_rtl'))))
    dates = [l for l in lines if re.search(r'(19|20)\d\d\s*-\s*(19|20)\d\d', l.get_text())]
    assert len(dates) >= 3
    assert all(d.x1 < pw / 2 for d in dates), [(d.get_text(), d.x1, pw) for d in dates]


def test_ltr_pdf_unchanged_dates_on_right_and_no_mirroring():
    pw, lines = _pdf_lines(_b(docx_bytes_to_pdf(_raw('designer'))))
    dates = [l for l in lines if re.search(r'(19|20)\d\d\s*[-\u2013]\s*(19|20)\d\d|Present', l.get_text())]
    if dates:
        assert all(d.x0 > pw / 2 for d in dates)


# ---------------- 3. Creative: заголовок по оформлению ----------------

def _roles(raw):
    _, rep = cdb._analyze(Document(io.BytesIO(raw)))
    return [r[2] for r in rep]


def _with_headings_renamed(raw, mapping):
    doc = Document(io.BytesIO(raw))
    for p in doc.paragraphs:
        if p.text.strip() in mapping and p.runs:
            p.runs[0].text = mapping[p.text.strip()]
            for r in p.runs[1:]:
                r.text = ''
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_hebrew_headings_recognized():
    assert _roles(_raw('hebrew_rtl')).count('HEADING') == 4


def test_unlisted_hebrew_headings_recognized_by_formatting():
    """Слова нет в словаре, но у образца есть линия и жирный крупный шрифт."""
    raw = _with_headings_renamed(_raw('hebrew_rtl'), {
        '\u05ea\u05e7\u05e6\u05d9\u05e8': '\u05d1\u05e7\u05e6\u05e8\u05d4 \u05db\u05dc\u05dc\u05d9\u05ea',
        '\u05d4\u05e9\u05db\u05dc\u05d4': '\u05dc\u05d9\u05de\u05d5\u05d3\u05d9\u05dd \u05d0\u05e7\u05d3\u05de\u05d9\u05d9\u05dd',
    })
    assert cdb._HEADINGS.isdisjoint({'\u05d1\u05e7\u05e6\u05e8\u05d4 \u05db\u05dc\u05dc\u05d9\u05ea'})
    assert _roles(raw).count('HEADING') == 4


def test_short_bold_same_size_line_is_not_heading():
    doc = Document()
    doc.add_paragraph('Jane Roe')
    doc.add_paragraph('jane@example.com | +1 415 555 0100 | SF')
    p = doc.add_paragraph()
    r = p.add_run('Team Lead')
    r.bold = True
    doc.add_paragraph('2020 - 2023')
    doc.add_paragraph('Built many things for many users across the world in a long sentence here.')
    buf = io.BytesIO()
    doc.save(buf)
    assert 'HEADING' not in _roles(buf.getvalue())


@pytest.mark.parametrize('name', ['ua', 'designer', 'engineer', 'tabdates'])
def test_ltr_corpus_roles_not_all_body(name):
    roles = _roles(_raw(name))
    assert 'HEADING' in roles
