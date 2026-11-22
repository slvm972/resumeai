"""
Cycle N2a: адаптер ODT -> DOCX (app/utils/odt_to_docx.py), подключён в таблицу ADAPTERS как 'odt'.
Без сети. Тесты с отрисовкой требуют soffice и pdfminer (иначе пропускаются). Пути от __file__.
Запуск: FLASK_ENV=testing python -m pytest tests/test_n2_odt_adapter.py -q
"""
import collections
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile

import pytest
from lxml import etree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

from docx import Document
from docx.oxml.ns import qn

from app.utils import normalize_input as ni
from app.utils import odt_to_docx as o2d
from app.utils.normalize_input import normalize_to_docx, NormalizeRejected
from app.utils.creative_docx_builder import _analyze
from app.missing_routes4 import _extract_structured
from app.utils.docx_to_odt import docx_bytes_to_odt
from app.utils.docx_to_rtf import docx_bytes_to_rtf
from app.utils.docx_to_pdf import docx_bytes_to_pdf

DATA = os.path.join(ROOT, 'tests', 'data', 'matrix')
EXTRA = os.path.join(ROOT, 'tests', 'data', 'n2_odt')
TOOLS = os.path.join(ROOT, 'tools')
CORPUS = ['ua', 'designer', 'engineer', 'tabdates', 'hebrew_rtl']
EXTRAS = ['two_columns', 'numbered', 'bold_italic', 'with_image']
T = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
HAVE_SOFFICE = shutil.which('soffice') is not None


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def _odt(name):
    return _read(os.path.join(DATA, name + '.odt'))


def _b(x):
    return x.getvalue() if hasattr(x, 'getvalue') else x


def _doc(raw):
    return Document(io.BytesIO(raw))


def _norm(raw, name='x.odt'):
    out = normalize_to_docx(raw, name)
    assert out, name
    return out


def _flat(s):
    return re.sub(r'[\s\u2022\u00b7\uf0b7]+', '', s)


def _odt_text(raw):
    z = zipfile.ZipFile(io.BytesIO(raw))
    root = etree.fromstring(z.read('content.xml'))
    parts = []
    for el in root.iter('{%s}p' % T, '{%s}h' % T):
        parts.append(''.join(el.itertext()))
    return ''.join(parts)


def _docx_text(raw):
    d = _doc(raw)
    parts = []
    for child in d.element.body.iterchildren():
        if child.tag == qn('w:p'):
            parts.append(''.join(t.text or '' for t in child.iter(qn('w:t'))))
        elif child.tag == qn('w:tbl'):
            for p in child.iter(qn('w:p')):
                parts.append(''.join(t.text or '' for t in p.iter(qn('w:t'))))
    return ''.join(parts)


# ---------------- реестр ----------------

def test_odt_registered_in_adapters():
    assert set(ni.ADAPTERS) == {'docx', 'txt', 'odt'}


# ---------------- корпус: текст, структура ----------------

@pytest.mark.parametrize('name', CORPUS)
def test_corpus_odt_normalizes_to_valid_docx(name):
    out = _norm(_odt(name), name + '.odt')
    assert zipfile.is_zipfile(io.BytesIO(out))
    d = _doc(out)
    assert any(p.text.strip() for p in d.paragraphs) or d.tables


@pytest.mark.parametrize('name', CORPUS)
def test_corpus_text_not_lost_not_added(name):
    raw = _odt(name)
    assert _flat(_docx_text(_norm(raw))) == _flat(_odt_text(raw))


@pytest.mark.parametrize('name', EXTRAS)
def test_extra_odt_text_not_lost_not_added_and_no_crash(name):
    raw = _read(os.path.join(EXTRA, name + '.odt'))
    assert _flat(_docx_text(_norm(raw))) == _flat(_odt_text(raw))


def test_extra_two_columns_table_kept():
    d = _doc(_norm(_read(os.path.join(EXTRA, 'two_columns.odt'))))
    assert len(d.tables) == 1 and len(d.tables[0].rows[0].cells) == 2
    assert 'Skills' in d.tables[0].rows[0].cells[0].text and 'Experience' in d.tables[0].rows[0].cells[1].text


