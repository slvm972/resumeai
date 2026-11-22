"""
Cycle N1a: app/utils/normalize_input.py (нормализация входа в DOCX; вход TXT).
Без сети, без soffice, без жёстко прописанных путей.
Запуск: FLASK_ENV=testing python -m pytest tests/test_normalize_input.py -q
"""
import io
import logging
import os
import re
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

from docx import Document
from docx.oxml.ns import qn

from app.utils import normalize_input as ni
from app.utils.normalize_input import normalize_to_docx, prepare_raw_text, default_template
from app.utils.creative_docx_builder import (
    parse_creative_text, _doc_texts, _norm, _analyze)
from app.missing_routes4 import _extract_structured
from app.utils.docx_to_odt import docx_bytes_to_odt
from app.utils.docx_to_rtf import docx_bytes_to_rtf
from app.utils.docx_to_pdf import docx_bytes_to_pdf

DATA = os.path.join(ROOT, 'tests', 'data', 'matrix')
CORPUS = ['ua', 'designer', 'engineer', 'tabdates', 'hebrew_rtl']
HEADING_COLOR = '1F4E79'


def _txt(name):
    with open(os.path.join(DATA, name + '.txt'), 'rb') as f:
        return f.read()


def _b(x):
    return x.getvalue() if hasattr(x, 'getvalue') else x


def _doc(raw):
    return Document(io.BytesIO(raw))


def _roles(docx_bytes):
    _, rep = _analyze(_doc(docx_bytes))
    return [r[2] for r in rep]


def _plain(text):
    """Текст TXT без маркеров списка в начале строк."""
    return "\n".join(re.sub(r'^\s*[-\u2022*\u00b7\u2013]\s+', '', l) for l in text.split("\n"))


# ---------------- диспетчер ----------------

def test_docx_returned_as_is():
    raw = default_template(False)
    assert normalize_to_docx(raw, 'cv.docx') is raw or normalize_to_docx(raw, 'cv.docx') == raw


def test_txt_normalized_to_valid_docx():
    out = normalize_to_docx(_txt('designer'), 'designer.txt')
    assert out and zipfile.is_zipfile(io.BytesIO(out))
    assert 'Sarah Mitchell' in _doc(out).paragraphs[0].text


@pytest.mark.parametrize('name', ['cv.odt', 'cv.rtf', 'cv.pdf', 'cv.png', 'cv.doc'])
def test_unsupported_formats_return_none(name):
    assert normalize_to_docx(b'whatever', name) is None


def test_empty_and_none_input():
    assert normalize_to_docx(b'', 'a.txt') is None
    assert normalize_to_docx(None, 'a.txt') is None


def test_no_extension_sniffing():
    assert normalize_to_docx(default_template(False), 'resume')                     # PK: DOCX как есть
    assert normalize_to_docx(b'{\\rtf1\\ansi hello}', 'resume') is None
    assert normalize_to_docx(b'%PDF-1.4 stuff', None) is None
    assert normalize_to_docx(_txt('tabdates'), 'resume')                             # текст: TXT


def test_registry_is_a_table_that_next_cycles_can_extend(monkeypatch):
    assert {'docx', 'txt'} <= set(ni.ADAPTERS)          # N2a: добавлен 'odt'; N3, N4 допишут pdf, rtf
    monkeypatch.setitem(ni.ADAPTERS, 'odt', lambda b: b'FAKE-DOCX')
    assert normalize_to_docx(b'x', 'cv.odt') == b'FAKE-DOCX'


def test_adapter_exception_returns_none_and_logs_without_resume_text(monkeypatch, caplog):
    def boom(_b):
        raise ValueError('TOP-SECRET-RESUME-TEXT John Doe 555-1234')
    monkeypatch.setitem(ni.ADAPTERS, 'txt', boom)
    with caplog.at_level(logging.ERROR, logger=ni.logger.name):
        assert normalize_to_docx('John Doe 555-1234'.encode(), 'a.txt') is None
    assert 'ValueError' in caplog.text and 'boom' in caplog.text
    assert 'TOP-SECRET' not in caplog.text and 'John Doe' not in caplog.text


def test_non_utf8_txt_returns_none_old_path_stays():
    """cp1251 и другие кодировки в N1 не поддерживаются: None, поведение прежнее."""
    assert normalize_to_docx('Иван Петров\nИнженер'.encode('cp1251'), 'cv.txt') is None
    assert normalize_to_docx('Иван Петров'.encode('utf-16'), 'cv.txt') is None


def test_garbage_txt_returns_none():
    assert normalize_to_docx(b'\n\n   \n', 'a.txt') is None
    assert normalize_to_docx(b'!!! ???', 'a.txt') is None


# ---------------- детерминизм ----------------

