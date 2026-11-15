"""
Cycle C1: сборка нового DOCX из исходного DOCX и текста Creative (app/utils/creative_docx_builder.py).
Без сети. Данные: tests/data/creative_c1/ (3 оригинала и реальные тексты Creative; текст для инженера
СИНТЕТИЧЕСКИЙ: Creative на этом резюме упал в Precise, поэтому текст сделан по тем же правилам из оригинала).
Запуск: FLASK_ENV=testing python -m pytest tests/test_creative_docx_builder.py -q
"""
import io
import os
import sys
import json
import collections

import pytest

sys.path.insert(0, '/home/claude/resumeai')
os.environ.setdefault('FLASK_ENV', 'testing')

from docx import Document
from docx.table import Table
from docx.oxml.ns import qn

from app.utils.creative_docx_builder import (
    parse_creative_text, build_creative_docx, analyze_original, CreativeDocxError)
from app.utils.docx_to_odt import docx_bytes_to_odt
from app.utils.docx_to_rtf import docx_bytes_to_rtf
from app.utils.docx_to_pdf import docx_bytes_to_pdf

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'creative_c1')
with open(os.path.join(DATA, 'creative_texts.json'), encoding='utf-8') as _f:
    TEXTS = json.load(_f)

ORIG_FILES = {'designer': 'resume_english_designer.docx', 'ua': 'resume_ua.docx',
              'engineer_synthetic': 'resume_russian_engineer.docx'}


def orig(key):
    with open(os.path.join(DATA, ORIG_FILES[key]), 'rb') as f:
        return f.read()


def _doc(b):
    return Document(io.BytesIO(b))


def _texts(doc):
    out = []
    for it in doc.iter_inner_content():
        if isinstance(it, Table):
            for row in it.rows:
                cells = [c.text.strip() for c in row.cells]
                out.append(' | '.join(cells))
        elif it.text.strip():
            out.append(it.text.strip())
    return out


def _flat(s):
    import re
    return re.sub(r'[\s\u2022\u00b7*\-\u2013\u2014,;|:]+', '', s)


# --- парсер ----------------------------------------------------------------------------------------

@pytest.mark.parametrize('key', sorted(TEXTS))
def test_parser_runs_on_all_texts_and_keeps_every_line(key):
    blocks = parse_creative_text(TEXTS[key])
    assert blocks and blocks[0]['role'] == 'NAME'
    assert [b for b in blocks if b['role'] == 'CONTACT']
    got = ''
    for b in blocks:
        if b['role'] == 'SKILL_ROW':
            got += b['label'] + b['value']
        elif b['role'] == 'JOB':
            got += b['text'] + (b['date'] or '')
        elif b['role'] != 'BLANK':
            got += b['text']
    assert _flat(got) == _flat(TEXTS[key])


def test_roles_designer_exact_sequence():
    roles = [b['role'] for b in parse_creative_text(TEXTS['designer'])]
    assert roles[:3] == ['NAME', 'TITLE', 'CONTACT']
    assert roles.count('HEADING') == 4 and roles.count('JOB') == 3 and roles.count('DATE') == 3
    assert roles.count('BULLET') == 8 and roles.count('SKILL_ROW') == 4
    i = roles.index('JOB')
    assert roles[i:i + 3] == ['JOB', 'DATE', 'BULLET']


def test_roles_w1_inline_dates_are_split_from_job_lines():
    blocks = parse_creative_text(TEXTS['W1'])
    jobs = [b for b in blocks if b['role'] == 'JOB']
    assert len(jobs) == 2
    assert jobs[0]['text'] == 'Офис-менеджер, ООО Альфа-Торг' and jobs[0]['date'] == '2019 - 2024'
    assert jobs[0]['raw'] == jobs[0]['text'] + jobs[0]['sep'] + ' ' + jobs[0]['date']
    assert jobs[1]['date'] == '2015-2019'
    assert [b['text'] for b in blocks if b['role'] == 'BULLET'][4] == 'Офисные программы: MS Office, 1С'


def test_skill_rows_only_inside_skills_sections():
    blocks = parse_creative_text(TEXTS['W5'])
    rows = [b for b in blocks if b['role'] == 'SKILL_ROW']
    assert [r['label'] for r in rows] == ['Водительские категории:']
    assert 'Знание города' in [b['text'] for b in blocks if b['role'] == 'BODY']


