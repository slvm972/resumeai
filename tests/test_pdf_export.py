"""
Тесты для _generate_pdf() — изолированная проверка генерации PDF.
Cycle P1: только функция генерации (без Flask-роута, без фронтенда).

Запуск: python -m pytest tests/test_pdf_export.py -v

ВАЖНО — эмпирическая находка (см. отчёт цикла P1):
PyPDF2.extract_text() не гарантирует сохранение логического порядка слов
в строке, если строка смешивает иврит с цифрами/латиницей (RTL+LTR в одной
физической строке). Для ЧИСТОГО ивритского текста (без смешения) извлечение
оказалось точным и детерминированным при многократном прогоне — на этом
построена строгая проверка (test_04). Для смешанного контента строгую
проверку текста не делаем принципиально — только структурную валидность
(test_05), чтобы не притягивать несуществующую гарантию силой.
"""
import io
import os
import sys

sys.path.insert(0, '/home/claude/resumeai')

import pytest
import PyPDF2

import app.missing_routes4 as mr


def _extract(buf):
    reader = PyPDF2.PdfReader(buf)
    return reader, "\n".join(p.extract_text() or "" for p in reader.pages)


# ---------------------------------------------------------------------------
# Латинский текст — строгая проверка точного совпадения
# ---------------------------------------------------------------------------

def test_01_latin_basic_no_exception_and_readable():
    """Базовая генерация с латинским текстом не должна падать, PDF должен
    открываться и содержать ожидаемые слова."""
    text = "###ITEM_001###\nJohn Doe\nSoftware Engineer\n\n###ITEM_002###\nManaged a team of 5 engineers at Acme Corp."
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    assert "John Doe" in extracted
    assert "Software Engineer" in extracted
    assert "Managed a team of 5 engineers at Acme Corp." in extracted


def test_02_item_markers_removed():
    """Маркеры ###ITEM_NNN### не должны попадать в извлечённый текст."""
    text = "###ITEM_001###\nSome text here\n\n###ITEM_002###\nMore text"
    buf = mr._generate_pdf(text)
    _, extracted = _extract(buf)
    assert "###ITEM_" not in extracted


def test_03_markdown_stripped():
    """Markdown-звёздочки должны быть убраны, как и в _generate_odt."""
    text = "###ITEM_001###\n**Bold Title**\n\n###ITEM_002###\n*italic-ish note*"
    buf = mr._generate_pdf(text)
    _, extracted = _extract(buf)
    assert "**" not in extracted
    assert "Bold Title" in extracted
    assert "italic-ish note" in extracted


# ---------------------------------------------------------------------------
# Иврит — чистые (без смешения с цифрами/латиницей) строки: точная проверка
# ---------------------------------------------------------------------------

def test_04_pure_hebrew_exact_roundtrip():
    """
    Чистый ивритский текст (без смешения с цифрами/латиницей в одной строке)
    извлекается PyPDF2 в правильном логическом порядке слов и символов —
    эмпирически подтверждено детерминированным повтором (3x идентичный
    результат) при написании этого теста. Строгая проверка точного
    совпадения оправдана только для этого случая.
    """
    text = "###ITEM_001###\nישראל ישראלי מהנדס תוכנה בכיר\n\n###ITEM_002###\nניסיון תעסוקתי בחברת אקמי"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    assert extracted.strip() == "ישראל ישראלי מהנדס תוכנה בכיר\nניסיון תעסוקתי בחברת אקמי"


def test_04b_pure_hebrew_deterministic_across_runs():
    """Regression guard: повторная генерация того же текста даёт identical
    extract_text() — важно, т.к. bidi-перестановка теоретически могла
    зависеть от состояния (не должна, но проверяем явно)."""
    text = "###ITEM_001###\nניהול פרויקטים ופיתוח תוכנה"
    results = set()
    for _ in range(3):
        buf = mr._generate_pdf(text)
        _, extracted = _extract(buf)
        results.add(extracted.strip())
    assert len(results) == 1, f"Недетерминированное извлечение: {results}"


# ---------------------------------------------------------------------------
# Смешанный контент (иврит + цифры/латиница в одной строке) —
# ТОЛЬКО структурная валидность, без строгой проверки порядка слов.
# ---------------------------------------------------------------------------

