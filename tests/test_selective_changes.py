"""
Cycle SA: принять или отклонить отдельную правку (серверная часть). Без сети.
Запуск: FLASK_ENV=testing python -m pytest tests/test_selective_changes.py -q
"""
import os
import sys
import json
from unittest.mock import patch

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

import pytest

from app.utils.selective_changes import apply_selection, MAX_CHANGES, MAX_TEXT_BYTES

PRECISE = ("###ITEM_001:HEADER###\nAnna Smith\n\n"
           "###ITEM_002:BULLET###\nLed a team of 5\n\n"
           "###ITEM_003:BULLET###\nBuilt internal tools\n\n"
           "###ITEM_004:BULLET###\nDelivered weekly reports")
PRECISE_DISPLAY = "Anna Smith\nLed a team of 5\nBuilt internal tools\nDelivered weekly reports"
PRECISE_CHANGES = [
    {"id": "002", "original_text": "Managed a team of 5", "improved_text": "Led a team of 5"},
    {"id": "003", "original_text": "Made internal tools", "improved_text": "Built internal tools"},
    {"id": "004", "original_text": "Sent weekly reports", "improved_text": "Delivered weekly reports"},
]


# --- Precise -----------------------------------------------------------------------------------

def test_precise_reject_one_keeps_others_and_markers():
    r = apply_selection(PRECISE, PRECISE_DISPLAY, PRECISE_CHANGES, [1])
    assert r["unrevertable"] == []
    assert "###ITEM_003:BULLET###\nMade internal tools\n\n" in r["improved_resume"]
    assert "Led a team of 5" in r["improved_resume"] and "Delivered weekly reports" in r["improved_resume"]
    assert r["improved_resume"].count("###ITEM_") == 4
    assert r["display_text"] == "Anna Smith\nLed a team of 5\nMade internal tools\nDelivered weekly reports"


def test_precise_block_changed_since_is_unrevertable():
    edited = PRECISE.replace("Built internal tools", "Built internal tools and dashboards")
    r = apply_selection(edited, PRECISE_DISPLAY, PRECISE_CHANGES, [1])
    assert r["unrevertable"] == [1] and r["improved_resume"] == edited and r["display_text"] == PRECISE_DISPLAY


# --- Creative (без маркеров) ---------------------------------------------------------------------

CREATIVE = "Anna Smith\nSummary\nSupport specialist with 5 years of experience.\nLed a team\nBuilt tools\nWindows, troubleshooting"


def test_creative_reject_one_in_the_middle():
    ch = [{"original_text": "Managed a team", "improved_text": "Led a team"},
          {"original_text": "Made tools", "improved_text": "Built tools"}]
    r = apply_selection(CREATIVE, CREATIVE, ch, [0])
    assert r["improved_resume"] == CREATIVE.replace("Led a team", "Managed a team")
    assert "Built tools" in r["improved_resume"] and r["unrevertable"] == []


def test_creative_reject_added_line_removes_line_and_one_newline():
    ch = [{"original_text": "", "improved_text": "Support specialist with 5 years of experience."}]
    r = apply_selection(CREATIVE, CREATIVE, ch, [0])
    assert r["improved_resume"] == "Anna Smith\nSummary\nLed a team\nBuilt tools\nWindows, troubleshooting"
    last = apply_selection("A\nB", "A\nB", [{"original_text": "", "improved_text": "B"}], [0])
    assert last["improved_resume"] == "A"


# --- unrevertable ----------------------------------------------------------------------------------

@pytest.mark.parametrize("improved", ["not in the text", "Led", ""])
def test_unrevertable_not_found_twice_or_empty(improved):
    text = "Led a team\nLed a project\nBuilt tools"
    r = apply_selection(text, text, [{"original_text": "Managed", "improved_text": improved}], [0])
    assert r["unrevertable"] == [0] and r["improved_resume"] == text and r["display_text"] == text


def test_reject_all_gives_originals():
    ch = [{"original_text": "Managed a team", "improved_text": "Led a team"},
          {"original_text": "Made tools", "improved_text": "Built tools"}]
    r = apply_selection(CREATIVE, CREATIVE, ch, [0, 1])
    assert "Managed a team" in r["improved_resume"] and "Made tools" in r["improved_resume"]
    assert "Led a team" not in r["improved_resume"] and "Built tools" not in r["improved_resume"]