def test_extra_numbered_and_nested_bullets_are_real_lists():
    out = _norm(_read(os.path.join(EXTRA, 'numbered.odt')))
    z = zipfile.ZipFile(io.BytesIO(out))
    doc_xml, num_xml = z.read('word/document.xml').decode(), z.read('word/numbering.xml').decode()
    assert doc_xml.count('<w:numPr>') >= 6
    assert 'w:numFmt w:val="decimal"' in num_xml and 'w:numFmt w:val="bullet"' in num_xml
    assert num_xml.count('<w:abstractNum ') >= 3 + 0      # три разных стиля списков (две нумерации и маркеры)


def test_truly_nested_list_gets_second_level():
    content = ('<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:text="%s"><office:body><office:text><text:list><text:list-item><text:p>Parent</text:p>'
               '<text:list><text:list-item><text:p>Child</text:p></text:list-item></text:list></text:list-item></text:list>'
               '</office:text></office:body></office:document-content>') % T
    x = zipfile.ZipFile(io.BytesIO(_norm(_zip({'content.xml': content})))).read('word/document.xml').decode()
    assert set(re.findall(r'<w:ilvl w:val="(\d)"/>', x)) == {'0', '1'}


def test_extra_inline_formatting_kept():
    out = _norm(_read(os.path.join(EXTRA, 'bold_italic.odt')))
    x = zipfile.ZipFile(io.BytesIO(out)).read('word/document.xml').decode()
    assert '<w:b/>' in x and '<w:i/>' in x and '<w:u w:val="single"/>' in x
    assert 'w:val="C00000"' in x and 'w:vertAlign w:val="superscript"' in x and '<w:br/>' in x


def test_extra_image_ignored_without_error():
    out = _norm(_read(os.path.join(EXTRA, 'with_image.odt')))
    x = zipfile.ZipFile(io.BytesIO(out)).read('word/document.xml').decode()
    assert '<w:drawing' not in x and 'Engineer with a photo' in x


def test_no_extra_empty_paragraphs_added():
    raw = _odt('designer')
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(raw)).read('content.xml'))
    n_odt = len([e for e in root.iter('{%s}p' % T, '{%s}h' % T)])
    d = _doc(_norm(raw))
    n_docx = len(d.paragraphs) + sum(len(c.paragraphs) for t in d.tables for r in t.rows for c in r.cells)
    assert n_docx == n_odt


# ---------------- форматирование ----------------

def test_designer_formatting_carried_over():
    d = _doc(_norm(_odt('designer')))
    first = d.paragraphs[0]
    assert first.alignment is not None and int(first.alignment) == 1
    assert first.runs[0].font.size is not None and first.runs[0].font.size.pt >= 20
    heads = [p for p in d.paragraphs if p.text.strip() == 'Professional Summary']
    assert heads and heads[0].runs[0].font.bold
    assert str(heads[0].runs[0].font.color.rgb) == '1F4E79'
    assert any(p._p.pPr is not None and p._p.pPr.find(qn('w:pBdr')) is not None for p in d.paragraphs)
    assert len(d.tables) >= 1
    assert sum(1 for p in d.paragraphs if p._p.pPr is not None and p._p.pPr.find(qn('w:numPr')) is not None) >= 2


def test_tabdates_right_tab_and_hebrew_rtl():
    d = _doc(_norm(_odt('tabdates')))
    tabbed = [p for p in d.paragraphs if '\t' in p.text]
    assert len(tabbed) >= 3
    for p in tabbed:
        assert p._p.pPr.find(qn('w:tabs')).find(qn('w:tab')).get(qn('w:val')) == 'right'
    h = _doc(_norm(_odt('hebrew_rtl')))
    assert any(p._p.pPr is not None and p._p.pPr.find(qn('w:bidi')) is not None for p in h.paragraphs)
    assert all(t._tbl.tblPr.find(qn('w:bidiVisual')) is not None for t in h.tables)


def test_default_font_times_when_odt_has_no_font(tmp_path):
    """Шрифт нигде не задан: по умолчанию Times New Roman (как в P2)."""
    content = ('<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:text="%s"><office:body><office:text><text:p>Just text without any fonts</text:p></office:text>'
               '</office:body></office:document-content>' % T)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
        z.writestr('content.xml', content)
    out = _norm(buf.getvalue())
    assert 'w:ascii="Times New Roman"' in zipfile.ZipFile(io.BytesIO(out)).read('word/document.xml').decode()


# ---------------- детерминизм ----------------

