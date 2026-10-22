"""
Тесты app/creative_bridge.py (шаг 2а). Без сети: creative_rewrite мокается.
Запуск: FLASK_ENV=testing python -m pytest tests/test_creative_bridge.py -q
"""
import json
import sys

import pytest

sys.path.insert(0, '/home/claude/resumeai')

from app import creative_bridge as cb
from app import rewrite_creative as rc

_KEY = "SECRET-KEY-XYZ-123"
_TEXT = "Anna Smith\nanna@example.com\nSales Associate, Acme Store, 2018-2022\nWorked with customers"
_MODEL_TEXT = ("Anna Smith\nanna@example.com\nSummary\nSales associate.\nExperience\n- Served customers\n"
               "DERIVED\n- 4 years = 2018-2022\nCHANGELOG\n- old model log")
_CHECKS = {"NEW_NUMBERS": [], "DERIVED_NUMBERS": [], "LOST_CONTACTS": [],
           "HEADER_PRESERVED": True, "FORBIDDEN": 0}
_RESULT_KEYS = {"attempted", "acceptable", "status", "improved_resume", "display_text", "derived",
                "changelog", "detected_language", "rounds", "calls", "prompt_version", "checks",
                "issues", "error"}


def _fake_result(status="clean", acceptable=True, **over):
    final = _MODEL_TEXT.split("CHANGELOG")[0] + "Changes\nRewritten:\n\u00abWorked\u00bb \u2192 \u00abServed\u00bb"
    r = {"status": status, "acceptable": acceptable, "text": _MODEL_TEXT, "final_text": final,
         "changelog": "Changes\nRewritten:\n\u00abWorked\u00bb \u2192 \u00abServed\u00bb", "rounds": 0,
         "calls": 2, "final_issues": [], "judge_error": False, "checks": dict(_CHECKS),
         "error": None, "prompt_version": "v2.2"}
    r.update(over)
    return r


class _Calls(list):
    """Записанные вызовы creative_rewrite + управление ответом."""
    result = None

    def set_result(self, r):
        self.result = r


@pytest.fixture
def calls(monkeypatch):
    rec = _Calls()
    rec.result = _fake_result()

    def fake(source_text, api_key, **kw):
        rec.append({"source": source_text, "key": api_key, **kw})
        return rec.result

    monkeypatch.setattr(rc, "creative_rewrite", fake)
    return rec


_CFG = {"GROQ_API_KEY": _KEY}


# ---- is_creative_v2_enabled --------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes", "On", " true ", True])
def test_01_enabled_values(value):
    assert cb.is_creative_v2_enabled({"CREATIVE_V2_ENABLED": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "2", None, False, 1, "enabled"])
def test_02_disabled_values(value):
    assert cb.is_creative_v2_enabled({"CREATIVE_V2_ENABLED": value}) is False


def test_03_missing_key_and_bad_cfg_are_false():
    assert cb.is_creative_v2_enabled({}) is False
    assert cb.is_creative_v2_enabled(None) is False


# ---- источник текста ---------------------------------------------------------

def test_10_docx_path_uses_extract_full_text_from_docx(monkeypatch, calls):
    import app.missing_routes4 as mr
    seen = []
    monkeypatch.setattr(mr, "_extract_full_text_from_docx", lambda b: seen.append(b) or _TEXT)
    r = cb.creative_improve(b"DOCXBYTES", "cv.DOCX", None, _CFG)
    assert seen == [b"DOCXBYTES"] and calls[0]["source"] == _TEXT and r["attempted"] is True


def test_11_text_path_uses_fallback_and_other_files_are_decoded(calls):
    cb.creative_improve(None, None, "  " + _TEXT + "  ", _CFG)
    assert calls[0]["source"] == _TEXT
    cb.creative_improve(_TEXT.encode("utf-8"), "cv.txt", "ignored", _CFG)
    assert calls[1]["source"] == _TEXT


def test_12_short_text_is_not_attempted(calls):
    for args in ((None, None, "short"), (None, None, None), (b"tiny", "a.txt", None)):
        r = cb.creative_improve(*args, _CFG)
        assert r == {"attempted": False, "acceptable": False, "error": "Resume text too short"}
    assert calls == []


