"""
Cycle C1b: Creative не должен молча терять числа из исходника (35%, $1.2M, команда из 4 и т.д.)
и не должен приписывать документы ("сертификат", "תעודת"), которых нет в исходнике.
Без сети: call_groq заменён заглушкой.
Запуск: FLASK_ENV=testing python -m pytest tests/test_creative_lost_numbers.py -q
"""
import sys

import pytest

sys.path.insert(0, '/home/claude/resumeai')

from app import rewrite_creative as rc

_SRC_LINES = [
    "Helped manage weekly inventory reports for 40 stores, cutting reporting time by 35%",
    "Worked on a vendor scorecard together with a team of 4 analysts",
    "Was responsible for monthly tracking of a $1.2M logistics budget",
    "Coordinated with 12 suppliers to reduce late deliveries by 18%",
    "Helped improve order accuracy from 94% to 98%",
]
_SRC = "Jordan Ellis\nExperience\n" + "\n".join(_SRC_LINES)


def _lost(source, main):
    return [i for i in rc.lost_number_issues(source, main)]


# ---------------------------------------------------------------------------
# Реальный случай владельца: Creative выбросил метрики
# ---------------------------------------------------------------------------

def test_01_creative_dropping_metrics_is_flagged_line_by_line():
    out = ("Jordan Ellis\nExperience\n- Assisted with weekly inventory reports for stores\n"
           "- Contributed to a vendor scorecard with a team of analysts\n- Tracked monthly logistics budget\n"
           "- Coordinated with suppliers to reduce late deliveries\n- Assisted in improving order accuracy")
    issues = _lost(_SRC, out)
    assert len(issues) == 5 and all(i["type"] == "LOST_NUMBER" for i in issues)
    text = " | ".join(i["reason"] for i in issues)
    for n in ("35%", "40", "4", "1.2", "12", "18%", "94%", "98%"):
        assert n in text, n
    # quote - строка исходника, по ней модель восстановит число
    assert issues[0]["quote"] == _SRC_LINES[0]


def test_02_rewording_around_figures_is_not_flagged():
    out = ("Jordan Ellis\nExperience\n- Supported weekly inventory reports for 40 stores, reducing reporting time by 35%\n"
           "- Contributed to a vendor scorecard alongside a team of 4 analysts\n"
           "- Tracked a $1.2 M logistics budget each month\n"
           "- Coordinated with 12 suppliers to cut late deliveries by 18%\n"
           "- Raised order accuracy from 94% to 98%")
    assert _lost(_SRC, out) == []


def test_03_number_formats_are_normalized():
    assert _lost("Saved $1,500 per month", "Saved $1500 monthly") == []
    assert _lost("Growth of 2,5%", "Growth of 2.5%") == []


def test_04_contact_lines_are_skipped_even_with_broken_email():
    src = "slvm972@gmail. com\nУправлял 5 сотрудниками"
    assert _lost(src, "slvm972@gmail.com\nРуководил 5 сотрудниками") == []


def test_05_list_markers_and_dates_do_not_create_false_losses():
    src = "1. Led project\n2. Built tool\nIT Support, 2019-2022"
    out = "- Led project\n- Built tool\nIT Support, 2019-2022"
    assert _lost(src, out) == []
    assert [i["reason"] for i in _lost(src, "- Led project\nIT Support, 2019")][0].endswith("missing in the output")


def test_06_limit_keeps_repair_message_short():
    src = "\n".join("Item %d done for %d clients" % (n, 100 + n) for n in range(20))
    assert len(_lost(src, "nothing here")) == 8


def test_07_numbers_present_in_output_are_never_flagged():
    assert _lost(_SRC, _SRC) == []
    assert [i for i in rc.det_issues(_SRC, _SRC) if i["type"] == "LOST_NUMBER"] == []


def test_08_precise_style_result_from_real_run_has_no_losses():
    # результат Precise из прогона владельца: все 14 чисел на месте
    out = ("Jordan Ellis\nExperience\n- Supported the management of weekly inventory reports for 40 stores, cutting reporting time by 35%\n"
           "- Contributed to the development of a vendor scorecard with a team of 4 analysts\n"
           "- Was responsible for monthly tracking of a $1.2M logistics budget\n"
           "- Coordinated with 12 suppliers to cut late deliveries by 18%\n"
           "- Assisted in improving order accuracy from 94% to 98%")
    assert _lost(_SRC, out) == []