@pytest.mark.parametrize('name', CORPUS)
def test_determinism_bytes_and_structure(name):
    a, b = _norm(_odt(name)), _norm(_odt(name))
    za, zb = zipfile.ZipFile(io.BytesIO(a)), zipfile.ZipFile(io.BytesIO(b))
    for part in ('word/document.xml', 'word/numbering.xml', 'word/styles.xml', 'docProps/core.xml'):
        assert za.read(part) == zb.read(part), part
    assert [i['text'] for i in _extract_structured(_doc(a))] == [i['text'] for i in _extract_structured(_doc(b))]


# ---------------- цепочка ----------------

@pytest.mark.parametrize('name', CORPUS)
def test_result_goes_through_all_exporters_and_item_ids(name):
    out = _norm(_odt(name))
    items = _extract_structured(_doc(out))
    assert items
    ids = [str(i + 1).zfill(3) for i in range(len(items))]
    text = '\n\n'.join('###ITEM_%s###\n%s' % (ids[i], items[i]['text']) for i in range(len(items)))
    from app.missing_routes4 import _apply_improved_text_to_docx
    again = _apply_improved_text_to_docx(out, text, ids).getvalue()
    assert [i['text'] for i in _extract_structured(_doc(again))] == [i['text'] for i in items]
    assert zipfile.is_zipfile(io.BytesIO(_b(docx_bytes_to_odt(out))))
    assert _b(docx_bytes_to_rtf(out))[:5] in (b'{\\rtf', '{\\rtf')
    assert _b(docx_bytes_to_pdf(out))[:4] == b'%PDF'
    _, rep = _analyze(_doc(out))
    assert rep and rep[0][2] == 'NAME'


@pytest.mark.parametrize('name', ['ua', 'designer', 'engineer'])
def test_creative_roles_match_original_docx(name):
    roles_odt = [r[2] for r in _analyze(_doc(_norm(_odt(name))))[1]]
    roles_docx = [r[2] for r in _analyze(_doc(_read(os.path.join(DATA, name + '.docx'))))[1]]
    assert roles_odt == roles_docx


# ---------------- отрисовка и профиль (soffice, pdfminer) ----------------

@pytest.fixture(scope='module')
def rendered(tmp_path_factory):
    if not HAVE_SOFFICE:
        pytest.skip('нет soffice')
    pytest.importorskip('pdfminer')
    w = tmp_path_factory.mktemp('n2render')
    srcs = []
    for n in CORPUS:
        shutil.copy(os.path.join(DATA, n + '.odt'), str(w / (n + '_o.odt')))
        with open(str(w / (n + '_a.docx')), 'wb') as f:
            f.write(_norm(_odt(n)))
        srcs += [str(w / (n + '_o.odt')), str(w / (n + '_a.docx'))]
    for n in EXTRAS:
        shutil.copy(os.path.join(EXTRA, n + '.odt'), str(w / (n + '_o.odt')))
        with open(str(w / (n + '_a.docx')), 'wb') as f:
            f.write(_norm(_read(os.path.join(EXTRA, n + '.odt'))))
        srcs += [str(w / (n + '_o.odt')), str(w / (n + '_a.docx'))]
    for i in range(0, len(srcs), 6):
        subprocess.run(['soffice', '--headless', '--convert-to', 'pdf', '--outdir', str(w)] + srcs[i:i + 6],
                       capture_output=True, timeout=240)
    return w


def _lines(pdf):
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextLine
    out = []
    for pi, p in enumerate(extract_pages(pdf)):
        def walk(o, pi=pi, p=p):
            if isinstance(o, LTTextLine):
                t = o.get_text().strip()
                for g in ('\uf0b7', '\u2022'):
                    if t.startswith(g):
                        t = t[1:].strip()
                if t:
                    out.append((pi, round(p.height - o.y1, 1), round(o.x0, 1), round(o.x1, 1), t))
            elif hasattr(o, '__iter__'):
                for c in o:
                    walk(c)
        walk(p)
    return out


