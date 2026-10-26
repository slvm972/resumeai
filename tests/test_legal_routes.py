"""
Маршруты /privacy.html и /terms.html (ссылки в футере index.html и в Google Cloud Console). Без сети.
Запуск: FLASK_ENV=testing python -m pytest tests/test_legal_routes.py -q
"""
import os
import re
import sys

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

from app import create_app

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        yield app.test_client()


@pytest.mark.parametrize('url, filename, title', [
    ('/privacy.html', 'privacy.html', 'Privacy Policy'),
    ('/terms.html', 'terms.html', 'Terms of Service'),
])
def test_legal_pages_are_served_publicly_without_login(client, url, filename, title):
    resp = client.get(url)
    assert resp.status_code == 200 and resp.mimetype == 'text/html'
    body = resp.get_data(as_text=True)
    assert title in body
    with open(os.path.join(_ROOT, filename), encoding='utf-8', newline='') as f:
        assert body.replace('\r\n', '\n') == f.read().replace('\r\n', '\n')   # отдаётся именно файл из репозитория


def test_index_footer_links_point_to_served_pages(client):
    with open(os.path.join(_ROOT, 'index.html'), encoding='utf-8') as f:
        html = f.read()
    hrefs = set(re.findall(r'href="(/(?:privacy|terms)\.html)"', html))
    assert hrefs == {'/privacy.html', '/terms.html'}
    for url in hrefs:
        assert client.get(url).status_code == 200
