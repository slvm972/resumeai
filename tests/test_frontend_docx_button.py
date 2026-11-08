"""
Cycle F1b: кнопка "DOCX (Word)" для входных форматов не-DOCX и meta-тег подтверждения владения сайтом.
Статическая проверка index.html (без браузера) + node --check встроенных скриптов, если node есть.
Запуск: FLASK_ENV=testing python -m pytest tests/test_frontend_docx_button.py -q
"""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()


def _func(name):
    m = re.search(r'async function %s\(\) \{(.*?)\n  \}\n' % name, HTML, re.S)
    assert m, name
    return m.group(1)


def test_01_google_site_verification_meta_in_head():
    head = HTML.split('</head>')[0]
    assert ('<meta name="google-site-verification" '
            'content="ru_Wg8HmWB9j06LVV7wYgxZVVqDAC-e5t-lVylSAkmE" />') in head
    assert HTML.count('google-site-verification') == 1


def test_02_docx_button_exists_between_txt_and_odt():
    i_txt = HTML.index('id="downloadBtn"')
    i_docx = HTML.index('id="downloadDocxBtn"')
    i_odt = HTML.index('id="downloadOdtBtn"')
    assert i_txt < i_docx < i_odt
    assert 'onclick="downloadDocx()"' in HTML


def test_03_button_hidden_only_for_docx_input():
    assert "document.getElementById('downloadDocxBtn').style.display = (lastImprovedFormat === 'docx') ? 'none' : '';" in HTML


def test_04_download_docx_sends_text_only_to_docx_endpoint():
    body = _func('downloadDocx')
    assert "fetch('/api/improve/docx'" in body
    assert "formData.append('improved_resume', getEffectiveRaw() ||" in body
    assert 'original_file' not in body and 'item_ids' not in body     # оригинала нет, DOCX собирает сервер
    assert "a.download = 'improved_resume.docx'" in body
    assert 'btn.disabled = true' in body and 'btn.disabled = false' in body and 'finally' in body


def test_05_existing_docx_path_for_docx_input_is_untouched():
    body = _func('downloadImproved')
    assert "formData.append('original_file', selectedFile)" in body
    assert "formData.append('item_ids', JSON.stringify(lastItemIds))" in body


def test_06_other_export_buttons_still_present():
    for bid, fn in (('downloadOdtBtn', 'downloadOdt'), ('downloadRtfBtn', 'downloadRtf'), ('downloadPdfBtn', 'downloadPdf')):
        assert 'id="%s"' % bid in HTML and 'onclick="%s()"' % fn in HTML


@pytest.mark.skipif(shutil.which('node') is None, reason='node не установлен')
def test_07_inline_scripts_pass_node_check(tmp_path):
    blocks = re.findall(r'<script(?![^>]*src)[^>]*>(.*?)</script>', HTML, re.S)
    assert blocks
    for n, code in enumerate(blocks):
        f = tmp_path / ('blk%d.js' % n)
        f.write_text(code, encoding='utf-8')
        r = subprocess.run(['node', '--check', str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[:400]
