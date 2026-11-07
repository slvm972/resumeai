"""
Cycle H2: тексты главной страницы (index.html) соответствуют коду. Без сети, html читается как текст.
Запуск: FLASK_ENV=testing python -m pytest tests/test_frontend_landing_copy.py -q
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BETA = "Hebrew and Arabic support is in beta — please review the improved resume carefully before using it."


def _read(name):
    with open(os.path.join(_ROOT, name), encoding='utf-8') as f:
        return f.read()


def test_beta_note_under_upload_card_in_card_and_in_modal():
    html = _read('index.html')
    assert 'class="beta-note"' in html
    assert html.count(BETA) >= 3          # под карточкой загрузки, в карточке Multilingual, в окне openFeat('languages')
    modal = html[html.index("languages: {"):]
    assert BETA in modal[:2500]


def test_upload_subtitle_lists_input_and_output_formats():
    html = _read('index.html')
    assert "Input: PDF, DOCX, ODT, RTF, TXT · Output: DOCX, ODT, RTF, PDF · Max 5 MB" in html


def test_help_link_in_nav_footer_and_under_score_ring():
    html = _read('index.html')
    nav = html[html.index('<ul class="nav-links">'):]
    nav = nav[:nav.index('</ul>')]
    assert 'href="/help.html"' in nav
    footer = html[html.index('<footer>'):]
    product = footer[footer.index('<h4>Product</h4>'):footer.index('<h4>Company</h4>')]
    assert 'href="/help.html"' in product
    assert re.search(r'href="/help\.html#scores"[^>]*target="_blank"', html)
    assert 'What do these scores mean?' in html


def _ring_threshold(html):
    m = re.search(r"ring\.style\.stroke = score >= (\d+) \? '#10b981' : score >= (\d+) \?", html)
    assert m, "не найдена строка цвета кольца"
    return int(m.group(1)), int(m.group(2))


def test_ring_threshold_is_65_and_matches_dashboard_and_history():
    green, yellow = _ring_threshold(_read('index.html'))
    assert (green, yellow) == (80, 65)
    for page in ('dashboard.html', 'history.html'):
        m = re.search(r"function scoreColor\(s\)\{ if\(s>=(\d+)\) return 'var\(--success\)'; if\(s>=(\d+)\)", _read(page))
        assert m and (int(m.group(1)), int(m.group(2))) == (green, yellow), page


def test_old_ring_threshold_60_is_gone():
    assert "score >= 60 ? '#f59e0b'" not in _read('index.html')


def test_no_unconfirmed_language_claims_and_layout_sentence_present():
    html = _read('index.html')
    low = html.lower()
    assert '97 languages' not in low and '97 supported languages' not in low
    assert 'formatting preserved in every language' not in low
    assert 'Precise mode keeps your original layout' in html and 'Creative mode rebuilds it' in html
    assert 'quality is not guaranteed' in html
