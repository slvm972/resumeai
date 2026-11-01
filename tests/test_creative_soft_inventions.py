"""
Cycle C1a: «мягкие домыслы» в creative-режиме (добавленные люди, места, условия работы).
Без сети: call_groq заменён заглушкой.
Запуск: FLASK_ENV=testing python -m pytest tests/test_creative_soft_inventions.py -q
"""
import sys

import pytest

sys.path.insert(0, '/home/claude/resumeai')

from app import rewrite_creative as rc


def _soft(source, main):
    return [i for i in rc.soft_invention_issues(source, main)]


def _words(issues):
    return " | ".join(i["reason"] for i in issues)


# ---------------------------------------------------------------------------
# Реальные домыслы из прогонов владельца ловятся
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,main,expected", [
    ("Fixed computers", "- Provided on-site technical assistance to production staff",
     ["on-site", "staff"]),
    ("Configured network devices", "- Configured network devices according to project specifications",
     ["specifications"]),
    ("Taught English", "- Taught English to students for classroom use", ["students", "classroom"]),
    ("Supported printers", "- Supported printers for end-users and internal staff", ["end-users", "staff"]),
    ("Prepared reports", "- Prepared routine reports", ["routine"]),
    ("Ремонтировал компьютеры", "- Ремонтировал компьютеры для сотрудников на месте", ["сотрудников", "на месте"]),
    ("Обучал детей", "- Обучал учеников в классе", ["учеников", "в классе"]),
    ("Налаштовував мережу", "- Налаштовував мережу для користувачів за потреби", ["користувачів", "за потреби"]),
])
def test_01_known_soft_inventions_are_flagged(source, main, expected):
    issues = _soft(source, main)
    assert issues, "домысел не пойман"
    assert all(i["type"] == "EMBELLISHMENT" for i in issues)
    text = _words(issues)
    for w in expected:
        assert w in text, (w, text)


def test_02_issue_has_judge_format_and_quote_from_main():
    main = "- Provided on-site technical assistance to production staff"
    for i in _soft("Fixed computers", main):
        assert set(i) == {"type", "quote", "reason"}
        assert i["quote"] in main.replace("\n", " ")


# ---------------------------------------------------------------------------
# Креатив НЕ задет: сильные глаголы, структура, слова из исходника
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,main", [
    ("Fixed computers", "- Repaired and restored computers"),
    ("Configured network devices", "- Set up and configured network devices"),
    ("Taught English", "- Taught English"),
    ("Was involved in fixing printer problems", "- Assisted in fixing printer problems"),
    ("Worked with customers and cash", "- Served customers and handled cash"),
    ("Helped colleagues with printers", "- Assisted colleagues with printer problems"),
    ("Работал с клиентами", "- Консультировал клиентов"),
    ("Помогал сотрудникам", "- Консультировал для сотрудников по вопросам техники"),
    ("Remote support", "- Provided remote support"),
])
def test_03_honest_rewrites_and_words_from_source_are_not_flagged(source, main):
    assert _soft(source, main) == []


def test_04_candidate_himself_as_staff_is_not_an_invention():
    # "сотрудник склада" / "warehouse staff member" описывают самого кандидата, а не адресата работы
    assert _soft("Работал на складе", "Профиль\nСотрудник склада.") == []
    assert _soft("Worked in a warehouse", "Warehouse staff member with experience") == []


def test_05_synonym_groups_count_as_present_in_source():
    assert _soft("Supported employees", "- Supported staff") == []          # employees -> staff
    assert _soft("Answered client calls", "- Answered customer calls") == []  # client -> customer
    assert _soft("Тренировал студентов", "- Обучал студентов") == []


def test_06_limit_per_pattern_keeps_repair_message_short():
    main = "\n".join("- Item %d done for students" % n for n in range(8))
    assert len([i for i in _soft("Done items", main) if "students" in i["reason"]]) == 3


# ---------------------------------------------------------------------------
# Встраивание в det_issues и repair
# ---------------------------------------------------------------------------

_SRC = ("Anna Smith\nanna@example.com\nExperience\nIT Support, 2019-2022\n"
        "Fixed computers\nConfigured network devices")
_BAD = ("Anna Smith\nanna@example.com\nExperience\nIT Support, 2019-2022\n"
        "- Provided on-site technical assistance to production staff\n"
        "- Configured network devices according to project specifications\n"
        "DERIVED\nCHANGELOG\n- merged lines")
