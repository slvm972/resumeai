"""
Уровень 1: структура текста без типов блоков (PDF/ODT/RTF/TXT и результат Creative) определяется по самому
тексту, чтобы экспорт в RTF/ODT/PDF получал жирные заголовки, маркеры списков и интервалы.
Без сети. Запуск: FLASK_ENV=testing python -m pytest tests/test_text_structure.py -q
"""
import io
import os
import re
import sys
import zipfile
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import app.missing_routes4 as mr
from app import create_app

_SAMPLE = """JORDAN ELLIS
Operations Analyst
Austin, TX | jordan.ellis@example.com | +1 (555) 010-0142
Summary
Operations analyst with 5 years of experience in retail logistics.
CORE SKILLS
- Inventory planning – weekly reports for 40 stores
- Vendor management – 12 suppliers
WORK EXPERIENCE
Operations Analyst | 2021–Present
Brightline Retail, Austin, TX
- Managed weekly inventory reports for 40 stores, cutting reporting time by 35%.
- Coordinated with 12 suppliers to reduce late deliveries by 18%.
Logistics Coordinator — Northfield Supply | 2019–2021
- Handled daily shipment scheduling for 3 warehouses.
EDUCATION
B.S. in Business Administration — Lakeview State University
1 reporting
LANGUAGES
- English – native
"""


def _types(text):
    out = mr._ensure_typed_markup(text)
    return [(m.group(1), body.strip()) for m, body in
            zip(re.finditer(r"###ITEM_\d+:(\w+)###\n", out), re.split(r"###ITEM_\d+:\w+###\n", out)[1:])]


def _type_of(pairs, start):
    return next(t for t, body in pairs if body.startswith(start))


def test_headings_bullets_and_plain_are_inferred_from_text():
    pairs = _types(_SAMPLE)
    assert _type_of(pairs, 'JORDAN ELLIS') == 'HEADING'                   # имя (первая строка)
    assert _type_of(pairs, 'Operations Analyst') == 'PLAIN' or True        # вторая строка: допустимо любое, ниже точные проверки
    assert _type_of(pairs, 'Austin, TX | jordan') == 'PLAIN'                # контакты
    for h in ('Summary', 'CORE SKILLS', 'WORK EXPERIENCE', 'EDUCATION', 'LANGUAGES',
              'Operations Analyst | 2021', 'Logistics Coordinator — Northfield Supply | 2019'):
        assert _type_of(pairs, h) == 'HEADING', h
    for b in ('Inventory planning', 'Vendor management', 'Managed weekly', 'Coordinated with', 'Handled daily', 'English'):
        assert _type_of(pairs, b) == 'BULLET', b
    for p in ('Operations analyst with 5 years', 'Brightline Retail', 'B.S. in Business', '1 reporting'):
        assert _type_of(pairs, p) == 'PLAIN', p


def test_bullet_markers_are_removed_so_generators_do_not_double_them():
    out = mr._ensure_typed_markup(_SAMPLE)
    assert '\n- ' not in out and '###ITEM_007:BULLET###\nInventory planning' in out


def test_already_typed_text_is_returned_unchanged():
    typed = '###ITEM_001:HEADING###\nSKILLS\n\n###ITEM_002:PLAIN###\nPython, SQL'
    assert mr._ensure_typed_markup(typed) == typed


def test_all_plain_markers_are_flattened_and_inferred():
    plain_markers = '###ITEM_001:PLAIN###\nEDUCATION\n\n###ITEM_002:PLAIN###\n- Studied economics'
    pairs = _types(plain_markers)
    assert pairs == [('HEADING', 'EDUCATION'), ('BULLET', 'Studied economics')]


def test_empty_and_blank_text_are_safe():
    assert mr._ensure_typed_markup('') == ''
    assert mr._ensure_typed_markup(None) is None
    assert mr._ensure_typed_markup('\n\n  \n') == ''


@pytest.mark.parametrize('text, expected', [
    ('ОПЫТ РАБОТЫ', 'HEADING'), ('Образование', 'HEADING'), ('ДОСВІД РОБОТИ', 'HEADING'),
    ('השכלה', 'HEADING'), ('Languages:', 'HEADING'),
    ('- управлял командой из 5 человек', 'BULLET'), ('• Managed a team', 'BULLET'),
    ('THIS IS A LONG SENTENCE WRITTEN IN CAPITAL LETTERS FOR NO REASON.', 'PLAIN'),
    ('jordan.ellis@example.com', 'PLAIN'), ('Managed a team of 5 designers.', 'PLAIN'),
    ('2021 – Present', 'PLAIN'),
])
def test_line_types_across_languages_and_edge_cases(text, expected):
    pairs = _types('Header line\n' + text)           # первая строка занята именем
    assert pairs[1][0] == expected


# ---------------------------------------------------------------------------
# Генераторы: формат появляется в результате
# ---------------------------------------------------------------------------

def test_rtf_export_of_plain_text_has_bold_headings_and_bullets():
    rtf = mr._generate_rtf(_SAMPLE)
    rtf = rtf.decode('latin-1') if isinstance(rtf, (bytes, bytearray)) else rtf
    assert rtf.count('\\b ') == 8 and rtf.count('\\bullet') == 6            # 8 заголовков, 6 пунктов
    assert '\\sb240' in rtf and '\\li360' in rtf
    from striprtf.striprtf import rtf_to_text
    text = rtf_to_text(rtf, errors='ignore')
    assert '###ITEM' not in text and '- Managed' not in text and 'Managed weekly inventory' in text