# ---- результаты --------------------------------------------------------------

@pytest.mark.parametrize("status", ["clean", "repaired"])
def test_20_clean_and_repaired_are_acceptable_and_text_has_no_blocks(calls, status):
    calls.set_result(_fake_result(status=status, rounds=1 if status == "repaired" else 0))
    r = cb.creative_improve(None, None, _TEXT, _CFG)
    assert set(r) == _RESULT_KEYS
    assert r["attempted"] is True and r["acceptable"] is True and r["status"] == status
    assert r["improved_resume"] == r["display_text"] == (
        "Anna Smith\nanna@example.com\nSummary\nSales associate.\nExperience\n- Served customers")
    for word in ("DERIVED", "Changes", "Изменения", "Зміни", "CHANGELOG"):
        assert word not in r["improved_resume"]
    assert r["derived"] == "- 4 years = 2018-2022"
    assert r["changelog"].startswith("Changes") and "old model log" not in r["changelog"]
    assert r["detected_language"] == "English" and r["prompt_version"] == "v2.2"
    assert r["checks"] == _CHECKS and r["error"] is None and r["calls"] == 2


def test_21_unresolved_and_not_acceptable_are_passed_through(calls):
    calls.set_result(_fake_result(status="unresolved", acceptable=False))
    assert cb.creative_improve(None, None, _TEXT, _CFG)["acceptable"] is False
    calls.set_result(_fake_result(status="clean", acceptable=False))
    r = cb.creative_improve(None, None, _TEXT, _CFG)
    assert r["acceptable"] is False and r["status"] == "clean"
    calls.set_result(_fake_result(status="error", acceptable=False, error="Creative rewrite failed",
                                  text="", final_text="", changelog=""))
    r = cb.creative_improve(None, None, _TEXT, _CFG)
    assert r["acceptable"] is False and r["error"] == "Creative rewrite failed" and r["improved_resume"] == ""


def test_22_detected_language_uses_pipeline_format(calls):
    r = cb.creative_improve(None, None, "Иван Петров\nОпыт работы: склад, оформление накладных", _CFG)
    assert r["detected_language"] == "Russian"                 # полное название, как в _run_improve_pipeline
    r = cb.creative_improve(None, None, "\u05e1\u05dc\u05d1\u05d4 \u05de\u05d5\u05e1\u05ea\u05d5\u05d1\u05d5\u05d9 \u05e0\u05d9\u05e1\u05d9\u05d5\u05df \u05ea\u05e2\u05e1\u05d5\u05e7\u05ea\u05d9", _CFG)
    assert r["detected_language"] == "Hebrew"


def test_23_issues_are_short_max5_and_quote_truncated(calls):
    issues = [{"type": "FLUFF", "quote": "q" * 200, "reason": "long reason"} for _ in range(8)]
    calls.set_result(_fake_result(status="unresolved", acceptable=False, final_issues=issues))
    r = cb.creative_improve(None, None, _TEXT, _CFG)
    assert len(r["issues"]) == 5
    assert all(set(i) == {"type", "quote"} and len(i["quote"]) == 80 for i in r["issues"])


# ---- ошибки ------------------------------------------------------------------

