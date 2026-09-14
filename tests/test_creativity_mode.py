"""
Тесты для creativity_mode (Cycle CM1) — добавлен параметр creativity_mode
("precise"/"creative") в _run_improve_pipeline(), управляющий temperature
и similarity_threshold. НЕ дублирует существующий tests/test_missing_routes4.py
(PLANB1-3, F2, CHANGES1-2) — тот файл в рабочей сессии не приложен, поэтому
эти тесты самодостаточны и не полагаются на его фикстуры/хелперы.

Запуск: python -m pytest tests/test_creativity_mode.py -v
"""
import io
import re
import sys
import pytest
from unittest.mock import patch, MagicMock

import app.missing_routes4 as mr


# ===========================================================================
# Юнит-тесты _select_batch_temperature — прямая проверка таблицы значений
# ===========================================================================

def test_CM_unit_precise_defaults_unchanged():
    """precise-режим должен давать ТЕ ЖЕ значения, что были в старом
    TEMP_BY_BLOCK_TYPE до рефакторинга (regression guard)."""
    assert mr._select_batch_temperature("bullet", "attempt_1", "precise") == 0.40
    assert mr._select_batch_temperature("plain", "attempt_1", "precise") == 0.30
    assert mr._select_batch_temperature("heading", "attempt_1", "precise") == 0.15
    assert mr._select_batch_temperature("table", "attempt_1", "precise") == 0.15


def test_CM_unit_creative_values():
    assert mr._select_batch_temperature("bullet", "attempt_1", "creative") == 0.60
    assert mr._select_batch_temperature("plain", "attempt_1", "creative") == 0.50
    assert mr._select_batch_temperature("heading", "attempt_1", "creative") == 0.15
    assert mr._select_batch_temperature("table", "attempt_1", "creative") == 0.15


def test_CM_unit_default_arg_is_precise():
    """Вызов без явного creativity_mode (обратная совместимость со старыми
    вызовами/тестами PLANB1-3) должен давать precise-поведение."""
    assert mr._select_batch_temperature("bullet", "attempt_1") == 0.40
    assert mr._select_batch_temperature("plain", "attempt_1") == 0.30


def test_CM_unit_retry_bump_applies_in_both_modes():
    # attempt_2 bump (+0.15, cap 0.55) поверх base — одинаково в обоих режимах
    assert mr._select_batch_temperature("bullet", "attempt_2", "precise") == pytest.approx(0.55)
    assert mr._select_batch_temperature("bullet", "attempt_2", "creative") == pytest.approx(0.55)  # 0.60+0.15 капается до 0.55
    assert mr._select_batch_temperature("plain", "attempt_2", "creative") == pytest.approx(0.55)   # 0.50+0.15=0.65 -> капается до 0.55
    assert mr._select_batch_temperature("plain", "attempt_2", "precise") == pytest.approx(0.45)    # 0.30+0.15=0.45, не капается


# ===========================================================================
# CREATIVE3 — невалидный creativity_mode тихо откатывается на precise
# ===========================================================================

def test_CREATIVE3_invalid_mode_falls_back_to_precise_in_select_temperature():
    assert mr._select_batch_temperature("bullet", "attempt_1", "ultra") == \
        mr._select_batch_temperature("bullet", "attempt_1", "precise")
    assert mr._select_batch_temperature("plain", "attempt_1", "ultra") == \
        mr._select_batch_temperature("plain", "attempt_1", "precise")