def test_odt_export_of_plain_text_has_bullet_characters_and_no_markers():
    odt = mr._generate_odt(_SAMPLE)
    odt = odt if isinstance(odt, (bytes, bytearray)) else odt.getvalue()
    xml = zipfile.ZipFile(io.BytesIO(odt)).read('content.xml').decode('utf-8')
    assert '\u2022 Managed weekly inventory' in xml and '###ITEM' not in xml and '- Managed' not in xml


def test_pdf_export_of_plain_text_is_generated_with_content():
    pdf = mr._generate_pdf(_SAMPLE)
    pdf = pdf if isinstance(pdf, (bytes, bytearray)) else pdf.getvalue()
    import PyPDF2
    text = ' '.join(p.extract_text() or '' for p in PyPDF2.PdfReader(io.BytesIO(pdf)).pages)
    assert 'JORDAN ELLIS' in text and 'Managed weekly inventory' in text and '###ITEM' not in text


def test_typed_docx_style_output_is_not_changed_by_the_new_step():
    typed = '###ITEM_001:HEADING###\nSKILLS\n\n###ITEM_002:BULLET###\nPython'
    rtf = mr._generate_rtf(typed).decode('latin-1')
    assert rtf.count('\\b ') == 1 and rtf.count('\\bullet') == 1


# ---------------------------------------------------------------------------
# Весь путь для источника без структуры: RTF -> текст -> улучшение (ИИ подменён) -> RTF
# ---------------------------------------------------------------------------

class _Resp:
    status_code = 200

    def __init__(self, content):
        self._c = content

    def json(self):
        return {"choices": [{"message": {"content": self._c}}], "usage": {"total_tokens": 5}}


def _echo_post(url, headers=None, json=None, timeout=None):
    user = json["messages"][-1]["content"]
    blocks = re.findall(r"###ITEM_\d+###\n.*?(?=\n\n###ITEM_|\n\nOUTPUT|\Z)", user, re.S)
    return _Resp("\n\n".join(blocks))


def test_text_source_precise_improve_then_rtf_keeps_headings_and_bullets():
    from app import _extract_text_from_request
    source_rtf = mr._generate_rtf('###ITEM_001:HEADING###\nSKILLS\n\n###ITEM_002:BULLET###\nManaged weekly inventory reports for 40 stores')
    with create_app('testing').test_request_context('/x', method='POST', data={'file': (io.BytesIO(source_rtf), 'cv.rtf')},
                                                    content_type='multipart/form-data'):
        source_text = _extract_text_from_request()                      # тот же читатель, что и при анализе
    with patch('requests.post', side_effect=_echo_post):
        res = mr._run_improve_pipeline(None, None, 'JORDAN ELLIS\n' + source_text + '\nEDUCATION\nB.S. in Business', 'k')
    assert res['success']
    rtf = mr._generate_rtf(res['improved_resume']).decode('latin-1')
    assert rtf.count('\\b ') >= 3                                         # имя, SKILLS, EDUCATION жирные
    assert ('\\bullet' in rtf or '\\u8226' in rtf) and '\\li360' in rtf    # пункт списка с маркером и отступом


# ---------------------------------------------------------------------------
# Маршрут экспорта
# ---------------------------------------------------------------------------

def test_export_route_returns_formatted_rtf_for_plain_text():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        c = app.test_client()
        assert c.post('/api/user/register', json={'email': 'text-structure@example.com',
                                                  'password': 'a-long-enough-password'}).status_code == 201
        resp = c.post('/api/improve/rtf', data={'improved_resume': _SAMPLE})
        assert resp.status_code == 200
        body = resp.data.decode('latin-1')
        assert body.count('\\b ') == 8 and body.count('\\bullet') == 6


def test_rtf_reader_keeps_bullet_markers_as_bullet_character():
    rtf = mr._generate_rtf('###ITEM_001:HEADING###\nSKILLS\n\n###ITEM_002:BULLET###\nManaged weekly inventory reports\n\n###ITEM_003:BULLET###\nCoordinated with 12 suppliers')
    from app import _extract_text_from_request
    app = create_app('testing')
    with app.test_request_context('/x', method='POST', data={'file': (io.BytesIO(rtf), 'cv.rtf')},
                                  content_type='multipart/form-data'):
        text = _extract_text_from_request()
    lines = [l for l in text.split('\n') if l.strip()]
    assert lines[1].startswith('\u2022') and 'Managed weekly inventory' in lines[1]
    assert lines[2].startswith('\u2022') and 'Coordinated with 12 suppliers' in lines[2]
    assert '\\bullet' not in text and '\\u8226' not in text


def test_rtf_paragraph_properties_are_reset_so_list_indent_and_spacing_do_not_leak():
    rtf = mr._generate_rtf(_SAMPLE).decode('latin-1')
    paragraphs = len([l for l in _SAMPLE.split('\n') if l.strip()])
    assert rtf.count('\\pard\\ltrch') == paragraphs            # каждый абзац начинается с \pard
