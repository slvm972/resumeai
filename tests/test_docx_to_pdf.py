"""
Cycle X4: конвертер DOCX -> PDF с сохранением оформления, маршрут /api/improve/pdf и кнопка PDF.
Без сети и без внешних программ. Запуск: FLASK_ENV=testing python -m pytest tests/test_docx_to_pdf.py -q
"""
import io
import os
import re
import sys
import json
import shutil
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
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app import create_app
from app.utils.docx_to_pdf import docx_bytes_to_pdf

HEBREW = "מהנדס תוכנה בכיר"
CYR = "Руководитель проекта, Українська мова"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _border(p, side='bottom'):
    pPr = p._p.get_or_add_pPr()
    b = OxmlElement('w:pBdr')
    e = OxmlElement('w:' + side)
    for k, v in (('val', 'single'), ('sz', '8'), ('space', '1'), ('color', '1F3864')):
        e.set(qn('w:' + k), v)
    b.append(e)
    pPr.append(b)


def make_docx(border=True, extra=0):
    d = Document()
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Anna Smith")
    r.font.size = Pt(26)
    r.bold = True
    r.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
    p = d.add_paragraph()
    r = p.add_run("EXPERIENCE")
    r.bold = True
    r.italic = True
    if border:
        _border(p)
    p = d.add_paragraph("Lead Engineer, Acme\t2020 - 2024")
    p.paragraph_format.tab_stops.add_tab_stop(Pt(450), WD_TAB_ALIGNMENT.RIGHT)
    d.add_paragraph(CYR)
    for s in ("Led a team of 5", "Built internal tools"):
        d.add_paragraph(s, style='List Bullet')
    t = d.add_table(rows=2, cols=2)
    t.style = 'Table Grid'
    for i, (a, c) in enumerate((("Tools", "Python, SQL"), ("Languages", "English, Hebrew"))):
        t.cell(i, 0).text = a
        t.cell(i, 1).text = c
    p = d.add_paragraph()
    p._p.get_or_add_pPr().append(OxmlElement('w:bidi'))
    r = p.add_run(HEBREW)
    r._r.get_or_add_rPr().append(OxmlElement('w:rtl'))
    for i in range(extra):
        d.add_paragraph("Filler paragraph number %d with some text to take space." % i)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _reader(pdf):
    return PyPDF2.PdfReader(io.BytesIO(pdf))


def _text(pdf):
    return "\n".join(p.extract_text() or "" for p in _reader(pdf).pages)


def _positions(pdf):
    found = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text.strip():
            found.append((text.strip(), tm[4]))
    _reader(pdf).pages[0].extract_text(visitor_text=visitor)
    return found


@pytest.fixture(scope='module')
def pdf():
    return docx_bytes_to_pdf(make_docx())


def test_pdf_is_valid(pdf):
    assert pdf.startswith(b'%PDF-')
    assert len(_reader(pdf).pages) >= 1


def test_latin_and_cyrillic_text_extracted(pdf):
    t = _text(pdf)
    assert 'Anna Smith' in t and 'Lead Engineer' in t
    assert 'Руководитель проекта' in t and 'Українська мова' in t


def test_hebrew_letters_present(pdf):
    t = _text(pdf)
    heb = [ch for ch in HEBREW if ch != ' ']
    got = [ch for ch in t if '\u0590' <= ch <= '\u05ff']
    assert sorted(heb) == sorted(got)


def test_bold_italic_and_serif_fonts_embedded(pdf):
    assert b'LiberationSerif-Bold' in pdf
    assert b'LiberationSerif-BoldItalic' in pdf   # заголовок: жирный курсив
    assert b'LiberationSerif' in pdf


def test_sans_font_family_for_unknown_fonts():
    d = Document()
    r = d.add_paragraph().add_run("Sans sample")
    r.font.name = 'Arial'
    r.bold = True
    buf = io.BytesIO()
    d.save(buf)
    out = docx_bytes_to_pdf(buf.getvalue())
    assert b'FiraSans-Bold' in out


def test_name_is_centered_and_dates_on_the_right(pdf):
    pos = {t: x for t, x in _positions(pdf)}
    name_x = [x for t, x in pos.items() if t.startswith('Anna')][0]
    date_x = [x for t, x in pos.items() if '2020' in t][0]
    assert 150 < name_x < 300           # по центру A4, не у левого поля
    assert date_x > 400                  # правый таб: дата у правого края


