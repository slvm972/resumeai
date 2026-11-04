"""
Cycle T1: ячейки таблиц RTF читаются отдельными строками, а не склеиваются через символ "|".
Раньше striprtf превращал конец ячейки в "|", и разделители попадали в улучшаемый текст и результат
("תאריך לידה|"). Настоящий символ "|" в обычном тексте (например "Austin, TX | email") сохраняется.
Запуск: FLASK_ENV=testing python -m pytest tests/test_rtf_tables.py -q
"""
import sys

sys.path.insert(0, '/home/claude/resumeai')

from app.utils.resume_text import extract_resume_text


def _rtf(body):
    return (r"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Arial;}}" + body + "}").encode('latin-1')


def _row(*cells):
    """Строка таблицы как её пишет Word: \\trowd, границы \\cellxN, затем ячейки и \\row."""
    head = r"\trowd " + "".join(r"\cellx%d" % (2000 * (i + 1)) for i in range(len(cells)))
    body = "".join(r"\pard\intbl %s\cell " % c for c in cells)
    return head + body + r"\row "


def test_01_table_cells_become_separate_lines_without_pipes():
    text = extract_resume_text('cv.rtf', _rtf(_row("Operations Analyst", "2021 - Present")))
    lines = [l for l in text.split('\n') if l.strip()]
    assert lines == ["Operations Analyst", "2021 - Present"]
    assert '|' not in text


def test_02_multi_row_table_keeps_document_order():
    body = _row("Role A", "2019") + _row("Role B", "2021") + r"\pard After table\par"
    lines = [l for l in extract_resume_text('cv.rtf', _rtf(body)).split('\n') if l.strip()]
    assert lines == ["Role A", "2019", "Role B", "2021", "After table"]


def test_03_real_pipe_in_text_is_preserved():
    text = extract_resume_text('cv.rtf', _rtf(r"\pard Analyst | Austin, TX | a@b.com\par"))
    assert text == "Analyst | Austin, TX | a@b.com"


def test_04_cellx_definitions_do_not_leak_into_text():
    text = extract_resume_text('cv.rtf', _rtf(_row("Only cell")))
    assert text.strip() == "Only cell"
    assert 'cellx' not in text and '2000' not in text


def test_05_nested_cells_are_separate_lines_too():
    body = (r"\pard\intbl Outer\nestcell \pard\intbl Inner\nestcell \nestrow "
            r"\pard\intbl Last\cell \row ")
    lines = [l for l in extract_resume_text('cv.rtf', _rtf(body)).split('\n') if l.strip()]
    assert lines[0] == "Outer" and "Inner" in lines and lines[-1] == "Last"
    assert not any('|' in l for l in lines)


def test_06_trailing_tabs_and_blank_runs_are_cleaned():
    body = r"\pard Label\tab \par \par \par \par \pard Next\par"
    text = extract_resume_text('cv.rtf', _rtf(body))
    assert 'Label' in text and not any(l != l.rstrip() for l in text.split('\n'))
    assert '\n\n\n' not in text


def test_07_hebrew_label_cells_with_font_charset():
    def hx(t):
        return ''.join("\\'%02x" % b for b in t.encode('cp1255'))
    fonts = r"{\f0\fcharset204 A;}{\f46\fcharset177 Arial;}"
    head = r"{\rtf1\ansi\ansicpg1251\deff0{\fonttbl" + fonts + "}"
    body = (r"\trowd \cellx3000\cellx6000 \pard\intbl {\f46 %s}\cell \pard\intbl {\f46 %s}\cell \row"
            % (hx("09/10/1970"), hx("תאריך לידה")))
    text = extract_resume_text('cv.rtf', (head + body + "}").encode('latin-1'))
    lines = [l for l in text.split('\n') if l.strip()]
    assert lines == ["09/10/1970", "תאריך לידה"]