@pytest.mark.parametrize('name', CORPUS)
def test_determinism_same_paragraphs_and_structure(name):
    a = normalize_to_docx(_txt(name), name + '.txt')
    b = normalize_to_docx(_txt(name), name + '.txt')
    da, db = _doc(a), _doc(b)
    assert _doc_texts(da) == _doc_texts(db)
    sa, sb = _extract_structured(da), _extract_structured(db)
    assert [i['text'] for i in sa] == [i['text'] for i in sb]
    assert zipfile.ZipFile(io.BytesIO(a)).read('word/document.xml') == zipfile.ZipFile(io.BytesIO(b)).read('word/document.xml')


def test_template_is_deterministic():
    a, b = ni.build_default_template(False), ni.build_default_template(False)
    za, zb = zipfile.ZipFile(io.BytesIO(a)), zipfile.ZipFile(io.BytesIO(b))
    for part in ('word/document.xml', 'word/numbering.xml', 'docProps/core.xml'):
        assert za.read(part) == zb.read(part)


# ---------------- корпус: текст и роли ----------------

@pytest.mark.parametrize('name', CORPUS)
def test_corpus_text_not_lost_not_added(name):
    text = _txt(name).decode('utf-8')
    out = normalize_to_docx(_txt(name), name + '.txt')
    assert out
    got = _norm("".join(_doc_texts(_doc(out))), True)
    assert got == _norm(_plain(text), True)


@pytest.mark.parametrize('name', CORPUS)
def test_corpus_name_centered_headings_styled(name):
    out = normalize_to_docx(_txt(name), name + '.txt')
    doc = _doc(out)
    first = doc.paragraphs[0]
    assert first.text.strip() == _txt(name).decode('utf-8').split('\n')[0].strip()
    assert first.alignment is not None and int(first.alignment) == 1          # CENTER
    heads = [p for p in doc.paragraphs if p._p.pPr is not None and p._p.pPr.find(qn('w:pBdr')) is not None]
    roles = _roles(out)
    assert len(heads) == roles.count('HEADING') >= 3
    for p in heads:
        col = p.runs[0]._r.rPr.find(qn('w:color')).get(qn('w:val'))
        assert col == HEADING_COLOR
        assert p.runs[0]._r.rPr.find(qn('w:b')) is not None


@pytest.mark.parametrize('name', CORPUS)
def test_corpus_bullets_become_list_items(name):
    text = _txt(name).decode('utf-8')
    n_src = len([l for l in text.split('\n') if re.match(r'^\s*\u2022\s', l)])
    out = normalize_to_docx(_txt(name), name + '.txt')
    doc = _doc(out)
    n_out = len([p for p in doc.paragraphs if p._p.pPr is not None and p._p.pPr.find(qn('w:numPr')) is not None])
    assert n_src > 0 and n_out == n_src
    assert not any(p.text.lstrip().startswith('\u2022') for p in doc.paragraphs)   # маркер не дублируется текстом


@pytest.mark.parametrize('name', ['ua', 'designer', 'engineer', 'tabdates'])
def test_corpus_dates_go_right_through_tab(name):
    out = normalize_to_docx(_txt(name), name + '.txt')
    doc = _doc(out)
    tabbed = [p for p in doc.paragraphs if '\t' in p.text]
    assert len(tabbed) >= 3
    for p in tabbed:
        tabs = p._p.pPr.find(qn('w:tabs'))
        assert tabs is not None and tabs.find(qn('w:tab')).get(qn('w:val')) == 'right'
        assert re.search(r'(19|20)\d\d', p.text.split('\t')[-1])


def test_hebrew_is_rtl_with_bidi_table_for_dates():
    out = normalize_to_docx(_txt('hebrew_rtl'), 'h.txt')
    doc = _doc(out)
    assert all(p._p.pPr is not None and p._p.pPr.find(qn('w:bidi')) is not None for p in doc.paragraphs if p.text.strip())
    assert len(doc.tables) == 3
    assert all(t._tbl.tblPr.find(qn('w:bidiVisual')) is not None for t in doc.tables)
    assert [t.rows[0].cells[1].text for t in doc.tables] == ['2019 - 2024', '2015 - 2019', '2011 - 2015']


def test_designer_skills_rows_and_title():
    out = normalize_to_docx(_txt('designer'), 'd.txt')
    roles = _roles(out)
    assert roles[:3] == ['NAME', 'TITLE', 'CONTACT']
    assert roles.count('SKILL_ROW') == 4
    paras = {p.text: p for p in _doc(out).paragraphs}
    p = next(p for t, p in paras.items() if t.startswith('Design Tools:'))
    assert p.runs[0].bold and not p.runs[1].bold


# ---------------- грязные TXT ----------------

