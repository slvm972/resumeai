"""
Тесты для Cycle B1 — Rule 12 в _build_standard_system_prompt() против
канцелярита и неестественных формулировок в буллетах Experience.

Bullet-элементы (как и heading/table) идут через _build_standard_system_prompt
(НЕ через _build_improve_prompt/_build_relaxed_prompt — таких функций в
кодовой базе нет, это неточность в исходном ТЗ Cycle B1). Правило 12
применяется в ОБОИХ режимах (precise и creative), т.к. bullet-группа не
имеет отдельного relaxed-варианта промпта по режиму — в отличие от
summary (Cycle S2, только creative) и plain (свой relaxed-промпт всегда).

Запуск: python -m pytest tests/test_experience_bullets.py -v
"""
import sys
import pytest

import app.missing_routes4 as mr


# ===========================================================================
# B1_1 — промпт для буллетов содержит явный запрет на канцелярит
# ===========================================================================

def test_B1_1_bullet_prompt_forbids_bureaucratic_verbs():
    prompt = mr._build_standard_system_prompt(3, "Russian")

    for forbidden in ("осуществлял", "выполнял задачи", "принимал участие", "занимался"):
        assert forbidden in prompt, \
            f"Промпт должен явно упоминать запрещённую форму {forbidden!r} как пример"

    assert "STRICTLY FORBIDDEN" in prompt


def test_B1_1_bullet_prompt_requires_strong_active_verbs():
    prompt = mr._build_standard_system_prompt(3, "Russian")
    for good_example in ("Внедрил", "Настроил", "Оптимизировал", "Провёл", "Сократил"):
        assert good_example in prompt


# ===========================================================================
# B1_2 — промпт содержит корректную замену для типового неестественного
# сочетания "внёс в эксплуатацию" (интерпретация: проверка на уровне
# текста промпта, а не новой fact-validation-логики — задача этого цикла
# ограничена промптом, _validate_block/_FABRICATED_CLAIM_RE не трогались
# и не рассчитаны на проверку идиоматичности глагольных сочетаний).
# ===========================================================================

def test_B1_2_commissioning_phrase_correction_present():
    prompt = mr._build_standard_system_prompt(3, "Russian")
    assert "внёс в эксплуатацию" in prompt, \
        "Промпт должен приводить некорректное сочетание как пример того, что запрещено"
    assert "ввёл в эксплуатацию" in prompt or "внедрил" in prompt.lower() or "Внедрил" in prompt, \
        "Промпт должен явно предлагать корректную замену"


# ===========================================================================
# Regression guard — правило 12 не должно ломать/дублироваться в других
# промптах (plain-relaxed и summary-repositioning в этом цикле НЕ
# трогались; у summary своё отдельное правило 12 с прошлого цикла — это
# ДРУГОЙ текст, не должен совпадать с bullet-версией дословно).
# ===========================================================================

def test_B1_regression_plain_relaxed_prompt_unaffected():
    prompt = mr._build_plain_relaxed_system_prompt(1, "Russian")
    assert "STRICTLY FORBIDDEN" not in prompt, \
        "Rule 12 из этого цикла адресован только _build_standard_system_prompt (bullet/heading/table)"


def test_B1_regression_summary_prompt_keeps_its_own_rule12():
    """summary-промпт получил СВОЁ правило 12 в Cycle S2 — другое по
    тексту (REPOSITION-контекст), но с той же общей идеей. Проверяем,
    что оно не перезаписано и не задвоено новым bullet-специфичным
    текстом этого цикла."""
    prompt = mr._build_summary_repositioning_prompt(1, "Russian")
    assert "REPOSITION" in prompt  # Cycle S2 правило 5 на месте
    assert "выполнял разнообразные задачи" in prompt  # Cycle S2 правило 12 на месте
    assert "STRICTLY FORBIDDEN" not in prompt, \
        "Bullet-специфичная формулировка Cycle B1 не должна была попасть в summary-промпт"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