def test_text_color_is_kept(pdf):
    contents = b''.join(p.get_contents().get_data() for p in _reader(pdf).pages)
    assert b'.121569 .219608 .392157 rg' in contents


def test_border_line_under_heading_is_drawn():
    with_b = docx_bytes_to_pdf(make_docx(border=True))
    without = docx_bytes_to_pdf(make_docx(border=False))
    c1 = _reader(with_b).pages[0].get_contents().get_data()
    c0 = _reader(without).pages[0].get_contents().get_data()
    assert c1.count(b' l S') == c0.count(b' l S') + 1


def test_table_text_and_borders(pdf):
    t = _text(pdf)
    assert 'Python, SQL' in t and 'Languages' in t
    c = _reader(pdf).pages[0].get_contents().get_data()
    assert c.count(b' l S') >= 8        # линии рамок ячеек


def test_page_size_follows_docx():
    d = Document()
    d.sections[0].page_width, d.sections[0].page_height = d.sections[0].page_height, d.sections[0].page_width
    d.add_paragraph("landscape")
    buf = io.BytesIO()
    d.save(buf)
    box = _reader(docx_bytes_to_pdf(buf.getvalue())).pages[0].mediabox
    assert float(box.width) > float(box.height)


def test_long_resume_goes_to_second_page():
    out = docx_bytes_to_pdf(make_docx(extra=120))
    assert len(_reader(out).pages) >= 2
    assert 'Filler paragraph number 119' in _text(out)


def test_arabic_and_chinese_do_not_crash():
    d = Document()
    p = d.add_paragraph()
    p._p.get_or_add_pPr().append(OxmlElement('w:bidi'))
    p.add_run("مهندس برمجيات")
    d.add_paragraph("软件工程师 Software engineer")
    buf = io.BytesIO()
    d.save(buf)
    out = docx_bytes_to_pdf(buf.getvalue())
    assert out.startswith(b'%PDF-')
    assert '软件工程师' in _text(out)


def test_font_files_and_licenses_exist():
    for f in ('FiraSans-Bold.ttf', 'FiraSans-Italic.ttf', 'FiraSans-BoldItalic.ttf',
              'LiberationSerif-Regular.ttf', 'LiberationSerif-Bold.ttf', 'LiberationSerif-Italic.ttf',
              'LiberationSerif-BoldItalic.ttf', 'LiberationSerif-OFL.txt', 'FiraSans-OFL.txt'):
        assert os.path.isfile(os.path.join(ROOT, 'static', 'fonts', f)), f


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


def _post(client, raw, with_file=True, file_bytes=None, item_ids='ok', replace=None):
    text, ids = _marked(raw)
    if replace:
        text = text.replace(*replace)
    data = {'improved_resume': text}
    if with_file:
        data['original_file'] = (io.BytesIO(file_bytes if file_bytes is not None else raw), 'resume.docx')
    if item_ids == 'ok':
        data['item_ids'] = json.dumps(ids)
    elif item_ids is not None:
        data['item_ids'] = item_ids
    return client.post('/api/improve/pdf', data=data, content_type='multipart/form-data')


def test_route_with_original_uses_converter(client):
    r = _post(client, make_docx(), replace=('Built internal tools', 'Created internal tools'))
    assert r.status_code == 200 and r.mimetype == 'application/pdf'
    assert r.data.startswith(b'%PDF-')
    assert b'LiberationSerif' in r.data                 # признак нового конвертера
    t = _text(r.data)
    assert 'Created internal tools' in t and 'Built internal tools' not in t


@pytest.mark.parametrize('ids_value', [None, '[]'])
def test_route_creative_empty_item_ids_keeps_improved_text(client, ids_value):
    r = _post(client, make_docx(), item_ids=ids_value, replace=('Built internal tools', 'Created internal tools'))
    assert r.status_code == 200
    assert b'LiberationSerif' not in r.data             # прежний путь
    assert 'Created internal tools' in _text(r.data)


def test_route_without_original_uses_old_path(client):
    r = _post(client, make_docx(), with_file=False)
    assert r.status_code == 200 and r.data.startswith(b'%PDF-') and b'LiberationSerif' not in r.data


def test_route_broken_docx_falls_back_with_200(client):
    r = _post(client, make_docx(), file_bytes=b'not a docx at all')
    assert r.status_code == 200 and r.data.startswith(b'%PDF-')
    assert b'Traceback' not in r.data and b'BadZipFile' not in r.data


