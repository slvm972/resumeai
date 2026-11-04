"""
Cycle C1c-0: подсчёт метрик скрипта tools/creative_baseline.py на фиктивных данных. Без сети.
Запуск: FLASK_ENV=testing python -m pytest tests/test_creative_baseline.py -q
"""
import os
import sys
import json

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'

from tools import creative_baseline as cb


def _write_weak3(path, ids, blocks):
    data = {"precise": {"item_ids": ids, "quality_report": {"blocks": blocks}}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def test_changed_ratio_counts_rewritten_lines_and_ignores_bullets():
    src = "Anna\n- Managed a team\n- Wrote reports\nSkills: Excel"
    same = "Anna\n\u2022 Managed a team\n\u2022 Wrote reports\nSkills: Excel"
    one = "Anna\n- Led a team\n- Wrote reports\nSkills: Excel"
    assert cb.changed_ratio(src, same) == 0.0
    assert cb.changed_ratio(src, one) == 0.25
    assert cb.changed_ratio("", one) == 0.0


def test_load_source_uses_item_order_and_skips_retry_duplicates(tmp_path):
    p = tmp_path / "weak3_X.json"
    blocks = [
        {"id": "002", "original_text": "second", "attempt": "attempt_1"},
        {"id": "001", "original_text": "first", "attempt": "attempt_1"},
        {"id": "002", "original_text": "second", "attempt": "attempt_2"},
        {"id": "003", "original_text": None},
    ]
    _write_weak3(str(p), ["001", "002", "003"], blocks)
    assert cb.load_source(str(p)) == "first\nsecond"


def test_summarize_run_counts_issues_calls_and_special_types():
    rr = {
        "status": "unresolved", "rounds": 2, "calls": 7, "judge_error": False,
        "text": "Anna\nLed a team of 5",
        "final_text": "Anna\nLed a team of 5\n\nChanges",
        "final_issues": [
            {"type": "LOST_NUMBER", "quote": "12", "reason": "number lost"},
            {"type": "EMBELLISHMENT", "quote": "in a busy office", "reason": "added context"},
            {"type": "EMBELLISHMENT", "quote": "for clients", "reason": "added context"},
        ],
    }
    m = cb.summarize_run("Anna\nManaged a team of 5", rr)
    assert m["unresolved"] == 3 and m["calls"] == 7
    assert m["issue_counts"] == {"LOST_NUMBER": 1, "EMBELLISHMENT": 2}
    assert len(m["lost_number"]) == 1 and len(m["embellishment"]) == 2
    assert m["changed_ratio"] == 0.5          # 1 из 2 строк исходника изменена
    assert m["final_text"].endswith("Changes")


def test_main_survives_api_error_hides_key_and_prints_average(tmp_path, monkeypatch, capsys):
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    _write_weak3(str(inp / "weak3_A.json"), ["001", "002"],
                 [{"id": "001", "original_text": "Anna"}, {"id": "002", "original_text": "Managed a team"}])
    monkeypatch.setenv("GROQ_API_KEY", "gsk_SECRET_TEST_KEY_123")
    calls = {"n": 0}

    def fake_rewrite(source, model, judge_model, api_key=None, pause=0):
        calls["n"] += 1
        if model.startswith("qwen"):
            raise RuntimeError("429 rate limit for key %s" % api_key)
        return {"status": "clean", "rounds": 0, "calls": 2, "judge_error": False,
                "text": "Anna\nLed a team", "final_text": "Anna\nLed a team", "final_issues": []}

    rc = cb.main(["--input", str(inp), "--output", str(out), "--runs", "2"], rewrite_fn=fake_rewrite)
    assert rc == 0 and calls["n"] == 4        # 1 резюме x 2 модели x 2 прогона
    files = sorted(os.listdir(out))
    assert "summary.txt" in files and "weak3_A__main__run1.json" in files and "weak3_A__fallback__run2.json" in files
    err = json.load(open(out / "weak3_A__fallback__run1.json", encoding="utf-8"))
    assert err["error"] and err["status"] == "error"
    blob = capsys.readouterr().out + "".join(open(out / f, encoding="utf-8").read() for f in files)
    assert "gsk_SECRET_TEST_KEY_123" not in blob
    assert "AVERAGE" in blob and "50%" in blob   # main: 1 из 2 строк изменена