def test_label_on_separate_line_is_merged_into_skill_row():
    text = "Иван Иванов\nivan@example.com | +7 900 000 00 00\n\nНавыки\nCAD/CAE:\nAutoCAD, SolidWorks\nЯзыки:\nРусский (родной)"
    rows = [b for b in parse_creative_text(text) if b['role'] == 'SKILL_ROW']
    assert [(r['label'], r['value']) for r in rows] == [('CAD/CAE:', 'AutoCAD, SolidWorks'),
                                                         ('Языки:', 'Русский (родной)')]


def test_hebrew_and_arabic_do_not_break_parser():
    for text in ("יוסי כהן\nyossi@example.com | 050-1234567\n\nניסיון תעסוקתי\nמהנדס תוכנה בכיר\n- פיתוח מערכות",
                 "أحمد علي\nahmed@example.com | +20 100 000 0000\n\nالخبرة\nمهندس برمجيات\n- تطوير الأنظمة"):
        blocks = parse_creative_text(text)
        assert blocks[0]['role'] == 'NAME' and any(b['role'] == 'BULLET' for b in blocks)


def test_role_statistics_over_all_texts():
    c = collections.Counter(b['role'] for k in TEXTS for b in parse_creative_text(TEXTS[k]))
    assert c['NAME'] == 9 and c['CONTACT'] == 9 and c['HEADING'] >= 36 and c['JOB'] >= 20
    assert c['BULLET'] >= 40 and c['SKILL_ROW'] >= 15 and c['DATE'] >= 6


# --- образцы оригинала ------------------------------------------------------------------------------

def test_original_samples_designer_table_form():
    s = analyze_original(orig('designer'))['samples']
    assert {'NAME', 'TITLE', 'CONTACT', 'HEADING', 'JOB_TABLE', 'SKILL_TABLE', 'BULLET', 'SEP_SECTION'} <= set(s)


def test_original_samples_ua_paragraph_form():
    s = analyze_original(orig('ua'))['samples']
    assert {'NAME', 'CONTACT', 'HEADING', 'JOB_PARA', 'DATE', 'BULLET'} <= set(s)
    assert 'JOB_TABLE' not in s and 'SKILL_TABLE' not in s


# --- сборка ----------------------------------------------------------------------------------------

def test_name_centering_and_heading_colors_are_kept():
    o, b = _doc(orig('designer')), _doc(build_creative_docx(orig('designer'), TEXTS['designer']))
    po, pb = o.paragraphs[0], b.paragraphs[0]
    assert pb.text == 'Sarah Mitchell'
    assert pb._p.pPr.find(qn('w:jc')).get(qn('w:val')) == 'center'
    assert pb.runs[0].font.size == po.runs[0].font.size and str(pb.runs[0].font.color.rgb) == '1F4E79'
    heads = [p for p in b.paragraphs if p.text in ('Summary', 'Work Experience', 'Education', 'Skills')]
    assert len(heads) == 4
    assert all(h.runs[0].bold and h.runs[0].font.size == o.paragraphs[4].runs[0].font.size for h in heads)


def test_dates_go_to_right_aligned_cell():
    b = _doc(build_creative_docx(orig('designer'), TEXTS['designer']))
    job_tables = [t for t in b.tables if '2021' in t.rows[0].cells[1].text or '2018' in t.rows[0].cells[1].text
                  or '2014' in t.rows[0].cells[1].text]
    assert len(job_tables) == 3
    right = job_tables[0].rows[0].cells[1].paragraphs[0]
    assert right._p.pPr.find(qn('w:jc')).get(qn('w:val')) == 'right'
    assert job_tables[0].rows[0].cells[0].text.startswith('Lead UX Designer')


def test_skill_rows_return_to_label_value_tables():
    b = _doc(build_creative_docx(orig('designer'), TEXTS['designer']))
    rows = [(t.rows[0].cells[0].text, t.rows[0].cells[1].text) for t in b.tables if t.rows[0].cells[0].text.endswith(':')]
    assert len(rows) == 4 and rows[0][0] == 'Design Tools:' and rows[3][0] == 'Languages:'


def test_bullets_keep_numpr_in_designer_and_text_marker_in_ua():
    bd = _doc(build_creative_docx(orig('designer'), TEXTS['designer']))
    bullets = [p for p in bd.paragraphs if p._p.pPr is not None and p._p.pPr.find(qn('w:numPr')) is not None]
    assert len(bullets) == 8 and not bullets[0].text.startswith(('-', '•'))
    bu = _doc(build_creative_docx(orig('ua'), TEXTS['ua']))
    marked = [p.text for p in bu.paragraphs if p.text.startswith('• ')]
    assert len(marked) == 8