@pytest.mark.parametrize('name', CORPUS + ['two_columns', 'numbered', 'bold_italic'])
def test_libreoffice_render_of_adapter_docx_matches_original_odt(rendered, name):
    """Строгая проверка: положение каждой строки текста у исходного ODT и у DOCX адаптера совпадает (допуск 3-4 пт)."""
    A = _lines(str(rendered / (name + '_o.pdf')))
    B = collections.defaultdict(list)
    for b in _lines(str(rendered / (name + '_a.pdf'))):
        B[b[4]].append(b)
    assert A
    bad = []
    for a in A:
        cand = B.get(a[4])
        if not cand:
            bad.append((a, None))
            continue
        b = cand.pop(0)
        if abs(a[3] - b[3]) > 3 or abs(a[1] - b[1]) > 4 or (abs(a[2] - b[2]) > 3 and abs(a[2] - b[2]) != 18):
            bad.append((a, b))
    assert not bad, bad[:3]


@pytest.fixture(scope='module')
def fm_module():
    pytest.importorskip('pdfminer')
    sys.path.insert(0, TOOLS)
    import format_matrix
    return format_matrix


@pytest.mark.parametrize('name', CORPUS)
def test_profile_matches_reference_docx_by_matrix_metrics(rendered, fm_module, name):
    txt = open(os.path.join(DATA, name + '.txt'), encoding='utf-8').read()
    R = fm_module.reference_profile(name)
    E = fm_module.expected_from('creative', R, txt, txt)
    res = fm_module.measure(fm_module.Pdf(str(rendered / (name + '_a.pdf'))), R, E, 'docx', b'')
    failed = {k: v[1] for k, v in res.items() if v[0] is False}
    assert not failed, failed


@pytest.mark.parametrize('name', CORPUS)
def test_profile_in_our_pdf_matches_reference(fm_module, tmp_path, name):
    txt = open(os.path.join(DATA, name + '.txt'), encoding='utf-8').read()
    R = fm_module.reference_profile(name)
    E = fm_module.expected_from('creative', R, txt, txt)
    p = tmp_path / (name + '.pdf')
    p.write_bytes(_b(docx_bytes_to_pdf(_norm(_odt(name)))))
    res = fm_module.measure(fm_module.Pdf(str(p)), R, E, 'pdf', b'')
    failed = {k: v[1] for k, v in res.items() if v[0] is False}
    assert not failed, failed


@pytest.mark.parametrize('name', CORPUS)
def test_round_trip_own_odt_gives_same_profile(fm_module, tmp_path, name):
    """ODT, сделанный нашим docx_bytes_to_odt из корпусного DOCX, нормализуется обратно с тем же профилем оформления."""
    own_odt = _b(docx_bytes_to_odt(_read(os.path.join(DATA, name + '.docx'))))
    back = _norm(own_odt, name + '.odt')
    txt = open(os.path.join(DATA, name + '.txt'), encoding='utf-8').read()
    R = fm_module.reference_profile(name)
    E = fm_module.expected_from('creative', R, txt, txt)
    p = tmp_path / (name + '.pdf')
    p.write_bytes(_b(docx_bytes_to_pdf(back)))
    res = fm_module.measure(fm_module.Pdf(str(p)), R, E, 'pdf', b'')
    failed = {k: v[1] for k, v in res.items() if v[0] is False}
    assert not failed, failed
    assert _flat(_docx_text(back)) == _flat(_docx_text(_read(os.path.join(DATA, name + '.docx'))))


# ---------------- плохие входы и безопасность ----------------