# ---------------------------------------------------------------------------
# Встраивание: det_issues, типы, repair и приёмка
# ---------------------------------------------------------------------------

def test_09_type_is_registered_and_judge_types_untouched():
    assert "LOST_NUMBER" in rc.ALL_ISSUE_TYPES
    assert "LOST_NUMBER" not in rc.JUDGE_ISSUE_TYPES          # судья его не возвращает, только код
    assert dict(rc._ISSUE_LETTERS)["LOST_NUMBER"] == "M"


def test_10_repair_message_demands_figures_back_exactly():
    msg = rc.build_repair_message(rc.lost_number_issues(_SRC, "Jordan Ellis\n- Assisted with reports"))
    assert "LOST_NUMBER" in msg and "35%" in msg and "never drop a figure" in msg
    other = rc.build_repair_message([{"type": "FLUFF", "quote": "skilled", "reason": "Evaluative wording"}])
    assert "never drop a figure" not in other


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
_SRC2 = ("Anna Smith\nanna@example.com\nExperience\nIT Support, 2019-2022\n"
         "Helped manage weekly reports for 40 stores, cutting reporting time by 35%\nTrained 6 coordinators")
_BAD = ("Anna Smith\nanna@example.com\nExperience\nIT Support, 2019-2022\n"
        "- Assisted with weekly reports for stores\n- Trained coordinators\nDERIVED\nCHANGELOG\n- merged")
_GOOD = ("Anna Smith\nanna@example.com\nExperience\nIT Support, 2019-2022\n"
         "- Assisted with weekly reports for 40 stores, cutting reporting time by 35%\n- Trained 6 coordinators\n"
         "DERIVED\nCHANGELOG\n- merged")


def test_11_repair_loop_restores_lost_numbers(monkeypatch):
    sc = _Scripted([_BAD, _GOOD], [_PASS])
    monkeypatch.setattr(rc, "call_groq", sc)
    r = rc.rewrite_with_repair(_SRC2, "m/main", "m/judge", repair_rounds=2, pause=0, api_key="k")
    assert r["status"] == "repaired" and r["rounds"] == 1
    assert {i["type"] for i in r["issues_by_round"][0]} == {"LOST_NUMBER"}
    msg = sc.rewrite_messages[1][-1]["content"]
    assert "35%" in msg and "LOST_NUMBER" in msg
    assert "40 stores" in r["text"] and "35%" in r["text"]


def test_12_unrepaired_loss_is_not_acceptable_so_service_uses_precise_mode(monkeypatch):
    monkeypatch.setattr(rc, "call_groq", _Scripted([_BAD], [_PASS]))
    res = rc.creative_rewrite(_SRC2, "k", repair_rounds=2, pause=0)
    assert res["status"] == "unresolved" and res["acceptable"] is False


# ---------------------------------------------------------------------------
# Документы и сертификаты
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,main,word", [
    ("תקשורת MCSE, מכללה אשרות", "הסמכת תקשורת MCSE, מכללה אשרות", "הסמכת"),
    ("תקשורת MCSE, מכללה אשרות", "תעודת MCSE בתקשורת, מכללת אשרות", "תעודת"),
    ("MCSE course, Tel Aviv college", "MCSE certification, Tel Aviv college", "certification"),
    ("Курс MCSE, колледж", "Сертификат MCSE, колледж", "Сертификат"),
    ("Курс MCSE, коледж", "Сертифікат MCSE, коледж", "Сертифікат"),
])
def test_13_invented_credential_is_flagged(source, main, word):
    issues = [i for i in rc.soft_invention_issues(source, main) if "credential" in i["reason"]]
    assert issues and word.lower() in issues[0]["reason"].lower()


@pytest.mark.parametrize("source,main", [
    ("AWS Certified Solutions Architect", "AWS Certified Solutions Architect, 2022"),
    ("Сертификат MCSE", "Сертификат MCSE, колледж"),
    ("תעודת MCSE", "תעודת MCSE בתקשורת"),
    ("Diploma in design", "Design diploma"),
])
def test_14_credential_words_from_source_are_not_flagged(source, main):
    assert [i for i in rc.soft_invention_issues(source, main) if "credential" in i["reason"]] == []
