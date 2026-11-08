"""
Cycle SB: интерфейс "принять или отклонить правку" (index.html, help.html). Без сети, html читается как текст.
Запуск: FLASK_ENV=testing python -m pytest tests/test_frontend_selective_ui.py -q
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(_ROOT, name), encoding='utf-8') as f:
        return f.read()


def _func_body(html, name):
    """Текст функции от 'function name(' до следующей строки '  function'/'  async function' на том же отступе."""
    start = html.index('function %s(' % name)
    m = re.search(r"\n  (?:async )?function \w+\(", html[start + 10:])
    return html[start: start + 10 + (m.start() if m else len(html))]


def test_calls_apply_selection_with_original_data():
    html = _read('index.html')
    assert "/api/improve/apply_selection" in html
    body = _func_body(html, 'runApplySelection')
    assert "improved_resume: originalRaw" in body and "display_text: originalDisplay" in body
    assert "rejected: rejected" in body and "changes: lastChanges" in body


def test_effective_getters_exist_and_old_variable_is_gone():
    html = _read('index.html')
    assert "function getEffectiveRaw(" in html and "function getEffectiveDisplay(" in html
    assert "lastImprovedRaw" not in html


def test_export_and_copy_functions_use_effective_text_only():
    html = _read('index.html')
    for name in ("copyImproved", "downloadImproved", "downloadDocx", "downloadOdt", "downloadRtf", "downloadPdf"):
        body = _func_body(html, name)
        assert "lastImprovedRaw" not in body, name
        assert "getEffective" in body, name
        assert "document.getElementById('improvedText').textContent" not in body, name
        assert "guardSelection()" in body, name


def test_ui_has_keep_checkbox_buttons_and_messages():
    html = _read('index.html')
    assert "lbl.textContent = 'Keep'" in html and "cb.type = 'checkbox'" in html
    assert ">Keep all<" in html and ">Revert all<" in html
    assert "can't be reverted automatically" in html
    assert "kept <span id=\"changesKept\">" in html
    assert "setTimeout(() => { selTimer = null; runApplySelection(); }, 300)" in html


def test_unrevertable_rolls_checkbox_back_and_errors_keep_selection():
    body = _func_body(_read('index.html'), 'runApplySelection')
    assert "unrevertable.forEach(i => { rejectedSet.delete(i); unrevertableNotes.add(i); })" in body
    assert "res.status === 401" in body and "rollbackSelection(" in body


def test_selection_reset_on_new_improve_and_report_marks_kept_reverted():
    html = _read('index.html')
    assert "resetSelectionState();" in _func_body(html, 'improveResume')
    report = _func_body(html, 'downloadChangeReport')
    assert "'Reverted' : 'Kept'" in report and "Summary: kept" in report


def test_user_data_is_inserted_with_textcontent():
    body = _func_body(_read('index.html'), 'renderChangesList')
    assert "oldLine.textContent = oldText" in body and "newLine.textContent" in body
    assert "innerHTML = oldText" not in body and "innerHTML +=" not in body


def test_help_has_review_the_changes_paragraph():
    help_html = _read('help.html')
    assert "Review the changes: untick any change you do not want." in help_html
    assert "A few changes cannot be reverted automatically; they stay applied." in help_html
    modes = help_html[help_html.index('id="modes"'):help_html.index('id="credits"')]
    assert "Review the changes" in modes