def test_empty_rejected_returns_same_text():
    r = apply_selection(PRECISE, PRECISE_DISPLAY, PRECISE_CHANGES, [])
    assert r == {"improved_resume": PRECISE, "display_text": PRECISE_DISPLAY, "unrevertable": []}


# --- CRLF, иврит, кириллица ------------------------------------------------------------------------

def test_crlf_text_is_matched_and_kept_crlf():
    text = "Anna\r\nLed a team\r\nBuilt tools"
    r = apply_selection(text, text, [{"original_text": "Managed a team", "improved_text": "Led a team"}], [0])
    assert r["improved_resume"] == "Anna\r\nManaged a team\r\nBuilt tools"


def test_crlf_changes_against_lf_text():
    r = apply_selection("A\nLine one\nLine two\nB", "", [{"original_text": "X", "improved_text": "Line one\r\nLine two"}], [0])
    assert r["improved_resume"] == "A\nX\nB"


def test_hebrew_and_cyrillic_are_plain_strings():
    he = "ניהל צוות מהנדסים\nהוביל וניהל צוות מהנדסים"
    r = apply_selection(he, he, [{"original_text": "ניהל צוות", "improved_text": "הוביל וניהל צוות"}], [0])
    assert r["improved_resume"] == "ניהל צוות מהנדסים\nניהל צוות מהנדסים"
    ru = "Доставлял грузы по городу\nВёл документацию"
    r = apply_selection(ru, ru, [{"original_text": "Занимался доставкой", "improved_text": "Доставлял грузы по городу"}], [0])
    assert r["improved_resume"].startswith("Занимался доставкой\n")


# --- лимиты ----------------------------------------------------------------------------------------

def test_limits_text_size_and_number_of_changes():
    with pytest.raises(ValueError):
        apply_selection("x" * (MAX_TEXT_BYTES + 1), "", [], [])
    with pytest.raises(ValueError):
        apply_selection("text", "", [{"original_text": "a", "improved_text": "b"}] * (MAX_CHANGES + 1), [])
    with pytest.raises(ValueError):
        apply_selection("text", "", [{"original_text": "a", "improved_text": "b"}], [5])


# --- маршрут ---------------------------------------------------------------------------------------

@pytest.fixture
def client():
    from app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def admin_client(client):
    with client.session_transaction() as sess:
        sess['admin'] = 'admin'
    return client


URL = '/api/improve/apply_selection'


def _body():
    return {"improved_resume": PRECISE, "display_text": PRECISE_DISPLAY, "changes": PRECISE_CHANGES, "rejected": [1]}


def test_route_200_on_valid_input(admin_client):
    r = admin_client.post(URL, json=_body())
    assert r.status_code == 200
    j = r.get_json()
    assert j["success"] is True and j["unrevertable"] == [] and "Made internal tools" in j["improved_resume"]


def test_route_401_without_login(client):
    assert client.post(URL, json=_body()).status_code == 401


@pytest.mark.parametrize("payload", [
    "not json at all",
    {"improved_resume": ""},
    {"improved_resume": "text", "changes": "x"},
    {"improved_resume": "text", "changes": [{"original_text": "a", "improved_text": "b"}], "rejected": [9]},
    {"improved_resume": "text", "changes": [1, 2], "rejected": [0]},
])
def test_route_400_on_garbage(admin_client, payload):
    if isinstance(payload, str):
        r = admin_client.post(URL, data=payload, content_type='application/json')
    else:
        r = admin_client.post(URL, json=payload)
    assert r.status_code == 400
    assert r.get_json()["success"] is False


def test_route_does_not_leak_exception_text(admin_client):
    leak = "boom at /home/claude/resumeai/app/secret_module.py via https://api.groq.com/v1"
    for exc, status in ((RuntimeError(leak), 500), (ValueError(leak), 400)):
        with patch('app.utils.selective_changes.apply_selection', side_effect=exc):
            r = admin_client.post(URL, json=_body())
        raw = json.dumps(r.get_json())
        assert r.status_code == status
        for bad in ("secret_module", "/home/claude", "api.groq.com", "groq"):
            assert bad not in raw