_GOOD = ("Anna Smith\nanna@example.com\nExperience\nIT Support, 2019-2022\n"
         "- Repaired computers\n- Set up and configured network devices\n"
         "DERIVED\nCHANGELOG\n- merged lines")


def test_07_det_issues_includes_soft_inventions_only_for_main_text():
    issues = rc.det_issues(_SRC, _BAD)
    soft = [i for i in issues if i["reason"].startswith("Added ")]
    assert {"on-site", "staff", "specifications"} <= {
        w for i in soft for w in ("on-site", "staff", "specifications") if w in i["reason"]}
    # слова из секций DERIVED и CHANGELOG не проверяются
    tail = _GOOD.replace("- merged lines", "- added on-site help for students")
    assert [i for i in rc.det_issues(_SRC, tail) if i["reason"].startswith("Added ")] == []
    assert [i for i in rc.det_issues(_SRC, _GOOD) if i["type"] == "EMBELLISHMENT"] == []


def test_08_repair_message_tells_to_delete_words_and_strengthen_verb():
    msg = rc.build_repair_message(rc.soft_invention_issues(_SRC, _BAD))
    assert "EMBELLISHMENT" in msg and "on-site" in msg
    assert "delete exactly those words" in msg and "stronger" in msg
    # для других замечаний подсказка не добавляется
    other = rc.build_repair_message([{"type": "FLUFF", "quote": "skilled", "reason": "Evaluative wording"}])
    assert "delete exactly those words" not in other


class _Scripted:
    def __init__(self, rewrites, judges):
        self.rewrites, self.judges = list(rewrites), list(judges)
        self.rewrite_messages = []

    def __call__(self, api_key, model, messages, **kw):
        if messages[0]["content"].startswith("You are a strict fact-checker"):
            queue = self.judges
        else:
            self.rewrite_messages.append(messages)
            queue = self.rewrites
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"text": text, "usage": {"total_tokens": 10}, "seconds": 0.1}


_PASS = '{"verdict":"pass","issues":[]}'


def test_09_repair_loop_removes_soft_inventions_and_keeps_rewrite(monkeypatch):
    sc = _Scripted([_BAD, _GOOD], [_PASS])
    monkeypatch.setattr(rc, "call_groq", sc)
    r = rc.rewrite_with_repair(_SRC, "m/main", "m/judge", repair_rounds=2, pause=0, api_key="k")
    assert r["status"] == "repaired" and r["rounds"] == 1
    first = [i["reason"] for i in r["issues_by_round"][0]]
    assert any("on-site" in x for x in first) and r["issues_by_round"][1] == []
    repair_msg = sc.rewrite_messages[1][-1]["content"]
    assert "on-site" in repair_msg and "delete exactly those words" in repair_msg
    # результат остаётся переписанным (креатив не откатывается к исходнику)
    assert "Repaired computers" in r["text"] and "Fixed computers" not in r["text"]


# ---------------------------------------------------------------------------
# Промт и рубрика судьи
# ---------------------------------------------------------------------------

def test_10_system_prompt_has_rule_8_and_keeps_creativity_clause():
    p = rc.SYSTEM_PROMPT
    assert "8. No invented context." in p
    for w in ("on-site", "production staff", "classroom", "Repaired computers"):
        assert w in p
    assert "brightness must come from precise verbs" in p
    # прежний контракт промта не нарушен
    assert p.startswith("You are an expert resume writer and career coach.")
    assert p.rstrip().endswith("what was merged or reworded and why>")
    for phrase in ("NEVER resolved", "TODAY:", "DERIVED", "CHANGELOG", "WHAT YOU ARE ENCOURAGED TO DO"):
        assert phrase in p


def test_11_judge_rubric_covers_people_places_conditions_but_not_style():
    j = rc.JUDGE_SYSTEM
    for w in ("added person or group", "place or setting", "on-site", "classroom use", "routine",
              "NOT embellishment"):
        assert w in j
    assert rc.JUDGE_ISSUE_TYPES == ("NEW_FACT", "OVERSTATED_ROLE", "EMBELLISHMENT",
                                    "TENSE_ERROR", "LANGUAGE_ERROR", "FLUFF")
    assert rc.PROMPT_VERSION == "v2.4" and rc.JUDGE_PROMPT_VERSION == "v2"