def test_30_exception_in_creative_rewrite_returns_dict(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-detail Anna Smith " + _KEY)
    monkeypatch.setattr(rc, "creative_rewrite", boom)
    r = cb.creative_improve(None, None, _TEXT, _CFG)
    assert set(r) == _RESULT_KEYS and r["acceptable"] is False and r["error"]
    assert "secret-detail" not in json.dumps(r) and _KEY not in json.dumps(r) and "Anna" not in json.dumps(r)


def test_31_exception_reading_docx_returns_dict(monkeypatch, calls):
    import app.missing_routes4 as mr

    def bad(_b):
        raise ValueError("secret-detail corrupt Anna Smith")
    monkeypatch.setattr(mr, "_extract_full_text_from_docx", bad)
    r = cb.creative_improve(b"x", "cv.docx", None, _CFG)
    assert r["acceptable"] is False and r["attempted"] is False and r["error"] == "Could not read resume file"
    assert "secret-detail" not in json.dumps(r) and calls == []


def test_32_bad_cfg_never_raises():
    r = cb.creative_improve(None, None, _TEXT, None)
    assert r["acceptable"] is False and r["error"]


# ---- cfg ---------------------------------------------------------------------

def test_40_models_and_budget_from_cfg(calls):
    cfg = {"GROQ_API_KEY": _KEY, "GROQ_MODEL_EDITOR": "ed/model", "GROQ_MODEL_JUDGE": "jd/model",
           "CREATIVE_MAX_SECONDS": "45"}
    cb.creative_improve(None, None, _TEXT, cfg)
    c = calls[0]
    assert (c["key"], c["model"], c["judge_model"], c["max_seconds"]) == (_KEY, "ed/model", "jd/model", 45)
    assert c["repair_rounds"] == 2 and c["pause"] == 0


def test_41_defaults(calls):
    cb.creative_improve(None, None, _TEXT, {"GROQ_API_KEY": _KEY, "CREATIVE_MAX_SECONDS": None,
                                            "GROQ_MODEL_EDITOR": "", "GROQ_MODEL_JUDGE": None})
    c = calls[0]
    assert c["model"] == c["judge_model"] == "openai/gpt-oss-120b" and c["max_seconds"] == 60
    cb.creative_improve(None, None, _TEXT, {"GROQ_API_KEY": _KEY, "CREATIVE_MAX_SECONDS": "abc"})
    assert calls[1]["max_seconds"] == 60


def test_42_no_key_goes_through_to_creative_rewrite_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "ENV-MUST-NOT-BE-USED")
    r = cb.creative_improve(None, None, _TEXT, {})              # настоящий creative_rewrite: пустой ключ -> error, без сети
    assert r["attempted"] is True and r["acceptable"] is False and r["status"] == "error" and r["error"]


# ---- утечки ------------------------------------------------------------------

def test_50_key_and_source_fragment_not_in_result(calls):
    marker = "UNIQUE-SOURCE-MARKER-777"
    calls.set_result(_fake_result(status="error", acceptable=False, error="Creative rewrite failed",
                                  text="", final_text="", changelog="", final_issues=[]))
    for res in (cb.creative_improve(None, None, _TEXT + " " + marker, _CFG),
                cb.creative_improve(None, None, "short", _CFG)):
        dump = json.dumps(res, ensure_ascii=False)
        assert _KEY not in dump and marker not in dump
    calls.set_result(_fake_result())
    assert _KEY not in json.dumps(cb.creative_improve(None, None, _TEXT, _CFG))


# ---- split_creative_output ---------------------------------------------------

def test_60_split_ru():
    t = "Иван\nПрофиль\nТекст\nDERIVED\n- 4 года\nИзменения\nПереписано:\n«а» → «б»"
    main, derived, changelog = cb.split_creative_output(t)
    assert main == "Иван\nПрофиль\nТекст" and derived == "- 4 года" and changelog.startswith("Переписано")


def test_61_split_uk():
    main, derived, changelog = cb.split_creative_output("Іван\nТекст\nЗміни\nДодано:\n+ «нова»")
    assert main == "Іван\nТекст" and derived == "" and changelog.startswith("Додано")


def test_62_split_en_and_changelog_alias():
    main, _, changelog = cb.split_creative_output("Anna\nText\nChanges\nRewritten:\n\u00aba\u00bb \u2192 \u00abb\u00bb")
    assert main == "Anna\nText" and changelog.startswith("Rewritten")
    main, _, changelog = cb.split_creative_output("Anna\nText\nCHANGELOG\n- x")
    assert main == "Anna\nText" and changelog == "- x"


def test_63_split_without_blocks_and_empty():
    assert cb.split_creative_output("Anna\nText") == ("Anna\nText", "", "")
    assert cb.split_creative_output("") == ("", "", "")
    assert cb.split_creative_output(None) == ("", "", "")


def test_64_module_has_no_flask_import():
    lines = open(cb.__file__, encoding="utf-8").read().split("\n")
    assert not [l for l in lines if l.strip().startswith(("import flask", "from flask"))]
