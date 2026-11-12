"""
Cycle X2: кнопка ODT отправляет исходный DOCX и item_ids (только для входа .docx).
Без сети. Запуск: FLASK_ENV=testing python -m pytest tests/test_frontend_odt_button.py -q
"""
import os
import re
import shutil
import subprocess
import tempfile

import pytest

INDEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'index.html')


def _html():
    with open(INDEX, encoding='utf-8', newline='') as f:
        return f.read().replace('\r\n', '\n')


def _body(name):
    """Тело функции async function name() { ... } по балансу фигурных скобок."""
    src = _html()
    m = re.search(r'async function %s\s*\(\)\s*\{' % name, src)
    assert m, name
    i, depth = m.end(), 1
    while depth:
        c = src[i]
        depth += (c == '{') - (c == '}')
        i += 1
    return src[m.start():i]


def test_odt_body_appends_original_file_and_item_ids():
    b = _body('downloadOdt')
    assert "append('original_file', selectedFile)" in b
    assert "append('item_ids', JSON.stringify(lastItemIds))" in b


def test_both_appends_are_under_docx_check():
    b = _body('downloadOdt')
    m = re.search(r"if \(selectedFile && selectedFile\.name\.toLowerCase\(\)\.endsWith\('\.docx'\)\) \{", b)
    assert m
    assert m.start() < b.index("append('original_file'") < b.index("append('item_ids'")
    assert b.index("append('item_ids'") < b.index("fetch(")
    # item_ids только при непустом lastItemIds
    assert re.search(r"if \(lastItemIds\) \{\s*formData\.append\('item_ids'", b)


def test_odt_fetch_target_and_improved_resume_unchanged():
    b = _body('downloadOdt')
    assert "fetch('/api/improve/odt'" in b
    assert "formData.append('improved_resume', getEffectiveRaw() || getEffectiveDisplay());" in b
    assert 'guardSelection()' in b and "improved_resume.odt" in b


def test_rtf_and_pdf_do_not_send_original():
    for name in ('downloadRtf', 'downloadPdf'):
        assert 'original_file' not in _body(name), name


def test_inline_scripts_pass_node_check():
    node = shutil.which('node')
    if not node:
        pytest.skip('node не установлен')
    scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', _html(), re.S)
    assert scripts
    for i, code in enumerate(scripts):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
            f.write(code)
        try:
            r = subprocess.run([node, '--check', f.name], capture_output=True, text=True)
            assert r.returncode == 0, 'script %d: %s' % (i, r.stderr[:300])
        finally:
            os.unlink(f.name)