def _variants(name):
    raw = _txt(name).decode('utf-8')
    return {
        'no_blank_lines': "\n".join(l for l in raw.split("\n") if l.strip()).encode('utf-8'),
        'crlf': raw.replace("\n", "\r\n").encode('utf-8'),
        'bom': b'\xef\xbb\xbf' + raw.encode('utf-8'),
    }


@pytest.mark.parametrize('kind', ['no_blank_lines', 'crlf', 'bom'])
def test_dirty_txt_same_result_as_clean(kind):
    clean = normalize_to_docx(_txt('designer'), 'd.txt')
    dirty = normalize_to_docx(_variants('designer')[kind], 'd.txt')
    assert dirty
    assert [p.text for p in _doc(dirty).paragraphs if p.text.strip()] == [p.text for p in _doc(clean).paragraphs if p.text.strip()]
    assert _roles(dirty) == _roles(clean)


@pytest.mark.parametrize('kind', ['no_blank_lines', 'crlf', 'bom'])
def test_dirty_txt_text_not_lost_and_roles_found(kind):
    raw = _variants('engineer')[kind]
    out = normalize_to_docx(raw, 'e.txt')
    text = raw.decode('utf-8-sig').replace('\r\n', '\n')
    assert _norm("".join(_doc_texts(_doc(out))), True) == _norm(_plain(text), True)
    roles = _roles(out)
    assert roles[0] == 'NAME' and roles.count('HEADING') >= 4 and 'BULLET' in roles and 'JOB' in roles


def test_markers_uppercase_and_colon_headings():
    src = ("JOHN DOE\njohn@example.com | +1 555 123 4567 | NYC\nEXPERIENCE\nDeveloper, Acme Corp 2019 - 2022\n"
           "1. Built things\n\u2013 Fixed bugs\n* Wrote docs\n\u00b7 Tuned queries\n-  Mentored juniors\n"
           "PROJECTS\nSomething cool\nResponsibilities:\n- Did it\nSkills\nLanguages: English, Hebrew\n")
    out = normalize_to_docx(src.encode('utf-8'), 'x.txt')
    assert out
    doc = _doc(out)
    roles = _roles(out)
    heads = [p.text for p in doc.paragraphs if p._p.pPr is not None and p._p.pPr.find(qn('w:pBdr')) is not None]
    assert heads == ['EXPERIENCE', 'PROJECTS', 'Responsibilities:', 'Skills']
    assert roles.count('BULLET') == 6
    assert 'SKILL_ROW' in roles
    job = next(p for p in doc.paragraphs if p.text.startswith('Developer'))
    assert job.text == 'Developer, Acme Corp\t2019 - 2022'
    assert [p.text for p in doc.paragraphs if p._p.pPr is not None and p._p.pPr.find(qn('w:numPr')) is not None][:2] == \
        ['Built things', 'Fixed bugs']


def test_date_not_swallowing_city_before_it():
    out = normalize_to_docx(b"Ann Lee\nann@x.com | +1 555 123 4567\nExperience\nDriver, Acme, Chicago\t2018 - 2024\n", 'a.txt')
    assert [p.text for p in _doc(out).paragraphs if p.text.startswith('Driver')] == ['Driver, Acme, Chicago\t2018 - 2024']


def test_dictionary_headings_ru_uk_en_he():
    for word in ('Опыт работы', 'Освіта', 'Навыки', 'Мови', 'Contacts', 'Education', 'כישורים', 'ניסיון תעסוקתי'):
        clean, hs, _ = prepare_raw_text("Name Surname\nmail@x.com | +1 555 123 4567\n" + word + "\nText line here\n")
        assert ni._norm_heading(word) in hs, word


# ---------------- цепочка экспортов ----------------

@pytest.mark.parametrize('name', CORPUS)
def test_normalized_docx_goes_through_all_exporters(name):
    out = normalize_to_docx(_txt(name), name + '.txt')
    odt = _b(docx_bytes_to_odt(out))
    assert zipfile.is_zipfile(io.BytesIO(odt))
    rtf = _b(docx_bytes_to_rtf(out))
    assert rtf[:5] == b'{\\rtf' if isinstance(rtf, bytes) else rtf.startswith('{\\rtf')
    pdf = _b(docx_bytes_to_pdf(out))
    assert pdf[:4] == b'%PDF'


# ---------------- creative_docx_builder: поведение по умолчанию не изменилось ----------------

def test_parse_creative_text_defaults_unchanged_and_extra_headings_work():
    text = "Ann Lee\nann@x.com | +1 555 123 4567\nProjects Done:\nSome text here\n"
    base = parse_creative_text(text)
    assert base == parse_creative_text(text, None, None) == parse_creative_text(text, [], [])
    assert [b['role'] for b in base] == ['NAME', 'CONTACT', 'BODY', 'BODY']
    extra = parse_creative_text(text, {'projects done'})
    assert [b['role'] for b in extra] == ['NAME', 'CONTACT', 'HEADING', 'BODY']