def test_ua_dates_stay_separate_paragraphs():
    texts = _texts(_doc(build_creative_docx(orig('ua'), TEXTS['ua'])))
    i = texts.index('Senior Backend Developer — ТОВ «КодЛаб», Львів')
    assert texts[i + 1] == '2020 — теперішній час'


def test_inline_dates_are_restored_to_original_form():
    # текст W1 (дата в конце строки) на шаблоне с таблицами: дата уходит в правую ячейку
    d = _doc(build_creative_docx(orig('designer'), TEXTS['W1']))
    first = d.tables[0].rows[0].cells
    assert first[0].text == 'Офис-менеджер, ООО Альфа-Торг' and first[1].text == '2019 - 2024'


def test_new_section_without_sample_uses_heading_and_body():
    d = _doc(build_creative_docx(orig('designer'), TEXTS['engineer_synthetic']))
    texts = [p.text for p in d.paragraphs]
    assert 'Сертификаты' in texts
    heading = d.paragraphs[texts.index('Сертификаты')]
    ref = next(p for p in d.paragraphs if p.text == 'Опыт работы')
    assert heading.runs[0].bold == ref.runs[0].bold and heading.runs[0].font.size == ref.runs[0].font.size
    assert 'PMP, ISO 9001 Lead Auditor' in texts


@pytest.mark.parametrize('key', sorted(ORIG_FILES))
def test_built_texts_equal_blocks_and_no_old_text_leaks(key):
    built = build_creative_docx(orig(key), TEXTS[key])
    got = _flat(''.join(_texts(_doc(built))))
    want = _flat(TEXTS[key])
    assert got == want
    built_flat, creative_flat = got, want
    for t in _texts(_doc(orig(key))):
        f = _flat(t)
        if len(f) > 30 and f in built_flat:
            assert f in creative_flat, 'старый текст попал в результат: %r' % t[:60]


@pytest.mark.parametrize('key', sorted(ORIG_FILES))
def test_chain_odt_rtf_pdf_on_built_docx(key):
    built = build_creative_docx(orig(key), TEXTS[key])
    assert docx_bytes_to_odt(built).getvalue()[:2] == b'PK'
    assert docx_bytes_to_rtf(built).startswith(b'{\\rtf1')
    assert docx_bytes_to_pdf(built).startswith(b'%PDF-')


@pytest.mark.parametrize('src', ['W1', 'W2', 'W3', 'W4', 'W5', 'W6'])
@pytest.mark.parametrize('key', sorted(ORIG_FILES))
def test_any_creative_text_on_any_template(key, src):
    built = build_creative_docx(orig(key), TEXTS[src])
    assert _flat(''.join(_texts(_doc(built)))) == _flat(TEXTS[src])
    docx_bytes_to_pdf(built)


def test_page_setup_is_preserved():
    o, b = _doc(orig('designer')), _doc(build_creative_docx(orig('designer'), TEXTS['designer']))
    so, sb = o.sections[0], b.sections[0]
    assert (so.page_width, so.page_height, so.left_margin, so.top_margin) == \
           (sb.page_width, sb.page_height, sb.left_margin, sb.top_margin)


def test_section_separators_follow_blank_lines():
    b = _doc(build_creative_docx(orig('designer'), TEXTS['designer']))
    bdr = [p for p in b.paragraphs if not p.text.strip() and p._p.pPr is not None and p._p.pPr.find(qn('w:pBdr')) is not None]
    assert len(bdr) == 4      # по одному разделителю перед каждым из 4 заголовков


def test_hebrew_text_builds_without_error():
    text = "יוסי כהן\nyossi@example.com | 050-1234567\n\nניסיון תעסוקתי\nמהנדס תוכנה בכיר\n- פיתוח מערכות"
    built = build_creative_docx(orig('designer'), text)
    assert 'יוסי כהן' in _texts(_doc(built))


# --- ошибки ----------------------------------------------------------------------------------------

@pytest.mark.parametrize('bad,code', [('', 'empty_text'), ('   \n \n', 'empty_text'), (None, 'empty_text'),
                                      ('@@@ ### ---', 'garbage_text'), ('!!', 'garbage_text')])
def test_empty_or_garbage_text_raises_clear_error(bad, code):
    with pytest.raises(CreativeDocxError) as e:
        build_creative_docx(orig('designer'), bad)
    assert e.value.code == code


def test_broken_original_raises_clear_error():
    with pytest.raises(CreativeDocxError) as e:
        build_creative_docx(b'not a docx', TEXTS['designer'])
    assert e.value.code == 'bad_original'