def _make_mixed_docx():
    """Собрать in-memory .docx: 2 hard-frozen поля (имя/контакт) +
    1 bullet-параграф + 1 обычный (plain) параграф, оба с достаточным
    текстом и глаголом-маркером, чтобы classify_item дал strategy=improve."""
    from docx import Document
    doc = Document()
    doc.add_paragraph("John Smith")
    doc.add_paragraph("john.smith@example.com")
    doc.add_paragraph(
        "Managed a team of five engineers across multiple regional offices",
        style="List Bullet",
    )
    doc.add_paragraph(
        "Delivered comprehensive quarterly performance reports for senior stakeholders"
    )
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _mock_groq_response(request_body_text):
    """Построить фейковый ответ Groq: для каждого ###ITEM_NNN###\\n<text>
    блока во входящем user-prompt вернуть тот же текст с добавленным
    в начало словом (genuine word-level change -> проходит Quality Gate
    и Fact Validation без необходимости retry)."""
    out_blocks = []
    for m in re.finditer(r"###ITEM_(\d+)###\n(.*?)(?=\n\n###ITEM_|\n\nOUTPUT|\Z)", request_body_text, re.DOTALL):
        iid, text = m.group(1), m.group(2).strip()
        out_blocks.append(f"###ITEM_{iid}###\n{text}")  # без изменений здесь — переопределяется ниже
    return out_blocks


def _fake_post_factory(captured_calls):
    """Фабрика side_effect для requests.post: логирует temperature каждого
    вызова и возвращает правдоподобный ответ 200 с genuine word change для
    каждого ###ITEM_NNN### блока во входящем user-сообщении."""
    def _fake_post(url, headers=None, json=None, timeout=None):
        captured_calls.append({
            "temperature": json.get("temperature"),
            "user_prompt": json["messages"][1]["content"],
        })
        user_prompt = json["messages"][1]["content"]
        out_parts = []
        for m in re.finditer(
            r"###ITEM_(\d+)###\n(.*?)(?=\n\n###ITEM_|\n\nOUTPUT|\Z)",
            user_prompt, re.DOTALL,
        ):
            iid, text = m.group(1), m.group(2).strip()
            # Genuine word change: добавляем новое слово в начало (позиция
            # предложения 0 -> не считается "invented fact" в _validate_block).
            out_parts.append(f"###ITEM_{iid}###\nSuccessfully {text}")
        content = "\n\n".join(out_parts)

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 42},
        }
        return resp
    return _fake_post


# ===========================================================================
# CREATIVE1 — интеграционный тест: смешанный bullet+plain сценарий,
# проверяем реальные temperature, отправленные в Groq payload
# ===========================================================================

def test_CREATIVE1_mixed_bullet_plain_creative_temperatures():
    docx_bytes = _make_mixed_docx()
    captured = []

    with patch("requests.post", side_effect=_fake_post_factory(captured)):
        result = mr._run_improve_pipeline(
            docx_bytes, "resume.docx", None, "fake-api-key",
            creativity_mode="creative",
        )

    assert result["success"], result.get("error")
    assert result["creativity_mode"] == "creative"

    temps = sorted(c["temperature"] for c in captured)
    # Группы: bullet (item 003) -> 0.60, plain (item 004) -> 0.50.
    # Сравниваем как множество, а не по порядку вызовов — порядок
    # (bullet раньше plain) это деталь реализации attempt_1_groups,
    # не часть тестируемого контракта creativity_mode.
    assert temps == sorted([0.60, 0.50]), f"unexpected temperatures: {temps}"


def test_CREATIVE1_mixed_bullet_plain_precise_temperatures():
    """Regression guard: тот же сценарий, но precise (или дефолт без
    явного creativity_mode) — должен давать прежние температуры."""
    docx_bytes = _make_mixed_docx()
    captured = []

    with patch("requests.post", side_effect=_fake_post_factory(captured)):
        result = mr._run_improve_pipeline(
            docx_bytes, "resume.docx", None, "fake-api-key",
        )  # без creativity_mode -> должен вести себя как раньше (precise)

    assert result["success"], result.get("error")
    assert result["creativity_mode"] == "precise"

    temps = sorted(c["temperature"] for c in captured)
    assert temps == sorted([0.40, 0.30]), f"unexpected temperatures: {temps}"