def test_route_bad_item_ids_falls_back_with_200(client):
    r = _post(client, make_docx(), item_ids='{not json')
    assert r.status_code == 200 and r.data.startswith(b'%PDF-')
    assert b'Expecting' not in r.data


def test_route_converter_failure_falls_back_with_200(client):
    with patch('app.utils.docx_to_pdf.docx_bytes_to_pdf', side_effect=RuntimeError('boom /secret/path')):
        r = _post(client, make_docx())
    assert r.status_code == 200 and b'secret' not in r.data and r.data.startswith(b'%PDF-')


# --- кнопка на сайте ---------------------------------------------------------------------------

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


def test_download_pdf_sends_original_only_with_docx_and_item_ids():
    b = _body('downloadPdf')
    cond = ("if (isNormalizableFile(selectedFile)"          # N1b: было endsWith('.docx')
            " && Array.isArray(lastItemIds) && lastItemIds.length) {")
    assert cond in b
    assert b.index(cond) < b.index("append('original_file', selectedFile)") < b.index("append('item_ids', JSON.stringify(lastItemIds))")
    assert b.index("append('item_ids'") < b.index("fetch('/api/improve/pdf'")
    assert "formData.append('improved_resume', getEffectiveRaw() || getEffectiveDisplay());" in b
    assert 'guardSelection()' in b


def test_docx_button_unchanged_by_x4():
    # N1b: кнопка DOCX (вход TXT) теперь шлёт оригинал по условию isNormalizableFile; остальные условия X4 не менялись
    assert "endsWith('.docx')" not in _body('downloadDocx')


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


# --- Cycle X4b: символы, которых нет в шрифтах (узкие пробелы и т.п.) ---------------------------------

def _plain_docx(text, font=None, bold=False):
    d = Document()
    r = d.add_paragraph().add_run(text)
    if font:
        r.font.name = font
    r.bold = bold
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_narrow_spaces_become_plain_spaces_in_pdf():
    out = docx_bytes_to_pdf(_plain_docx("Fortune\u202f500 and Adobe\u2009XD, Q\u200a1, a\u2003b"))
    t = _text(out)
    assert 'Fortune 500' in t and 'Adobe XD' in t and 'Q 1' in t and 'a b' in t
    assert not any(ch in t for ch in '\u202f\u2009\u200a\u2003')


def test_narrow_spaces_in_serif_and_bold_text():
    out = docx_bytes_to_pdf(_plain_docx("Fortune\u202f500", font='Times New Roman', bold=True))
    assert 'Fortune 500' in _text(out)


def test_non_breaking_hyphen_becomes_hyphen():
    out = docx_bytes_to_pdf(_plain_docx("e\u2011mail and co\u2010op"))
    assert 'e-mail' in _text(out) and 'co-op' in _text(out)


def test_cleaned_characters_exist_in_all_used_fonts():
    """Нет .notdef: каждый символ после замены есть в cmap Fira Sans и Liberation Serif (по реальным файлам)."""
    from fontTools.ttLib import TTFont
    from app.utils.docx_to_pdf import _clean
    problem = ''.join(chr(c) for c in (0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2008, 0x2009, 0x200A, 0x202F,
                                       0x2010, 0x2011, 0x2060, 0xFEFF, 0x2032, 0x2023, 0x25E6, 0x25AA, 0x27A4, 0x2713))
    cleaned = _clean(problem)
    for f in ('FiraSans-Regular.ttf', 'FiraSans-Bold.ttf', 'LiberationSerif-Regular.ttf'):
        cmap = TTFont(os.path.join(ROOT, 'static', 'fonts', f)).getBestCmap()
        assert [ch for ch in cleaned if ord(ch) not in cmap] == [], f


def test_common_punctuation_is_left_unchanged():
    from app.utils.docx_to_pdf import _clean
    s = "\u201cquotes\u201d \u2018x\u2019 \u2013 \u2014 \u00a0 \u00ab\u00bb \u2022 \u2026 \u2192 text"
    assert _clean(s) == s
    out = docx_bytes_to_pdf(_plain_docx(s))
    assert '\u201cquotes\u201d' in _text(out)


def test_ruble_sign_in_serif_text_is_kept_via_sans_fallback():
    out = docx_bytes_to_pdf(_plain_docx("Salary 10\u00a0000 \u20bd", font='Times New Roman'))
    assert '\u20bd' in _text(out)