def _zip(files, mimetype=b'application/vnd.oasis.opendocument.text'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        if mimetype is not None:
            z.writestr('mimetype', mimetype)
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


_OK_CONTENT = ('<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:text="%s"><office:body><office:text><text:p>SECRET-RESUME Jane Doe</text:p></office:text></office:body>'
               '</office:document-content>' % T)


def test_minimal_valid_odt_ok():
    assert normalize_to_docx(_zip({'content.xml': _OK_CONTENT}), 'a.odt')


@pytest.mark.parametrize('kind', ['empty', 'not_zip', 'no_content', 'wrong_mimetype', 'no_mimetype', 'bad_xml', 'no_body', 'no_text',
                                  'doctype', 'xxe', 'billion_laughs'])
def test_bad_inputs_give_none_without_exception_and_log_without_resume_text(kind, caplog):
    xxe = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
           '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="%s">'
           '<office:body><office:text><text:p>SECRET-RESUME &x;</text:p></office:text></office:body></office:document-content>' % T)
    laughs = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "SECRET-RESUME"><!ENTITY b "&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;">]>'
              '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="%s">'
              '<office:body><office:text><text:p>&c;</text:p></office:text></office:body></office:document-content>' % T)
    cases = {
        'empty': b'',
        'not_zip': b'SECRET-RESUME this is not a zip file',
        'no_content': _zip({'styles.xml': '<a/>'}),
        'wrong_mimetype': _zip({'content.xml': _OK_CONTENT}, b'application/vnd.oasis.opendocument.spreadsheet'),
        'no_mimetype': _zip({'content.xml': _OK_CONTENT}, None),
        'bad_xml': _zip({'content.xml': '<office:document-content SECRET-RESUME'}),
        'no_body': _zip({'content.xml': '<root xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"/>'}),
        'no_text': _zip({'content.xml': _OK_CONTENT.replace('SECRET-RESUME Jane Doe', '   ')}),
        'doctype': _zip({'content.xml': '<!DOCTYPE x>' + _OK_CONTENT}),
        'xxe': _zip({'content.xml': xxe}),
        'billion_laughs': _zip({'content.xml': laughs}),
    }
    with caplog.at_level(logging.DEBUG):
        assert normalize_to_docx(cases[kind], 'a.odt') is None
    assert 'SECRET-RESUME' not in caplog.text and 'root:' not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], kind


def test_rejections_are_warnings_not_errors(caplog):
    with caplog.at_level(logging.DEBUG):
        normalize_to_docx(b'not a zip', 'a.odt')
    recs = [r for r in caplog.records if r.name == ni.logger.name]
    assert recs and all(r.levelno == logging.WARNING for r in recs)


def test_odt_to_docx_raises_normalize_rejected_for_bad_input():
    with pytest.raises(NormalizeRejected):
        o2d.odt_to_docx(b'not a zip')


def test_zip_bomb_limits(monkeypatch):
    big = _zip({'content.xml': _OK_CONTENT})
    monkeypatch.setattr(o2d, 'MAX_XML', 200)
    assert normalize_to_docx(big, 'a.odt') is None                         # content.xml больше лимита
    monkeypatch.setattr(o2d, 'MAX_XML', 20 * 1024 * 1024)
    monkeypatch.setattr(o2d, 'MAX_FILES', 1)
    assert normalize_to_docx(big, 'a.odt') is None                         # слишком много файлов
    monkeypatch.setattr(o2d, 'MAX_FILES', 500)
    monkeypatch.setattr(o2d, 'MAX_TOTAL_UNPACKED', 50)
    assert normalize_to_docx(big, 'a.odt') is None                         # слишком большой распакованный размер
    monkeypatch.setattr(o2d, 'MAX_TOTAL_UNPACKED', 100 * 1024 * 1024)
    assert normalize_to_docx(big, 'a.odt')


def test_really_compressed_bomb_is_rejected():
    payload = _OK_CONTENT.replace('SECRET-RESUME Jane Doe', 'x' * (22 * 1024 * 1024))
    bomb = _zip({'content.xml': payload})
    assert len(bomb) < 200 * 1024                                        # маленький архив, большой XML
    assert normalize_to_docx(bomb, 'a.odt') is None


def test_too_many_nodes_rejected(monkeypatch):
    monkeypatch.setattr(o2d, 'MAX_NODES', 3)
    assert normalize_to_docx(_zip({'content.xml': _OK_CONTENT}), 'a.odt') is None


def test_hyperlink_field_note_and_nested_table_ignored_without_error():
    content = ('<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:text="%s" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
               'xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">'
               '<office:body><office:text><text:p>See <text:a xlink:href="http://x.example">the site</text:a> now'
               '<text:note><text:note-body><text:p>footnote</text:p></text:note-body></text:note>'
               '<text:date>2020</text:date><draw:frame><draw:image/></draw:frame> done</text:p>'
               '<table:table><table:table-column/><table:table-row><table:table-cell><text:p>cell</text:p>'
               '<table:table><table:table-column/><table:table-row><table:table-cell><text:p>nested</text:p>'
               '</table:table-cell></table:table-row></table:table></table:table-cell></table:table-row></table:table>'
               '</office:text></office:body></office:document-content>') % T
    out = _norm(_zip({'content.xml': content}))
    txt = _docx_text(out)
    assert 'the site' in txt and 'cell' in txt and 'nested' not in txt
