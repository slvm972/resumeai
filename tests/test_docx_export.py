"""
Cycle F1: DOCX-экспорт из текста для любого входного формата (ODT, PDF, RTF, TXT, creative без меток).
Раньше запасной DOCX был "голым": все строки одним стилем, без заголовков, списков и RTL.
Запуск: FLASK_ENV=testing python -m pytest tests/test_docx_export.py -q
"""
import io
import os
import sys
import uuid

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

import importlib
import config as config_module
importlib.reload(config_module)

from docx import Document
from docx.oxml.ns import qn

from app import create_app
from app.missing_routes4 import _generate_docx
from app.utils.resume_text import extract_resume_text

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

_TYPED = (
    "###ITEM_001:HEADING###\nJordan Ellis\n\n"
    "###ITEM_002:PLAIN###\nOperations Analyst | Austin, TX\n\n"
    "###ITEM_003:HEADING###\nWork Experience\n\n"
    "###ITEM_004:TABLE###\nOperations Analyst | 2021 - Present\n\n"
    "###ITEM_005:BULLET###\nCut reporting time by 35%\n\n"
    "###ITEM_006:BULLET###\n\u2022 Already has a bullet mark\n\n"
    "###ITEM_007:PLAIN###\n**Plain** text with *marks*"
)


def _doc(text):
    return Document(_generate_docx(text))


def _para(doc, starts):
    return next(p for p in doc.paragraphs if p.text.startswith(starts))


def _tags(el):
    return [c.tag.split('}')[1] for c in el]


# ---------------------------------------------------------------------------
# Типы блоков
# ---------------------------------------------------------------------------

def test_01_block_types_are_formatted():
    d = _doc(_TYPED)
    head = _para(d, "Jordan Ellis")
    assert head.runs[0].bold and head.runs[0].font.size.pt == 13
    assert head.paragraph_format.keep_with_next is True
    table = _para(d, "Operations Analyst | 2021")
    assert table.runs[0].bold and table.runs[0].font.size is None
    bullet = _para(d, "\u2022 Cut reporting")
    assert bullet.paragraph_format.left_indent is not None and not bullet.runs[0].bold
    plain = _para(d, "Operations Analyst | Austin")
    assert not plain.runs[0].bold and plain.paragraph_format.left_indent is None


def test_02_bullet_mark_is_not_doubled_and_markdown_marks_stripped():
    d = _doc(_TYPED)
    texts = [p.text for p in d.paragraphs]
    assert "\u2022 Already has a bullet mark" in texts
    assert not any(t.startswith("\u2022 \u2022") for t in texts)
    assert "Plain text with marks" in texts


def test_03_text_without_markers_gets_structure_inferred():
    text = "Jordan Ellis\nWork Experience\n- Cut reporting time by 35%\n- Built a vendor scorecard"
    d = _doc(text)
    assert _para(d, "Jordan Ellis").runs[0].bold                      # имя -> заголовок
    assert _para(d, "\u2022 Cut reporting").paragraph_format.left_indent is not None
    assert _para(d, "\u2022 Built a vendor").paragraph_format.left_indent is not None


def test_04_fonts_and_page_setup():
    d = _doc(_TYPED)
    normal = d.styles["Normal"]
    assert normal.font.name == "Arial" and normal.font.size.pt == 11
    rfonts = normal.element.rPr.find(qn("w:rFonts"))
    assert rfonts.get(qn("w:cs")) == "Arial"
    sec = d.sections[0]
    assert round(sec.left_margin.cm, 1) == 2.0 and round(sec.top_margin.cm, 1) == 2.0


def test_05_blank_lines_inside_block_keep_empty_paragraph():
    # блок не PLAIN: размеченный текст берётся как есть (чисто PLAIN-текст разбирается заново, пустые строки уходят)
    d = _doc("###ITEM_001:HEADING###\nName\n\n###ITEM_002:BULLET###\nLine one\n\nLine two")
    assert [p.text for p in d.paragraphs] == ["Name", "\u2022 Line one", "", "\u2022 Line two"]


# ---------------------------------------------------------------------------
# RTL: иврит и арабский
# ---------------------------------------------------------------------------

_HE = "###ITEM_001:HEADING###\nמוסתובוי סלבה\n\n###ITEM_002:BULLET###\nהתקין חומרה לרשתות מחשבים\n\n###ITEM_003:PLAIN###\nPlain English line"


