"""
Cycle F2: защита Precise от выдумок (иврит, русский, английский). Без сети.
Запуск: FLASK_ENV=testing python -m pytest tests/test_precise_guard.py -q
"""
import os
import sys

sys.path.insert(0, '/home/claude/resumeai')
os.environ['FLASK_ENV'] = 'testing'

import pytest

from app import missing_routes4 as mr

# Строки образования из ивритского резюме k_h20231_updated (как они лежат в docx)
HE_MCSE = "תקשורת MCSE,  מכללה אשרות,  תל אביב , 9916438MCP ID SP;"
HE_WIN2000 = ("Implementing and Supporting Microsoft Windows 2000 Server, מכללה קאמטכ,  תל אביב ; "
              "שימוש בכלי בינה מלאכותית ורשתות נוירונים")
HE_UNIVERSITY = "אוניברסיטה תקשורת, פקולטה תקשורת אוטומטית, אוקראינה, קייב."


# (а) заморозка образования, курсов и сертификатов -------------------------------------------

@pytest.mark.parametrize("line", [HE_MCSE, HE_WIN2000, HE_UNIVERSITY])
def test_a_hebrew_education_lines_are_frozen(line):
    assert mr._classify_item(line, 20, 40) == "freeze"


@pytest.mark.parametrize("line", [
    "Колледж информационных технологий, специальность: системное администрирование",
    "Київський університет, факультет комп'ютерних наук, спеціальність інженер",
    "Cisco Academy, networking fundamentals, graduated with distinction",
    "Курс «Администрирование Linux», учебный центр, 2019",
    "Certificate in Project Management, PMI Israel branch office",
])
def test_a_other_languages_education_frozen(line):
    assert mr._classify_item(line, 20, 40) == "freeze"


def test_a_course_word_with_achievement_verb_stays_improvable():
    # нет учебного заведения, есть глагол достижения: это работа, а не учёба
    assert mr._classify_item("Developed a Python course for internal use and trained 30 employees", 10, 40) == "improve"


# (б) _validate_block отклоняет домыслы ------------------------------------------------------

def test_b_rejects_added_hebrew_certificate_word():
    ok, reason = mr._validate_block(HE_MCSE, "תעודת MCSE בתקשורת, מכללה אשרות, תל אביב, 9916438MCP ID SP;")
    assert not ok and "Soft invention" in reason


def test_b_rejects_added_russian_certificate_word():
    ok, reason = mr._validate_block("Курс по Excel, учебный центр", "Сертификат по Excel, учебный центр")
    assert not ok and "Soft invention" in reason


def test_b_rejects_added_colleagues_and_routine():
    ok, _ = mr._validate_block("Fixed printers in the office", "Fixed printers for colleagues in the office")
    assert not ok
    ok, _ = mr._validate_block("Installed software", "Performed routine software installation")
    assert not ok


# (в) обычное улучшение по-прежнему принимается ------------------------------------------------

@pytest.mark.parametrize("orig,new", [
    ("Managed a team of engineers", "Led a team of engineers"),
    ("Занимался доставкой грузов по городу", "Доставлял грузы по городу"),
    ("ניהל צוות מהנדסים", "הוביל וניהל צוות מהנדסים"),
])
def test_c_normal_improvements_still_accepted(orig, new):
    assert mr._validate_block(orig, new)[0] is True


def test_c_word_present_in_original_is_not_an_invention():
    ok, _ = mr._validate_block("Training for clients on new software", "Delivered training for clients on new software")
    assert ok