def test_05_mixed_hebrew_and_digits_structurally_valid():
    """
    Смешанная RTL+LTR строка (иврит + цифра) — PDF должен генерироваться без
    исключений и оставаться структурно валидным (открывается, есть текст).
    Порядок слов в строке НЕ проверяем строго — эмпирически он может
    отличаться от логического (см. docstring модуля и отчёт цикла P1).
    """
    text = "###ITEM_001###\nניהל צוות של 5 מהנדסים בחברת אקמי"
    buf = mr._generate_pdf(text)
    reader, extracted = _extract(buf)
    assert len(reader.pages) == 1
    assert extracted.strip() != ""
    # Отдельные ивритские слова (не смежные с цифрой) всё равно должны
    # встречаться как читаемые подстроки, даже если порядок слов в строке иной.
    assert "מהנדסים" in extracted
    assert "ניהל" in extracted


# ---------------------------------------------------------------------------
# Перенос длинных строк
# ---------------------------------------------------------------------------

def test_06_long_line_wraps_into_multiple_lines():
    """Строка длиннее ширины страницы должна быть перенесена на несколько
    физических строк — проверяем через подсчёт вызовов drawString (мок),
    не полагаясь на количество '\\n' в extract_text() (ненадёжно)."""
    long_line = "word " * 80  # заведомо шире доступной ширины A4 при 11pt
    text = f"###ITEM_001###\n{long_line.strip()}"

    calls = {"count": 0}
    from reportlab.pdfgen import canvas as pdf_canvas
    original_draw_string = pdf_canvas.Canvas.drawString

    def _counting_draw_string(self, *args, **kwargs):
        calls["count"] += 1
        return original_draw_string(self, *args, **kwargs)

    pdf_canvas.Canvas.drawString = _counting_draw_string
    try:
        buf = mr._generate_pdf(text)
    finally:
        pdf_canvas.Canvas.drawString = original_draw_string

    assert calls["count"] > 1, f"Длинная строка не была перенесена: только {calls['count']} вызов(ов) drawString"

    reader, _ = _extract(buf)
    assert len(reader.pages) == 1


def test_07_short_line_not_wrapped():
    """Regression guard: короткая строка не должна разбиваться на несколько
    вызовов drawString."""
    calls = {"count": 0}
    from reportlab.pdfgen import canvas as pdf_canvas
    original_draw_string = pdf_canvas.Canvas.drawString

    def _counting_draw_string(self, *args, **kwargs):
        calls["count"] += 1
        return original_draw_string(self, *args, **kwargs)

    pdf_canvas.Canvas.drawString = _counting_draw_string
    try:
        buf = mr._generate_pdf("###ITEM_001###\nShort line")
    finally:
        pdf_canvas.Canvas.drawString = original_draw_string

    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# Пагинация
# ---------------------------------------------------------------------------

def test_08_long_text_spans_multiple_pages():
    """Текст с большим количеством строк должен переноситься на несколько
    страниц PDF."""
    lines = [f"Line number {i} of the resume content section" for i in range(120)]
    text = "###ITEM_001###\n" + "\n".join(lines)
    buf = mr._generate_pdf(text)
    reader, _ = _extract(buf)
    assert len(reader.pages) > 1


def test_09_empty_text_does_not_crash():
    """Пустой/пробельный ввод не должен вызывать исключение."""
    buf = mr._generate_pdf("###ITEM_001###\n\n\n")
    reader, _ = _extract(buf)
    assert len(reader.pages) >= 1


# ---------------------------------------------------------------------------
# Шрифт
# ---------------------------------------------------------------------------

def test_10_font_file_exists_and_is_static_ttf():
    """Регистрируемый файл шрифта должен существовать и быть обычным
    (не variable) TTF — variable-инстансы имеют таблицу 'fvar', которой
    у static-шрифта быть не должно."""
    assert os.path.exists(mr._PDF_FONT_PATH), f"Font file missing: {mr._PDF_FONT_PATH}"
    from fontTools.ttLib import TTFont as FTFont
    ft = FTFont(mr._PDF_FONT_PATH)
    assert "fvar" not in ft, "Шрифт содержит таблицу fvar — это variable font, а не static instance"
