"""
Лёгкий тест фронтенда creative-режима (шаг 3): читает index.html как текст, без браузера и сети.
Запуск: FLASK_ENV=testing python -m pytest tests/test_frontend_creative_notice.py -q
"""
import os
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()

NOTICE_CREATIVE = ("Creative result: new text built only from facts in your resume. "
                   "The layout of the original file is not preserved. Please review the changes below before sending.")
NOTICE_FALLBACK = ("Creative mode could not produce a result that passed our safety checks, "
                   "so Precise edits are shown instead.")
MODE_CAPTION = ("New wording and summary - takes up to a minute. "
                "Original file layout is not kept; check the change report")
SPINNER_CREATIVE = "⏳ Rewriting your resume in Creative mode… this can take up to a minute"


def _function_body(name):
    start = HTML.index("function %s(" % name) if ("function %s(" % name) in HTML else HTML.index("async function %s(" % name)
    nxt = re.search(r"\n  (?:async )?function \w+\(", HTML[start + 10:])
    return HTML[start:start + 10 + nxt.start()] if nxt else HTML[start:]


def test_01_notice_element_exists_and_is_hidden_by_default():
    m = re.search(r'<div id="creativeNotice"[^>]*>', HTML)
    assert m and "display:none" in m.group(0)
    assert HTML.index('id="creativeNotice"') < HTML.index('id="improvedText"')      # над текстом результата


def test_02_notice_logic_and_texts():
    assert "data.engine === 'creative_v2'" in HTML
    assert "data.engine === 'precise' && data.creative_fallback" in HTML
    assert NOTICE_CREATIVE in HTML.replace("' +\n            '", "").replace("' +\n          '", "") or \
        all(part in HTML for part in ("Creative result: new text built only from facts in your resume. ",
                                      "The layout of the original file is not preserved. Please review the changes below before sending."))
    assert all(part in HTML for part in ("Creative mode could not produce a result that passed our safety checks, ",
                                         "so Precise edits are shown instead."))


def test_03_notice_is_reset_at_start_of_improve_resume():
    body = _function_body("improveResume")
    first_reset = body.index("getElementById('creativeNotice').style.display = 'none'")
    assert first_reset < body.index("await fetch") if "await fetch" in body else first_reset < body.index("fetch(")


def test_04_mode_caption_and_spinner_texts():
    assert MODE_CAPTION in HTML
    assert SPINNER_CREATIVE in HTML
    assert "⏳ Rewriting your resume… this may take 15–20 seconds" in HTML          # прежний текст для precise
    body = _function_body("improveResume")
    assert "improvingSpinner').textContent = selectedCreativityMode === 'creative'" in body


def test_05_item_ids_empty_array_becomes_null_and_last_engine_is_recorded():
    assert "lastItemIds = (data.item_ids && data.item_ids.length) ? data.item_ids : null" in HTML
    assert "let lastEngine = null" in HTML and "lastEngine = data.engine || null" in HTML
    assert len(re.findall(r"\blastEngine\b", HTML)) == 2          # объявление и запись; нигде не читается


def test_06_changes_list_handles_added_and_removed_items():
    body = _function_body("improveResume")
    assert "if (!oldText && !newText) return" in body
    assert "if (oldText) {" in body and "if (newText) {" in body
    assert "(oldText ? '' : '+ ') + newText" in body


def test_07_download_report_handles_empty_texts():
    body = _function_body("downloadChangeReport")
    assert "ADDED:" in body and "REMOVED:" in body
    assert "c.original_text || ''" in body and "c.improved_text || ''" in body
    assert "${c.original_text}" not in body and "${c.improved_text}" not in body      # прямой вывод (мог дать undefined)


def test_08_new_fragments_do_not_use_innerhtml_with_server_data():
    for name in ("improveResume", "downloadChangeReport"):
        for m in re.finditer(r"innerHTML\s*=([^\n;]*)", _function_body(name)):
            assert not re.search(r"\bdata\.|\bc\.|oldText|newText|lastChanges", m.group(1)), m.group(0)
    for fragment in ("creativeNotice", "oldLine", "newLine"):
        for line in HTML.split("\n"):
            if fragment in line:
                assert "innerHTML" not in line, line
    # новые поля из ответа выводятся только через textContent
    assert "noticeEl.textContent" in HTML and "newLine.textContent" in HTML and "oldLine.textContent" in HTML


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_09_inline_scripts_pass_node_check():
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, flags=re.S | re.I)
    assert scripts
    for i, code in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(code)
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "script #%d: %s" % (i, r.stderr[:300])
