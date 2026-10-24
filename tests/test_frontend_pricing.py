"""
Витрина тарифов в index.html (#pricing): два пакета кредитов. Без сети.
Запуск: FLASK_ENV=testing python -m pytest tests/test_frontend_pricing.py -q
"""
import os
import re
import shutil
import subprocess
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LS_LINK_1 = 'https://mostovoy-ai.lemonsqueezy.com/checkout/buy/5cea1ea6-7ae8-447d-953e-9f41366a205e'
_LS_URL_RE = re.compile(r'https://mostovoy-ai\.lemonsqueezy\.com/checkout/buy/[0-9a-f-]{36}')


def _html():
    with open(os.path.join(_ROOT, 'index.html'), encoding='utf-8') as f:
        return f.read()


def _pricing(html=None):
    html = html or _html()
    start = html.index('id="pricing"')
    return html[start:html.index('</section>', start)]


def _cards(block):
    """Тексты карточек .plan-card внутри #pricing (до следующей карточки / конца сетки)."""
    starts = [m.start() for m in re.finditer(r'<div class="plan-card[ "]', block)]
    ends = starts[1:] + [len(block)]
    return [block[a:b] for a, b in zip(starts, ends)]


def test_exactly_two_plan_cards_in_pricing():
    assert len(_cards(_pricing())) == 2


def test_credit_counts_and_prices_present():
    block = _pricing()
    for text in ('10 credits', '30 credits', '$9.99', '$24.99'):
        assert text in block, text


def test_best_value_badge_on_second_card_only():
    first, second = _cards(_pricing())
    assert 'BEST VALUE' in second and 'popular-badge' in second
    assert 'BEST VALUE' not in first and 'popular-badge' not in first
    assert 'plan-card popular' in second


def test_first_card_keeps_original_lemonsqueezy_link():
    first, _ = _cards(_pricing())
    assert _LS_LINK_1 in first and 'Buy 10 Credits' in first


def test_second_card_link_is_placeholder_or_valid_lemonsqueezy_url():
    _, second = _cards(_pricing())
    hrefs = re.findall(r'<a href="([^"]+)"[^>]*>\s*Buy 30 Credits', second)
    assert len(hrefs) == 1
    href = hrefs[0]
    assert href == '__CHECKOUT_URL_30__' or _LS_URL_RE.fullmatch(href), href


def test_no_forbidden_words_in_pricing():
    low = _pricing().lower()
    for word in ('unlimited', 'guarantee'):
        assert word not in low, word


def test_price_per_credit_lines_match_prices():
    cards = _cards(_pricing())
    expected = [('9.99', 10, '1.00'), ('24.99', 30, '0.83')]
    for card, (price, credits, per_credit) in zip(cards, expected):
        assert '$' + price in card and ('%d credits' % credits) in card
        stated = re.search(r'\$(\d+\.\d\d) per credit', card).group(1)
        assert stated == per_credit
        assert '%.2f' % round(float(price) / credits, 2) == stated   # цена / кредиты = заявленная цена за кредит


def test_save_percent_is_consistent_with_prices():
    _, second = _cards(_pricing())
    saved = int(re.search(r'save (\d+)%', second).group(1))
    assert saved == round((1 - 24.99 / (30 * 9.99 / 10)) * 100) or saved == round((1 - 24.99 / 30) * 100)


def test_shared_email_and_free_credits_lines_are_after_cards():
    block = _pricing()
    last_card_end = block.rindex('</div>\n  </div>') if '</div>\n  </div>' in block else block.rindex('</a>')
    tail = block[last_card_end:]
    assert 'Use the same email at checkout' in tail
    assert '2 free credits' in tail
    assert block.count('Use the same email at checkout') == 1


def test_inline_scripts_pass_node_check():
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден в окружении: проверка синтаксиса инлайн-скриптов пропущена')
    scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', _html(), re.S)
    assert scripts
    for i, code in enumerate(scripts):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tmp:
            tmp.write(code)
        try:
            res = subprocess.run([node, '--check', tmp.name], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(tmp.name)
        assert res.returncode == 0, 'script #%d: %s' % (i, res.stderr[:400])