def test_06_hebrew_paragraphs_are_bidi_with_rtl_runs():
    d = _doc(_HE)
    he_head = _para(d, "מוסתובוי")
    he_bullet = _para(d, "\u2022 התקין")
    en = _para(d, "Plain English")
    for p in (he_head, he_bullet):
        assert p._p.pPr.find(qn("w:bidi")) is not None
        assert p.runs[0]._r.rPr.find(qn("w:rtl")) is not None
        assert p.runs[0]._r.rPr.find(qn("w:szCs")) is not None
    assert he_head.runs[0]._r.rPr.find(qn("w:bCs")) is not None          # жирный и для сложных письменностей
    assert en._p.pPr is None or en._p.pPr.find(qn("w:bidi")) is None
    assert en.runs[0]._r.rPr.find(qn("w:rtl")) is None


def test_07_arabic_is_rtl_too():
    d = _doc("###ITEM_001:PLAIN###\nمرحبا بالعالم")
    p = d.paragraphs[0]
    assert p._p.pPr.find(qn("w:bidi")) is not None


def test_08_ooxml_child_order_is_schema_valid():
    """Word строг к порядку дочерних элементов pPr/rPr: bidi перед spacing/ind, rtl после szCs."""
    d = _doc(_HE)
    ppr_order = ["pStyle", "keepNext", "keepLines", "bidi", "adjustRightInd", "spacing", "ind", "jc", "rPr"]
    rpr_order = ["rFonts", "b", "bCs", "i", "color", "sz", "szCs", "u", "rtl", "cs", "lang"]
    for p in d.paragraphs:
        if p._p.pPr is not None:
            idx = [ppr_order.index(t) for t in _tags(p._p.pPr) if t in ppr_order]
            assert idx == sorted(idx), _tags(p._p.pPr)
        for r in p.runs:
            if r._r.rPr is not None:
                idx = [rpr_order.index(t) for t in _tags(r._r.rPr) if t in rpr_order]
                assert idx == sorted(idx), _tags(r._r.rPr)


# ---------------------------------------------------------------------------
# Чтение результата и маршрут
# ---------------------------------------------------------------------------

def test_09_result_is_readable_back_as_text():
    raw = _generate_docx(_TYPED).getvalue()
    text = extract_resume_text("improved_resume.docx", raw)
    for w in ("Jordan Ellis", "Work Experience", "Cut reporting time by 35%"):
        assert w in text
    assert raw[:2] == b"PK"


@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app


def _admin(app):
    from app.services.auth_service import AuthService
    user = AuthService.register('f1-%s@example.com' % uuid.uuid4().hex[:10], 'somepassword123')['user']
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = user.id
        sess['admin'] = 'admin'
    return client


def test_10_route_without_original_returns_structured_docx(app_ctx):
    client = _admin(app_ctx)
    resp = client.post('/api/improve/docx', data={'improved_resume': _HE})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.mimetype == DOCX_MIME
    d = Document(io.BytesIO(resp.get_data()))
    assert _para(d, "מוסתובוי").runs[0].bold
    assert _para(d, "\u2022 התקין")._p.pPr.find(qn("w:bidi")) is not None


def test_11_route_creative_text_without_markers_is_structured_too(app_ctx):
    client = _admin(app_ctx)
    text = "Jordan Ellis\nSummary\n- Cut reporting time by 35%"
    resp = client.post('/api/improve/docx', json={'improved_resume': text})
    assert resp.status_code == 200
    d = Document(io.BytesIO(resp.get_data()))
    assert _para(d, "Jordan Ellis").runs[0].bold
    assert _para(d, "\u2022 Cut reporting").paragraph_format.left_indent is not None


def test_12_original_docx_path_is_unchanged(app_ctx):
    """Если есть исходный DOCX и метки, форматирование оригинала по-прежнему сохраняется (таблица на месте)."""
    src = Document()
    src.add_paragraph("Jordan Ellis")
    t = src.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Role"
    t.cell(0, 1).text = "2021"
    buf = io.BytesIO()
    src.save(buf)
    client = _admin(app_ctx)
    improved = "###ITEM_001:HEADING###\nJordan Ellis"
    resp = client.post('/api/improve/docx', data={
        'improved_resume': improved, 'item_ids': '["001"]',
        'original_file': (io.BytesIO(buf.getvalue()), 'cv.docx')}, content_type='multipart/form-data')
    assert resp.status_code == 200
    out = Document(io.BytesIO(resp.get_data()))
    assert len(out.tables) == 1                       # таблица оригинала сохранена, это не новый DOCX с нуля