# ===========================================================================
# CREATIVE3 (продолжение) — невалидный режим на уровне полного pipeline
# ===========================================================================

def test_CREATIVE3_invalid_mode_full_pipeline_does_not_crash():
    docx_bytes = _make_mixed_docx()
    captured = []

    with patch("requests.post", side_effect=_fake_post_factory(captured)):
        result = mr._run_improve_pipeline(
            docx_bytes, "resume.docx", None, "fake-api-key",
            creativity_mode="ultra",
        )

    assert result["success"], result.get("error")
    # Тихий откат на precise — и в возвращаемом поле, и в реально
    # использованных температурах.
    assert result["creativity_mode"] == "precise"
    temps = sorted(c["temperature"] for c in captured)
    assert temps == sorted([0.40, 0.30]), f"unexpected temperatures: {temps}"



# ===========================================================================
# CREATIVE2 — Cycle CM2: threshold теперь реально влияет на accepted/
# needs_retry для случая "нет confirmed genuine word-level change, но
# текст всё же заметно отличается от оригинала (sim <= threshold)".
# ===========================================================================

# Подобранная пара (Cycle CM2): исходный текст (28 слов) с запятыми,
# добавленными к трём словам в середине/конце (punctuation-only —
# не создаёт новых "слов" после strip_punct в _has_genuine_word_change).
# sim=0.9866 — строго в диапазоне (0.95, 0.99]:
#   sim > 0.95  -> precise (threshold=0.95) отклоняет (needs_retry)
#   sim <= 0.99 -> creative (threshold=0.99) принимает (accepted)
_CM2_ORIG = (
    "Delivered cross functional teams to deliver enterprise software projects "
    "on time and within budget while maintaining high quality standards throughout"
)


def _cm2_make_improved():
    words = _CM2_ORIG.split()
    words[5] += ","
    words[10] += ","
    words[15] += ","
    return " ".join(words)


def test_CREATIVE2_threshold_direction_affects_acceptance():
    orig = _CM2_ORIG
    improved = _cm2_make_improved()

    assert not mr._has_genuine_word_change(orig, improved), \
        "Пара текстов должна НЕ содержать genuine word-level change (иначе тест не изолирует threshold-ветку)"
    sim = mr._text_similarity(orig, improved)
    assert 0.95 < sim <= 0.99, f"sim={sim} вне нужного диапазона (0.95, 0.99]"

    ok_precise, sim_p, reason_p = mr._quality_gate(orig, improved, threshold=0.95)
    ok_creative, sim_c, reason_c = mr._quality_gate(orig, improved, threshold=0.99)
    assert ok_precise is False, f"precise должен отклонить (sim={sim_p:.3f} > 0.95): {reason_p}"
    assert ok_creative is True, f"creative должен принять (sim={sim_c:.3f} <= 0.99): {reason_c}"


def _cm2_fake_post(url, headers=None, json=None, timeout=None):
    """Эхо-мок Groq: для каждого ###ITEM_NNN### блока во входящем
    user-prompt возвращает ЗАРАНЕЕ ПОДОБРАННЫЙ improved-текст (без
    дальнейших модификаций) — так тестируется именно поведение
    _quality_gate на конкретной паре (orig, improved), а не случайный
    ответ мока."""
    improved = _cm2_make_improved()
    user_prompt = json["messages"][1]["content"]
    out_parts = []
    for m in re.finditer(
        r"###ITEM_(\d+)###\n(.*?)(?=\n\n###ITEM_|\n\nOUTPUT|\Z)",
        user_prompt, re.DOTALL,
    ):
        iid = m.group(1)
        out_parts.append(f"###ITEM_{iid}###\n{improved}")
    content = "\n\n".join(out_parts)

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": 10},
    }
    return resp


def _cm2_resume_text():
    # item 001/002 — hard-frozen (idx<=1), item 003 — наш подобранный блок.
    # classify_item(_CM2_ORIG, 2, 3) == "improve" (проверено отдельно) —
    # текст достаточно длинный и содержит глагол-маркер "Delivered".
    return "John Smith\njohn.smith@example.com\n" + _CM2_ORIG


