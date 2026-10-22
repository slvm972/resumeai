"""
Тесты app/rewrite_creative.py (шаг 1 интеграции creative-режима). Без сети.

Часть 1: тесты ядра, перенесённые из tests/test_proto_rewrite.py (замена импорта).
Часть 2: эквивалентность с прототипом (skip, если proto_rewrite не импортируется).
Часть 3: обёртка creative_rewrite (acceptable, ошибки, 429, бюджет времени, ключ).
Запуск: FLASK_ENV=testing python -m pytest tests/test_rewrite_creative.py -q
"""
import json
import sys

import pytest

sys.path.insert(0, '/home/claude/resumeai')

from app import rewrite_creative as pr          # то же имя, что в перенесённых тестах
from app import rewrite_creative as crc


# ===========================================================================
# Часть 1: ядро (перенесено из tests/test_proto_rewrite.py)
# ===========================================================================

class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body

def _ok(text="OK"):
    return _Resp(200, {"choices": [{"message": {"content": text}}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

U2011 = "\u2011"

_RR_SOURCE = "Anna Smith\nanna@example.com\nSales Associate, Acme Store, 2018-2022\nWorked with customers and cash"

_RR_GOOD = ("Anna Smith\nanna@example.com\nSummary\nSales associate who worked with customers and cash.\n"
            "Experience\nSales Associate, Acme Store, 2018-2022\n- Worked with customers and cash\n"
            "DERIVED\n- 4 years = 2018-2022\nCHANGELOG\n- added summary")

_PASS = '{"verdict":"pass","issues":[]}'

_FAIL = '{"verdict":"fail","issues":[{"type":"EMBELLISHMENT","quote":"Worked with customers","reason":"added manner"}]}'

class _Scripted:
    """Замоканный call_groq: ответы переписывания и судьи по очереди."""
    def __init__(self, rewrites, judges):
        self.rewrites, self.judges = list(rewrites), list(judges)
        self.rewrite_messages = []
        self.n = 0

    def __call__(self, api_key, model, messages, **kw):
        self.n += 1
        if messages[0]["content"].startswith("You are a strict fact-checker"):
            queue, text = self.judges, None
        else:
            self.rewrite_messages.append(messages)
            queue = self.rewrites
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"text": text, "usage": {"total_tokens": 10}, "seconds": 0.1}

def _rwr(monkeypatch, scripted, rounds=2):
    monkeypatch.setattr(pr, "call_groq", scripted)
    return pr.rewrite_with_repair(_RR_SOURCE, "m/main", "m/judge", repair_rounds=rounds, pause=0, api_key="k")

def _lost(source, output):
    return [i["quote"] for i in pr.det_issues(source, output) if i["type"] == "LOST_ITEM"]

_LS_SOURCE = ("Иван Петров\nivan@example.com\nНавыки\nответственность, аналитика\n"
              "Опыт работы\nРаботал на складе")

_LS_BAD = ("Иван Петров\nivan@example.com\nПрофиль\nСотрудник склада.\nНавыки\nаналитика\n"
           "Опыт работы\nРаботал на складе\nDERIVED\nCHANGELOG\n- Удалено слово \u00abконтроля\u00bb")

_LS_GOOD = _LS_BAD.replace("Навыки\nаналитика", "Навыки\nответственность, аналитика")


def test_07_parse_retry_after():
    assert pr.parse_retry_after("Rate limit reached. Please try again in 5.67s. Need more?") == 5.67
    assert pr.parse_retry_after("try again in 999s", cap=20.0) == 20.0
    assert pr.parse_retry_after("unrelated error", default=2.0) == 2.0
    assert pr.parse_retry_after(None) == 2.0


def test_03_new_number_not_from_source_is_caught():
    source = "Managed a team of 5 engineers at Acme (2019-2024)"
    output = "PROFESSIONAL SUMMARY\nManaged a team of 5 engineers and cut costs by 40%\nDERIVED\n- nothing = n/a"
    r = pr.check_output(source, output)
    assert "40%" in r["NEW_NUMBERS"]
    assert "5" not in r["NEW_NUMBERS"]


def test_03b_derived_number_goes_to_derived_not_new():
    source = "Sales manager at Acme, 2019-2024. Worked with clients."
    output = (
        "PROFESSIONAL SUMMARY\nSales manager with 5 years of experience.\n"
        "EXPERIENCE\nSales manager at Acme, 2019-2024\n- Worked with clients.\n"
        "DERIVED\n- 5 years of experience = 2019-2024 at one employer\n"
        "CHANGELOG\n- added summary"
    )
    r = pr.check_output(source, output)
    assert r["DERIVED_NUMBERS"] == ["5"]
    assert r["NEW_NUMBERS"] == []


def test_04_russian_source_new_latin_token_caught_source_word_not():
    source = "Работал с Python и вёл переписку с клиентами"
    output = (
        "PROFESSIONAL SUMMARY\nСпециалист по Python и Salesforce.\n"
        "SKILLS\nPython\nDERIVED\n- нет\nCHANGELOG\n- нет"
    )
    r = pr.check_output(source, output)
    assert r["SOURCE_SCRIPT"] == "non_latin"
    assert "Salesforce" in r["NEW_LATIN_TOKENS"]
    assert "Python" not in r["NEW_LATIN_TOKENS"]
    assert "PROFESSIONAL" not in [t.upper() for t in r["NEW_LATIN_TOKENS"]]


def test_05_english_source_new_capitalized_mid_sentence_caught_start_not():
    source = "Worked at Acme in sales. Managed accounts."
    output = "EXPERIENCE\nWorked at Google in sales. Directed accounts."
    r = pr.check_output(source, output)
    assert r["SOURCE_SCRIPT"] == "latin"
    assert r["NEW_CAPITALIZED"] == ["Google"]


def test_06_lost_contact_and_new_contact():
    source = "Anna Smith\nanna.smith@example.com | +1 (415) 555-0192\nhttps://linkedin.com/in/annasmith"
    output = "Anna Smith\n+1 (415) 555-0192\nhttps://linkedin.com/in/annasmith\nother@new.com"
    r = pr.check_output(source, output)
    assert r["LOST_CONTACTS"] == ["anna.smith@example.com"]
    assert r["NEW_CONTACTS"] == ["other@new.com"]


def test_08_forbidden_phrase_counted_only_when_not_in_source():
    source = "Handled customer requests"
    output = "EXPERIENCE\n- Handled requests, resulting in happier customers\n- Passionate about service, resulting in praise"
    r = pr.check_output(source, output)
    assert r["FORBIDDEN_PHRASES"]["resulting in"] == 2
    assert r["FORBIDDEN_PHRASES"]["passionate"] == 1
    assert r["FORBIDDEN_TOTAL"] == 3
    # фраза есть в исходнике -> не считается
    r2 = pr.check_output("Worked on dynamic pricing", "- Worked on dynamic pricing")
    assert "dynamic" not in r2["FORBIDDEN_PHRASES"]


def test_09_derived_changelog_excluded_from_main_checks():
    source = "Worked at Acme"
    output = ("EXPERIENCE\nWorked at Acme\n"
              "DERIVED\n- 7 things = Zebra Corp 2030\nCHANGELOG\n- Rewrote with Salesforce")
    r = pr.check_output(source, output)
    assert r["NEW_NUMBERS"] == []
    assert r["NEW_CAPITALIZED"] == []


def test_10_metrics_length_ratio_and_experience_bullets():
    source = "x" * 100
    output = ("PROFESSIONAL SUMMARY\nShort.\nEXPERIENCE\nJob A\n- one\n- two\nJob B\n- three\n"
              "EDUCATION\n- not counted\nSKILLS\nA, B")
    r = pr.check_output(source, output)
    assert r["EXPERIENCE_BULLETS"] == 3
    assert r["LENGTH_RATIO"] == round(len(pr.split_sections(output)[0]) / 100, 3)
    assert pr.check_output("a", "no sections here")["EXPERIENCE_BULLETS"] is None


def test_11_phone_number_in_text_not_counted_as_new_numbers():
    source = "Contact: +7 912 345 6789, worked 2019-2021"
    output = "Contact: +7 912 345 6789\nWorked 2019-2021"
    r = pr.check_output(source, output)
    assert r["NEW_NUMBERS"] == [] and r["LOST_CONTACTS"] == []


def test_12_call_groq_drops_reasoning_param_on_400():
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(dict(json))
        if "reasoning_format" in json:
            return _Resp(400, {"error": {"message": "unsupported parameter reasoning_format"}})
        return _ok("<think>x</think>RESULT")

    r = pr.call_groq("KEY", "qwen/qwen3-32b", [{"role": "user", "content": "x"}], post=post)
    assert r["text"] == "RESULT"
    assert "reasoning_format" in calls[0] and "reasoning_format" not in calls[1]
    assert r["dropped_params"] == ["reasoning_format"]
    assert r["usage"]["total_tokens"] == 15


def test_14_api_key_is_not_in_error_text_or_payload():
    def post(url, headers=None, json=None, timeout=None):
        assert "SECRET-KEY-123" not in str(json)
        return _Resp(401, {"error": {"message": "invalid api key"}})

    with pytest.raises(RuntimeError) as e:
        pr.call_groq("SECRET-KEY-123", "m", [], post=post)
    assert "SECRET-KEY-123" not in str(e.value)


def test_20_system_v2_has_required_phrases_and_date_substitution():
    import datetime
    for phrase in ("NEVER resolved", "TODAY:", "DERIVED", "CHANGELOG"):
        assert phrase in pr.SYSTEM_PROMPT
    msgs = pr.build_messages("CV")
    system = msgs[0]["content"]
    today = datetime.date.today()
    assert "TODAY: %s (current year %d)" % (today.isoformat(), today.year) in system
    assert "{today}" not in system and "{year}" not in system
    # дата подставляется явно, а фигурные скобки в тексте резюме не ломают сборку
    msgs = pr.build_messages("Skills: {Python}", today="2031-02-03")
    assert "TODAY: 2031-02-03 (current year 2031)" in msgs[0]["content"]
    assert "Skills: {Python}" in msgs[1]["content"]


def test_21_parse_judge_clean_json_and_issue_normalization():
    r = pr.parse_judge('{"verdict":"fail","issues":[{"type":"embellishment","quote":"efficiently","reason":"added"}]}')
    assert r["verdict"] == "fail"
    assert r["issues"] == [{"type": "EMBELLISHMENT", "quote": "efficiently", "reason": "added"}]
    assert pr.parse_judge('{"verdict":"pass","issues":[]}') == {"verdict": "pass", "issues": []}


def test_22_parse_judge_fenced_json_and_think_block():
    fenced = '```json\n{"verdict":"pass","issues":[]}\n```'
    assert pr.parse_judge(fenced)["verdict"] == "pass"
    with_think = '<think>Сначала проверю {"verdict": "fail"} возможно</think>\n{"verdict":"fail","issues":[{"type":"NEW_FACT","quote":"x","reason":"y"}]}'
    r = pr.parse_judge(with_think)
    assert r["verdict"] == "fail" and r["issues"][0]["type"] == "NEW_FACT"
    # текст вокруг JSON и фигурные скобки внутри строк
    r = pr.parse_judge('Вот ответ: {"verdict":"fail","issues":[{"type":"FLUFF","quote":"a {b} c","reason":"r"}]} конец')
    assert r["verdict"] == "fail" and r["issues"][0]["quote"] == "a {b} c"


def test_23_parse_judge_garbage_gives_error():
    r = pr.parse_judge("совсем не JSON " * 40)
    assert r["verdict"] == "error" and r["issues"] == []
    assert len(r["raw"]) == 300
    assert pr.parse_judge("")["verdict"] == "error"
    assert pr.parse_judge('{"foo": 1}')["verdict"] == "error"          # нет verdict
    assert pr.parse_judge('{"verdict": "maybe", "issues": []}')["verdict"] == "error"


def test_24_build_judge_messages_contains_source_rewrite_and_six_types():
    msgs = pr.build_judge_messages("SRC TEXT", "REWRITE TEXT {x}")
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    user = msgs[1]["content"]
    assert user.startswith("SOURCE:\nSRC TEXT\n\nREWRITE:\nREWRITE TEXT {x}")
    assert user.endswith("Check the rewrite.")
    for t in ("NEW_FACT", "OVERSTATED_ROLE", "EMBELLISHMENT", "TENSE_ERROR", "LANGUAGE_ERROR", "FLUFF"):
        assert t in msgs[0]["content"]


def test_25_run_judge_uses_temperature_zero_and_3000_tokens():
    seen = {}

    def fake_call(api_key, model, messages, **kw):
        seen.update(kw, model=model)
        return {"text": '{"verdict":"pass","issues":[]}', "usage": {"total_tokens": 42}, "seconds": 1.5}

    r = pr.run_judge("KEY", "openai/gpt-oss-120b", "s", "r", call=fake_call)
    assert seen["temperature"] == 0 and seen["max_tokens"] == 3000
    assert r["verdict"] == "pass" and r["tokens"] == 42 and r["model"] == "openai/gpt-oss-120b"


def test_26_reasoning_params_by_prefix():
    assert pr._reasoning_params("openai/gpt-oss-120b") == {"reasoning_effort": "low"}
    assert pr._reasoning_params("openai/gpt-oss-20b") == {"reasoning_effort": "low"}
    assert pr._reasoning_params("qwen/qwen3.8-27b") == {"reasoning_format": "hidden"}
    assert pr._reasoning_params("some/other") == {}


def test_27_header_preserved_true_and_false():
    source = "Ирина Кузнецова\nirina@example.com | +7 912 345 6789\nПомощник менеджера, 2019-2024\nВела документацию"
    ok_out = "Ирина Кузнецова\nirina@example.com  |  +7 912 345 6789\nПрофиль\nПомощник менеджера.\nОпыт\n- Вела документацию"
    lost_mail = "Ирина Кузнецова\n+7 912 345 6789\nПрофиль\nПомощник менеджера."
    lost_name = "Профиль\nirina@example.com | +7 912 345 6789"
    assert pr.check_output(source, ok_out)["HEADER_PRESERVED"] is True      # пробелы нормализуются
    assert pr.check_output(source, lost_mail)["HEADER_PRESERVED"] is False
    assert pr.check_output(source, lost_name)["HEADER_PRESERVED"] is False
    assert pr._header_preserved("", "anything") is True


def test_28_derived_changelog_english_headings_recognized_after_header_lines():
    source = "Anna Smith\nWorked at Acme 2019-2024"
    output = ("Anna Smith\nSummary\nSales manager with 5 years of experience.\n"
              "Experience\n- Worked at Acme\nDERIVED\n- 5 years = 2019-2024\nCHANGELOG\n- added summary")
    r = pr.check_output(source, output)
    assert r["DERIVED_NUMBERS"] == ["5"] and r["NEW_NUMBERS"] == []


def test_40_postprocess_nb_hyphen_replaced_unless_in_source():
    assert pr.postprocess("range 7-9", "range 7" + U2011 + "9") == "range 7-9"
    src = "range 7" + U2011 + "9"
    assert pr.postprocess(src, "range 7" + U2011 + "9") == "range 7" + U2011 + "9"   # символ был в source
    # каждый символ отдельно: в source только U+2011, а U+2010 в результате заменяется
    assert pr.postprocess(src, "a\u2010b " + U2011) == "a-b " + U2011


def test_41_postprocess_whitespace_bullets_and_blank_lines():
    src = "Anna Smith\nanna@example.com\nWorked"
    out = "Anna Smith  \nanna@example.com\t\nSummary\n\n\n\n\nText.\n\n\nMore\n\u2022 one\n* two  \n\u2013 three\n**bold** keep"
    r = pr.postprocess(src, out)
    lines = r.split("\n")
    assert all(l == l.rstrip() for l in lines)
    assert "- one" in lines and "- two" in lines and "- three" in lines
    assert "**bold** keep" in lines                       # '**' - не маркер списка
    assert "Text.\n\n\nMore" in r                         # две пустые строки подряд остаются
    assert "Summary\n\nText." in r


def test_42_postprocess_summary_heading_by_language():
    en_src = "Anna Smith\nanna@example.com\nSales manager 2019-2024"
    en_out = "Anna Smith\nanna@example.com\nProfil\nSales manager with 5 years of experience.\nExperience\n- Worked"
    assert pr.postprocess(en_src, en_out).split("\n")[2] == "Summary"
    ru_src = "Иван Петров\nivan@example.com\nМенеджер по продажам"
    ru_out = "Иван Петров\nivan@example.com\nРезюме\nМенеджер по продажам.\nОпыт\n- Вёл переписку"
    assert pr.postprocess(ru_src, ru_out).split("\n")[2] == "Профиль"
    uk_src = "Іван Петренко\nivan@example.com\nМенеджер з продажу"
    assert pr.postprocess(uk_src, "Іван Петренко\nivan@example.com\nРезюме\nМенеджер.").split("\n")[2] == "Профіль"
    he_src = "ישראל ישראלי\nisrael@example.com\nמנהל רשת"
    assert pr.postprocess(he_src, "ישראל ישראלי\nisrael@example.com\nפרופיל\nמנהל רשת.").split("\n")[2] == "סיכום מקצועי"


def test_43_postprocess_unknown_language_or_no_heading_untouched():
    src = "Anna Smith\nanna@example.com\nSales manager"
    out = "Anna Smith\nanna@example.com\nProfil\nSales manager.\nExperience\n- Worked"
    assert pr.postprocess(src, out, lang="xx") == out                       # язык вне таблицы
    assert pr.postprocess(src, out.replace("Profil\n", "")) == out.replace("Profil\n", "")  # заголовка нет
    # строка после шапки - само предложение вступления: не затираем
    sentence = "Anna Smith\nanna@example.com\nSales manager with five years of experience in retail.\nExperience"
    assert pr.postprocess(src, sentence) == sentence
    assert pr.postprocess("", "text") == "text"


def test_44_detect_lang():
    assert pr.detect_lang("Руководил отделом продаж и вёл переписку") == "ru"
    assert pr.detect_lang("Відповідала за облік і звітність, їздила в відрядження") == "uk"
    assert pr.detect_lang("ניהל צוות מהנדסים בחברה גדולה") == "he"
    assert pr.detect_lang("مدير مبيعات ذو خبرة في إدارة الفرق") == "ar"
    assert pr.detect_lang("负责销售管理工作和客户沟通") == "zh"
    assert pr.detect_lang("Managed a sales team of five people and worked with customers every day") == "en"
    assert pr.detect_lang("") == "en"


def test_45_det_issues_fluff():
    out = "Anna Smith\nExperience\n- Skilled in customer service, cash handling, and teamwork"
    r = pr.det_issues("Anna Smith\nWorked with customers and cash", out)
    assert [i["type"] for i in r] == ["FLUFF"] and "Skilled in" in r[0]["quote"]
    assert pr.det_issues("Anna Smith\nSkilled cashier", out) == []              # слово есть в source
    # существительное "Ответственность" из навыков источника не флагуется, прилагательное - флагуется
    noun = pr.det_issues("Навыки: Ответственность, коммуникабельность", "Навыки\nОтветственность, коммуникабельность")
    assert noun == []
    adj = pr.det_issues("Работал продавцом", "Ответственный продавец")
    assert [i["type"] for i in adj] == ["FLUFF"]
    assert pr.det_issues("Работал", "Hard-working and dynamic agent")[0]["type"] == "FLUFF"
    # служебные разделы не проверяются
    assert pr.det_issues("Worked", "Worked\nDERIVED\n- skilled expert = n/a\nCHANGELOG\n- managed") == []


def test_46_det_issues_overstated_role():
    r = pr.det_issues("Работал с накладными", "Оформлял документы на груз и накладные")
    assert [i["type"] for i in r] == ["OVERSTATED_ROLE"] and "Оформлял" in r[0]["quote"]
    # в source есть глагол владения -> проверка пропускается
    assert pr.det_issues("Руководил отделом", "Оформлял документы и осуществлял контроль") == []
    both = pr.det_issues("Работал на складе", "Осуществлял приёмку и контролировал сохранность груза")
    quotes = " | ".join(i["quote"].lower() for i in both)
    assert len(both) == 2 and "осуществлял" in quotes and "контролировал" in quotes
    # цитата начинается и заканчивается на границах слов (не обрывается посреди слова)
    assert all(not q["quote"].startswith("ествлял") and q["quote"].split()[0] in
               "Осуществлял приёмку и контролировал сохранность груза".split() for q in both)
    en = pr.det_issues("Helped in the class", "Managed a class of children")
    assert [i["type"] for i in en] == ["OVERSTATED_ROLE"]


def test_50_repair_clean_first_time(monkeypatch):
    sc = _Scripted([_RR_GOOD], [_PASS])
    r = _rwr(monkeypatch, sc)
    assert r["status"] == "clean" and r["rounds"] == 0 and r["calls"] == 2
    assert r["final_issues"] == [] and r["issues_by_round"] == [[]] and r["judge_error"] is False
    assert len(sc.rewrite_messages) == 1


def test_51_repair_fixes_issue_and_sends_fix_only_request(monkeypatch):
    sc = _Scripted([_RR_GOOD, _RR_GOOD.replace("Worked with customers and cash", "Served customers and handled cash")],
                   [_FAIL, _PASS])
    r = _rwr(monkeypatch, sc)
    assert r["status"] == "repaired" and r["rounds"] == 1 and r["calls"] == 4
    assert r["issues_by_round"][0][0]["quote"] == "Worked with customers" and r["issues_by_round"][1] == []
    repair_msgs = sc.rewrite_messages[1]
    assert [m["role"] for m in repair_msgs] == ["system", "user", "assistant", "user"]
    assert "Fix ONLY" in repair_msgs[-1]["content"]
    assert 'EMBELLISHMENT: "Worked with customers"' in repair_msgs[-1]["content"]
    assert repair_msgs[2]["content"].startswith("Anna Smith")            # предыдущий результат
    assert "Served customers" in r["text"]


def test_52_repair_unresolved_after_all_rounds(monkeypatch):
    sc = _Scripted([_RR_GOOD], [_FAIL])
    r = _rwr(monkeypatch, sc, rounds=2)
    assert r["status"] == "unresolved" and r["rounds"] == 2 and r["calls"] == 6
    assert r["final_issues"] and len(r["issues_by_round"]) == 3
    assert len(sc.rewrite_messages) == 3


def test_53_judge_garbage_marks_judge_error_but_det_issues_still_work(monkeypatch):
    fluffy = _RR_GOOD.replace("- Worked with customers and cash", "- Skilled in customer service and cash handling")
    sc = _Scripted([fluffy, _RR_GOOD], ["совсем не JSON"])
    r = _rwr(monkeypatch, sc)
    assert r["judge_error"] is True
    assert r["issues_by_round"][0][0]["type"] == "FLUFF"                 # только det_issues
    assert r["status"] == "repaired" and r["rounds"] == 1 and r["calls"] == 4


def test_54_repair_rounds_zero_never_repairs(monkeypatch):
    sc = _Scripted([_RR_GOOD], [_FAIL])
    r = _rwr(monkeypatch, sc, rounds=0)
    assert r["status"] == "unresolved" and r["rounds"] == 0 and r["calls"] == 2
    assert len(sc.rewrite_messages) == 1 and r["final_issues"]


def test_60_lost_item_flags_missing_skill_and_keeps_present_ones():
    src = "Иван Петров\nНавыки\nответственность, коммуникабельность\nОпыт работы\nРаботал на складе"
    out = "Иван Петров\nНавыки\nкоммуникабельность\nОпыт работы\nРаботал на складе"
    issues = [i for i in pr.det_issues(src, out) if i["type"] == "LOST_ITEM"]
    assert [i["quote"] for i in issues] == ["ответственность"]
    assert issues[0]["reason"] == "skill listed in the source is missing in the output"
    assert set(issues[0]) == {"type", "quote", "reason"}
    assert _lost(src, src) == []


def test_61_lost_item_prefix_rule_inflected_forms():
    src = "Навыки\nответственность\n\nОпыт работы\nРабота"
    assert _lost(src, "Навыки\nответственный подход\nОпыт работы\nРабота") == []      # max(5, len-4)
    assert _lost(src, "Навыки\nответственно\nОпыт работы\nРабота") == []                 # тоже 11 букв общей части
    assert _lost(src, "Навыки\nорганизованность\nОпыт работы\nРабота") == ["ответственность"]
    uk = "Навички\nвідповідальність"
    assert _lost(uk, "Навички\nвідповідальний") == []
    # короткое однословное (меньше 6 букв) - только точная подстрока
    assert _lost("Skills\nLinux, SQL", "Skills\nLinu") == ["Linux", "SQL"]
    assert _lost("Skills\nPython", "Skills\nPytho") == []


def test_62_lost_item_commas_semicolons_lines_and_markers():
    src = "Skills\nPython, SQL; Docker\n- Git\n\u2022 Linux\n* Excel\nExperience\nWorked"
    out = "Skills\nPython and Docker\nExperience\nWorked"
    assert _lost(src, out) == ["SQL", "Git", "Linux", "Excel"]


def test_63_lost_item_label_with_colon_is_dropped():
    src = "Skills\nTeaching: Classroom management\nMarketing Tools: HubSpot, Salesforce\nExperience\nWorked"
    out = "Skills\nClassroom management\nHubSpot\nExperience\nWorked"
    assert _lost(src, out) == ["Salesforce"]                 # метка 'Teaching' пунктом не считается
    assert pr.skill_items(src) == ["Classroom management", "HubSpot", "Salesforce"]


def test_64_lost_item_no_skills_section_means_no_check():
    src = "Anna Smith\nWorked with customers\nEducation\nSchool"
    assert pr.skill_items(src) == []
    assert _lost(src, "Anna Smith\nSomething else") == []


def test_65_lost_item_section_boundaries_and_headings_ru_uk_en():
    # раздел кончается пустой строкой или следующим заголовком
    src = "Key Skills:\nPython\n\nAwards\nBest employee"
    assert pr.skill_items(src) == ["Python"]
    src2 = "Technical Skills\nPython\nEducation\nMIT"
    assert pr.skill_items(src2) == ["Python"]
    for head, item in [("Ключевые навыки", "аналитика"), ("Навыки", "аналитика"),
                       ("Ключові навички", "аналітика"), ("Навички", "аналітика"),
                       ("Core Competencies", "analytics"), ("SKILLS", "analytics"),
                       ("\u05db\u05d9\u05e9\u05d5\u05e8\u05d9\u05dd", "analytics"),
                       ("\u0627\u0644\u0645\u0647\u0627\u0631\u0627\u062a", "analytics")]:
        assert pr.skill_items("%s\n%s" % (head, item)) == [item], head
    ru_stop = "Навыки\nаналитика\nОбразование\nМГУ"
    assert pr.skill_items(ru_stop) == ["аналитика"]
    uk_stop = "Навички\nаналітика\nДосвід роботи\nСклад"
    assert pr.skill_items(uk_stop) == ["аналітика"]
    # пустая строка сразу после заголовка пропускается
    assert pr.skill_items("Skills\n\nPython\n\nSQL") == ["Python"]


def test_66_lost_item_ignores_derived_and_changelog_sections():
    src = "Skills\nPython"
    out = "Skills\nJava\nDERIVED\n- Python used\nCHANGELOG\n- Python"
    assert _lost(src, out) == ["Python"]


def test_67_repair_message_for_lost_item_asks_to_restore():
    msg = pr.build_repair_message([{"type": "LOST_ITEM", "quote": "ответственность",
                                    "reason": pr.LOST_ITEM_REASON}])
    assert 'LOST_ITEM: "ответственность"' in msg and "put the listed item back" in msg
    assert "put the listed item back" not in pr.build_repair_message(
        [{"type": "FLUFF", "quote": "x", "reason": "y"}])


def test_70_changelog_replaced_line_was_now_and_unchanged_absent():
    src = "Anna Smith\nHelped customers\nSchool"
    out = "Anna Smith\nAssisted customers\nSchool"
    cl = pr.build_changelog(src, out)
    assert "\u00abHelped customers\u00bb \u2192 \u00abAssisted customers\u00bb" in cl
    assert "Anna Smith" not in cl and "School" not in cl
    assert cl.split("\n")[0] == "Changes" and "Rewritten:" in cl


def test_71_changelog_added_and_removed_lines():
    src = "Anna Smith\nOld line\nKeep"
    out = "Anna Smith\nKeep\nNew line"
    cl = pr.build_changelog(src, out)
    assert "- \u00abOld line\u00bb" in cl and "+ \u00abNew line\u00bb" in cl
    assert "Added:" in cl and "Removed:" in cl and "Keep" not in cl
    assert pr.build_changelog(src, src) == ""


def test_72_changelog_labels_ru_uk_en():
    ru = pr.build_changelog("Иван\nПомогал клиентам\nУдалённая", "Иван\nПомогал покупателям\nНовая")
    assert ru.split("\n")[0] == "Изменения" and "Переписано:" in ru
    uk = pr.build_changelog("Іван\nДопомагав клієнтам", "Іван\nДопомагав покупцям\nНова")
    assert uk.split("\n")[0] == "Зміни" and "Додано:" in uk
    uk2 = pr.build_changelog("Іван\nСтарий рядок\nЗалишок", "Іван\nЗалишок")
    assert "Видалено:" in uk2
    ru2 = pr.build_changelog("Иван\nСтарая\nОстаток", "Иван\nОстаток\nНовая строка")
    assert "Удалено:" in ru2 and "Добавлено:" in ru2
    en = pr.build_changelog("Anna Smith\nHelped customers", "Anna Smith\nAssisted customers")
    assert en.split("\n")[0] == "Changes"
    assert pr.split_sections("Text\n" + ru)[2].startswith("Иван") is False      # секция распознаётся как CHANGELOG
    assert pr.split_sections("Text\n" + ru)[0] == "Text"


def test_73_changelog_ignores_list_marker_differences():
    assert pr.build_changelog("Anna Smith\n\u2022 Worked", "Anna Smith\n- Worked") == ""


def test_74_final_text_uses_code_changelog_and_keeps_raw_model_one():
    model = ("Anna Smith\nAssisted customers\nDERIVED\n- 4 years = 2018-2022\n"
             "CHANGELOG\n- Удалено слово \u00abконтроля\u00bb из последней версии")
    final, raw = pr.assemble_final("Anna Smith\nHelped customers", model)
    assert "контроля" not in final and "контроля" in raw
    assert "\u00abHelped customers\u00bb \u2192 \u00abAssisted customers\u00bb" in final
    main, derived, changelog = pr.split_sections(final)
    assert main == "Anna Smith\nAssisted customers" and derived == "- 4 years = 2018-2022"
    assert changelog.startswith("Rewritten:") and "\nChanges\n" in final
    assert final.index("DERIVED") < final.index("Changes")


def test_80_repair_lost_item_goes_to_repair_and_is_fixed(monkeypatch):
    sc = _Scripted([_LS_BAD, _LS_GOOD], [_PASS])
    monkeypatch.setattr(pr, "call_groq", sc)
    r = pr.rewrite_with_repair(_LS_SOURCE, "m/main", "m/judge", repair_rounds=2, pause=0, api_key="k")
    assert r["status"] == "repaired" and r["rounds"] == 1 and r["calls"] == 4
    first = r["issues_by_round"][0]
    assert [i["type"] for i in first] == ["LOST_ITEM"] and first[0]["quote"] == "ответственность"
    assert r["issues_by_round"][1] == []
    repair_msg = sc.rewrite_messages[1][-1]["content"]
    assert 'LOST_ITEM: "ответственность"' in repair_msg
    assert "ответственность" in r["text"]


def test_13_call_groq_retries_429_once_with_wait_capped_at_12():
    waits = []
    seq = [_Resp(429, {"error": {"message": "try again in 1.5s."}}), _ok("DONE")]

    def post(url, headers=None, json=None, timeout=None):
        return seq.pop(0)

    r = pr.call_groq("KEY", "m", [], post=post, sleep=waits.append)
    assert r["text"] == "DONE" and waits == [1.5] and r["retries_429"] == 1

    def always_429(url, headers=None, json=None, timeout=None):
        return _Resp(429, {"error": {"message": "try again in 99s"}})

    waits.clear()
    with pytest.raises(RuntimeError):
        pr.call_groq("KEY", "m", [], post=always_429, sleep=waits.append)
    assert waits == [12.0]                       # один повтор, пауза ограничена 12 с


def test_19_prompt_version_and_system_prompt_contract():
    assert pr.PROMPT_VERSION == "v2.2" and pr.JUDGE_PROMPT_VERSION == "v1"
    assert pr.SYSTEM_PROMPT.startswith("You are an expert resume writer and career coach.")
    assert pr.SYSTEM_PROMPT.rstrip().endswith("what was merged or reworded and why>")
    msgs = pr.build_messages("CV TEXT", today="2026-10-20")
    assert msgs[1]["content"] == "RESUME:\nCV TEXT\n\nRewrite it following the rules above."


def test_19b_module_is_free_of_flask_cli_and_file_io():
    path = pr.__file__
    text = open(path, encoding="utf-8").read()
    for bad in ("import flask", "from flask", "argparse", "os.environ", "open(", "sys.argv"):
        assert bad not in text, bad


# ===========================================================================
# Часть 2: эквивалентность с прототипом
# ===========================================================================

try:
    import proto_rewrite as proto
except ImportError:                                   # pragma: no cover
    proto = None

_EQ_CASES = {
    "ru": ("Иван Петров\nivan@example.com\nОпыт работы\nУправлял складом, оформлял накладные\n"
           "Навыки\nответственность, аналитика",
           "Иван Петров\nivan@example.com\nПрофиль\nОтветственный сотрудник склада.\nОпыт работы\n"
           "- Руководил складом, оформлял накладные\nНавыки\nаналитика\nDERIVED\nCHANGELOG\n- старый журнал"),
    "uk": ("Іван Петренко\n+380 44 123 4567\nДосвід роботи\nПрацював на складі\nНавички\nвідповідальність",
           "Іван Петренко\n+380 44 123 4567\nПрофіль\nСкладський працівник.\nДосвід роботи\n"
           "- Працював на складі\nНавички\nвідповідальний"),
    "en": ("Anna Smith\nanna@example.com\nExperience\nHelped customers with cash\nEducation\nSchool 2010-2014",
           "Anna Smith\nanna@example.com\nSummary\nSales associate with 4 years of cash work.\nExperience\n"
           "- Managed customers and cash\nEducation\nSchool 2010-2014\nDERIVED\n- 4 years\nCHANGELOG\n- x"),
    "skills": ("Tom Lee\ntom@example.com\nSkills\nPython, SQL; Docker\n- Git\nExperience\nWorked",
               "Tom Lee\ntom@example.com\nSkills\nPython and Docker\nExperience\n\u2022 Worked"),
    "labels": ("Eva Roe\neva@example.com\nSkills\nTeaching: Classroom management\n"
               "Marketing Tools: HubSpot, Salesforce\nExperience\nHelped",
               "Eva Roe\neva@example.com\nSkills\nClassroom management\nHubSpot\nExperience\n- Assisted"),
    "u2011": ("Max Fox\nmax@example.com\nExperience\nCross-team work, e-commerce\n",
              "Max Fox\nmax@example.com\nExperience\n- Cross\u2011team work, e\u2011commerce  \n\n\n- Done."),
}


@pytest.mark.parametrize("case", sorted(_EQ_CASES))
def test_90_equivalent_to_prototype(case):
    if proto is None:
        pytest.skip("proto_rewrite is not importable")
    source, output = _EQ_CASES[case]
    assert crc.detect_lang(source) == proto.detect_lang(source)
    post_new, post_old = crc.postprocess(source, output), proto.postprocess(source, output)
    assert post_new == post_old
    assert crc.det_issues(source, post_new) == proto.det_issues(source, post_old)
    main = crc.split_sections(post_new)[0]
    assert crc.build_changelog(source, main) == proto.build_changelog(source, main)
    assert crc.assemble_final(source, post_new) == proto.assemble_final(source, post_old)
    assert crc.check_output(source, post_new) == proto.check_output(source, post_old)


def test_91_prototype_constants_and_prompts_identical():
    if proto is None:
        pytest.skip("proto_rewrite is not importable")
    for name in ("SYSTEM_PROMPT", "JUDGE_SYSTEM", "JUDGE_USER_TEMPLATE", "USER_TEMPLATE",
                 "PROMPT_VERSION", "JUDGE_PROMPT_VERSION", "JUDGE_ISSUE_TYPES"):
        assert getattr(crc, name) == getattr(proto, name), name
    for name in ("_OWN_RE", "_LIST_MARKER_RE", "_NUM_RE", "_EMAIL_RE", "_BULLET_RE", "_SKILL_SPLIT_RE"):
        assert getattr(crc, name).pattern == getattr(proto, name).pattern, name
    for name in ("_FORBIDDEN", "_FLUFF_EN", "_FLUFF_CYR", "SUMMARY_HEADINGS", "_SKILLS_HEADINGS"):
        assert getattr(crc, name) == getattr(proto, name), name


# ===========================================================================
# Часть 3: обёртка creative_rewrite
# ===========================================================================

_KEY = "SECRET-KEY-XYZ-123"
_CHECK_KEYS = {"NEW_NUMBERS", "DERIVED_NUMBERS", "LOST_CONTACTS", "HEADER_PRESERVED", "FORBIDDEN"}
_RESULT_KEYS = {"status", "acceptable", "text", "final_text", "changelog", "rounds", "calls",
                "final_issues", "judge_error", "checks", "error", "prompt_version"}


def _run(monkeypatch, scripted, **kw):
    monkeypatch.setattr(crc, "call_groq", scripted)
    return crc.creative_rewrite(_RR_SOURCE, _KEY, **kw)


def test_100_clean_is_acceptable(monkeypatch):
    r = _run(monkeypatch, _Scripted([_RR_GOOD], [_PASS]))
    assert set(r) == _RESULT_KEYS and set(r["checks"]) == _CHECK_KEYS
    assert r["status"] == "clean" and r["acceptable"] is True and r["error"] is None
    assert r["calls"] == 2 and r["rounds"] == 0 and r["judge_error"] is False
    assert r["prompt_version"] == "v2.2"
    assert r["checks"]["NEW_NUMBERS"] == [] and r["checks"]["LOST_CONTACTS"] == []
    assert r["checks"]["HEADER_PRESERVED"] is True and r["checks"]["FORBIDDEN"] == 0
    assert r["changelog"].startswith("Changes") and r["changelog"] in r["final_text"]
    assert "added summary" not in r["final_text"]               # CHANGELOG модели заменён кодовым


def test_101_repaired_is_acceptable(monkeypatch):
    fixed = _RR_GOOD.replace("Worked with customers and cash", "Served customers and handled cash")
    r = _run(monkeypatch, _Scripted([_RR_GOOD, fixed], [_FAIL, _PASS]))
    assert r["status"] == "repaired" and r["rounds"] == 1 and r["calls"] == 4
    assert r["acceptable"] is True and r["final_issues"] == []


def test_102_unresolved_is_not_acceptable(monkeypatch):
    r = _run(monkeypatch, _Scripted([_RR_GOOD], [_FAIL]))
    assert r["status"] == "unresolved" and r["acceptable"] is False
    assert r["final_issues"] and r["text"]                      # что есть - отдаём


def test_103_clean_but_new_number_is_not_acceptable(monkeypatch):
    bad = _RR_GOOD.replace("Sales associate who worked", "Sales associate with 40% growth who worked")
    r = _run(monkeypatch, _Scripted([bad], [_PASS]))
    assert r["status"] == "clean" and r["checks"]["NEW_NUMBERS"] and r["acceptable"] is False


def test_104_clean_but_lost_contact_or_header_is_not_acceptable(monkeypatch):
    r = _run(monkeypatch, _Scripted([_RR_GOOD.replace("anna@example.com", "contact on request", 2)], [_PASS]))
    assert r["status"] == "clean" and r["checks"]["LOST_CONTACTS"] and r["acceptable"] is False
    assert r["checks"]["HEADER_PRESERVED"] is False


def test_105_is_acceptable_logic_table():
    ok = {"NEW_NUMBERS": [], "DERIVED_NUMBERS": ["4"], "LOST_CONTACTS": [], "HEADER_PRESERVED": True,
          "FORBIDDEN": 0}
    assert crc._is_acceptable("clean", ok) and crc._is_acceptable("repaired", ok)
    assert not crc._is_acceptable("unresolved", ok) and not crc._is_acceptable("error", ok)
    for key, bad in (("NEW_NUMBERS", ["9"]), ("LOST_CONTACTS", ["a@b.co"]),
                     ("HEADER_PRESERVED", False), ("FORBIDDEN", 1)):
        assert not crc._is_acceptable("clean", dict(ok, **{key: bad}))


def test_106_network_exception_gives_error_without_details(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("secret-detail host=api.groq.com key=" + _KEY + " Anna Smith")
    monkeypatch.setattr(crc, "call_groq", boom)
    r = crc.creative_rewrite(_RR_SOURCE, _KEY)                  # исключение наружу не вылетает
    assert r["status"] == "error" and r["acceptable"] is False and r["calls"] == 0
    assert r["error"] and "secret-detail" not in r["error"] and _KEY not in r["error"]
    assert "Anna" not in r["error"] and set(r) == _RESULT_KEYS and set(r["checks"]) == _CHECK_KEYS
    assert _KEY not in json.dumps(r)


def test_107_garbage_responses_and_bad_arguments_never_raise(monkeypatch):
    monkeypatch.setattr(crc, "call_groq", lambda *a, **k: {})          # нет ключа "text"
    assert crc.creative_rewrite(_RR_SOURCE, _KEY)["status"] == "error"
    for src in (None, 123, "", "   \n "):
        r = crc.creative_rewrite(src, _KEY)
        assert r["status"] == "error" and r["acceptable"] is False and r["error"]


def test_108_empty_api_key_is_error_without_network(monkeypatch):
    n = []
    monkeypatch.setattr(crc, "call_groq", lambda *a, **k: n.append(1))
    monkeypatch.setenv("GROQ_API_KEY", "ENV-KEY-MUST-NOT-BE-USED")
    for key in ("", "   ", None):
        r = crc.creative_rewrite(_RR_SOURCE, key)
        assert r["status"] == "error" and r["acceptable"] is False and r["error"]
    assert n == []


def _patch_post(monkeypatch, responses):
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(headers["Authorization"])
        return responses.pop(0)

    monkeypatch.setattr("requests.post", fake_post)
    return seen


def test_109_429_twice_gives_error(monkeypatch):
    rate = lambda: _Resp(429, {"error": {"message": "Please try again in 0.01s."}})
    seen = _patch_post(monkeypatch, [rate(), rate()])
    r = crc.creative_rewrite(_RR_SOURCE, _KEY)
    assert r["status"] == "error" and r["acceptable"] is False and len(seen) == 2
    assert _KEY not in json.dumps(r) and "try again" not in r["error"]


def test_110_429_once_then_success_works(monkeypatch):
    rate = _Resp(429, {"error": {"message": "Please try again in 0.01s."}})
    seen = _patch_post(monkeypatch, [rate, _ok(_RR_GOOD), _ok(_PASS)])
    r = crc.creative_rewrite(_RR_SOURCE, _KEY)
    assert r["status"] == "clean" and r["acceptable"] is True and len(seen) == 3
    assert all(h == "Bearer " + _KEY for h in seen)
    assert _KEY not in json.dumps(r)


def test_111_time_budget_exhausted_before_first_request(monkeypatch):
    sc = _Scripted([_RR_GOOD], [_PASS])
    r = _run(monkeypatch, sc, max_seconds=0)
    assert r["status"] == "unresolved" and r["acceptable"] is False and r["calls"] == 0 == sc.n
    assert r["text"] == "" and r["error"] is None


def test_112_time_budget_exhausted_midway_keeps_what_exists(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(crc, "_now", lambda: clock["t"])
    timeouts = []
    inner = _Scripted([_RR_GOOD], [_PASS])

    def slow(api_key, model, messages, **kw):
        timeouts.append(kw.get("timeout"))
        clock["t"] += 100                                          # переписывание заняло 100 с
        return inner(api_key, model, messages, **kw)

    monkeypatch.setattr(crc, "call_groq", slow)
    r = crc.creative_rewrite(_RR_SOURCE, _KEY, max_seconds=60)
    assert r["status"] == "unresolved" and r["acceptable"] is False
    assert r["calls"] == 1 and inner.n == 1                        # судью уже не спрашивали
    assert r["text"].startswith("Anna Smith") and r["final_text"]
    assert timeouts == [60.0]                                      # таймаут запроса ограничен бюджетом


def test_113_pause_default_is_zero_and_no_sleep(monkeypatch):
    import inspect
    assert inspect.signature(crc.creative_rewrite).parameters["pause"].default == 0.0
    monkeypatch.setattr(crc.time, "sleep", lambda s: pytest.fail("sleep called"))
    r = _run(monkeypatch, _Scripted([_RR_GOOD], [_PASS]))
    assert r["status"] == "clean"


def test_114_key_not_in_any_result_field(monkeypatch):
    for sc in (_Scripted([_RR_GOOD], [_PASS]), _Scripted([_RR_GOOD], [_FAIL])):
        r = _run(monkeypatch, sc)
        assert _KEY not in json.dumps(r, ensure_ascii=False)
    assert crc.creative_rewrite.__defaults__[0] == crc.DEFAULT_MODEL == "openai/gpt-oss-120b"
