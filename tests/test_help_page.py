"""
Cycle H3: страница помощи /help и /help.html. Без сети.
Запуск: FLASK_ENV=testing python -m pytest tests/test_help_page.py -q
"""
import os
import re
import sys

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

import pytest

from app import create_app

ANCHORS = ["quick-start", "formats", "languages", "scores", "modes", "credits", "contact"]


@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _page(client, url):
    r = client.get(url)
    assert r.status_code == 200, url
    assert 'text/html' in r.content_type
    return r.get_data(as_text=True)


@pytest.mark.parametrize("url", ["/help", "/help.html"])
def test_both_routes_return_200(client, url):
    assert "<title>Help - ResumeAI</title>" in _page(client, url)


def test_all_anchors_present_and_linked_from_toc(client):
    html = _page(client, "/help.html")
    for a in ANCHORS:
        assert 'id="%s"' % a in html, a
        assert 'href="#%s"' % a in html, a


def test_required_words_and_score_thresholds(client):
    html = _page(client, "/help.html")
    for word in ("Hebrew", "Arabic", "beta", "Precise", "Creative"):
        assert word in html, word
    scores = html[html.index('id="scores"'):html.index('id="modes"')]
    assert "80" in scores and "65" in scores and "50" in scores


def test_no_external_scripts(client):
    html = _page(client, "/help.html")
    assert not re.search(r"<script[^>]*\ssrc\s*=", html, re.I)
    assert "<script" not in html.lower()


def test_index_help_links_point_to_existing_page(client):
    index = _page(client, "/")
    assert 'href="/help.html"' in index and 'href="/help.html#scores"' in index