def test_CREATIVE2_full_pipeline_precise_needs_retry():
    """precise: та же пара текстов должна давать decision=needs_retry
    на уровне полного _run_improve_pipeline (item "003")."""
    with patch("requests.post", side_effect=_cm2_fake_post):
        result = mr._run_improve_pipeline(
            None, None, _cm2_resume_text(), "fake-api-key",
            creativity_mode="precise",
        )
    assert result["success"], result.get("error")
    assert result["quality_report"]["summary"]["accepted"] == 0
    assert result["quality_report"]["summary"]["needs_retry"] == 1
    # Финальный блок item "003" (после 2 неудачных попыток) должен
    # остаться needs_retry, а не быть тихо принят.
    item_003_final = [b for b in result["quality_report"]["blocks"] if b["id"] == "003"][-1]
    assert item_003_final["decision"] == "needs_retry"


def test_CREATIVE2_full_pipeline_creative_accepted():
    """creative: та же пара текстов должна давать decision=accepted
    на уровне полного _run_improve_pipeline (item "003"), без retry."""
    with patch("requests.post", side_effect=_cm2_fake_post):
        result = mr._run_improve_pipeline(
            None, None, _cm2_resume_text(), "fake-api-key",
            creativity_mode="creative",
        )
    assert result["success"], result.get("error")
    assert result["quality_report"]["summary"]["accepted"] == 1
    assert result["quality_report"]["summary"]["needs_retry"] == 0
    item_003 = [b for b in result["quality_report"]["blocks"] if b["id"] == "003"][-1]
    assert item_003["decision"] == "accepted"
    assert "Delivered enterprise software" not in item_003.get("improved_text", "") or True  # no-op guard, содержимое не искажено
    assert item_003["improved_text"] == _cm2_make_improved()


# ===========================================================================
# QG1-3 — regression guard из исторического tests/test_missing_routes4.py
# (файл в этой сессии не приложен; тексты воспроизведены ДОСЛОВНО из
# документа, показанного ранее в этом диалоге — не реконструированы по
# памяти). Должны сохранить прежние результаты (True/False/False) после
# фикса Cycle CM2, т.к. фикс затрагивает только "тупиковую" ветку,
# которую эти три сценария не используют для итогового решения (QG1 —
# genuine word change bypass; QG2/QG3 — sim > 0.95 всегда, вторая ветка).
# ===========================================================================

def test_QG1_single_strong_synonym_not_blocked_by_high_similarity():
    """Замена одного сильного глагола на синоним не должна блокироваться
    только из-за высокого биграммного сходства (>95%), если
    _has_quality_improvement подтверждает реальное словесное изменение."""
    orig     = "Designed payment flows for Stripe Dashboard used by 1M+ businesses"
    improved = "Architected payment flows for Stripe Dashboard used by 1M+ businesses"
    ok, sim, reason = mr._quality_gate(orig, improved)
    assert ok, f"Confirmed word-change заблокирован Quality Gate: sim={sim:.3f} {reason}"


def test_QG2_truly_unchanged_text_still_needs_retry():
    """Regression guard: полностью идентичный текст всё ещё должен требовать retry."""
    orig = "Managed a team of 5 engineers"
    ok, sim, reason = mr._quality_gate(orig, orig)
    assert not ok, "Регрессия: идентичный текст теперь принимается Quality Gate"


def test_QG3_trivial_punctuation_change_still_needs_retry():
    """Regression guard: добавление только запятой не должно приниматься."""
    orig     = "פיתוח ממשקי משתמש"
    improved = "פיתוח ממשקי משתמש,"
    ok, sim, reason = mr._quality_gate(orig, improved)
    assert not ok, "Регрессия: тривиальное изменение пунктуации теперь принимается"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
