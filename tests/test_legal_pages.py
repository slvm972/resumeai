import os

def test_privacy_policy_content():
    path = os.path.join(os.path.dirname(__file__), '..', 'privacy.html')
    assert os.path.exists(path), "privacy.html not found"
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    assert "October 2026" in content
    assert "Google sign-in" in content
    assert "Groq" in content
    assert "session cookie" in content
    assert "slvm972@gmail.com" in content

def test_terms_content():
    path = os.path.join(os.path.dirname(__file__), '..', 'terms.html')
    assert os.path.exists(path), "terms.html not found"
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    assert "October 2026" in content
    assert "18 or older" in content
    assert "Lemon Squeezy" in content
    assert "slvm972@gmail.com" in content