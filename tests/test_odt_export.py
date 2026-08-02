"""
Тесты для _generate_odt() — изолированная проверка генерации ODT.
Cycle O1: только функция генерации, HTTP-роут не тестируется здесь
(роут добавляется отдельным циклом O2, вместе с тестом test client).

Запуск: python -m pytest tests/test_odt_export.py -v
"""
import sys
sys.path.insert(0, '/home/claude/resumeai')

from odf.opendocument import load
from odf.text import P

import app.missing_routes4 as mr


def _all_paragraph_texts(buf):
    """Прочитать ODT обратно и вернуть список текстов всех параграфов."""
    doc = load(buf)
    return [str(p) for p in doc.getElementsByType(P)]


def test_01_generate_odt_roundtrip_no_exception():
    """Базовая генерация не должна падать, файл должен читаться обратно."""
    text = "###ITEM_001###\nJohn Doe\n\n###ITEM_002###\nSoftware Engineer"
    buf = mr._generate_odt(text)
    # load() бросит исключение, если файл повреждён — этого достаточно
    # для проверки структурной валидности .odt
    doc = load(buf)
    assert doc is not None


def test_02_no_item_markers_in_output():
    """Маркеры ###ITEM_NNN### не должны попадать ни в один параграф результата."""
    text = "###ITEM_001###\nSome text here\n\n###ITEM_002###\nMore text"
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    for t in texts:
        assert "###ITEM_" not in t, f"Маркер утёк в текст: {t!r}"


def test_03_latin_text_preserved():
    """Латинский текст должен сохраниться без искажений."""
    text = "###ITEM_001###\nJohn Doe\nSenior Software Engineer"
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    assert "John Doe" in texts
    assert "Senior Software Engineer" in texts


def test_04_hebrew_text_preserved_and_readable():
    """Ивритский текст должен сохраниться и корректно читаться обратно
    (round-trip через odf.opendocument.load, без исключений)."""
    text = "###ITEM_001###\nניסיון תעסוקתי\n\n###ITEM_002###\nמנהל רשת בכיר"
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    assert "ניסיון תעסוקתי" in texts
    assert "מנהל רשת בכיר" in texts


def test_05_mixed_hebrew_latin_no_crash():
    """Смешанный иврит + латиница в одном документе не должен ронять генерацию."""
    text = (
        "###ITEM_001###\nJohn Doe\n\n"
        "###ITEM_002###\nניסיון תעסוקתי\n\n"
        "###ITEM_003###\nPython, Docker, Kubernetes"
    )
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    assert "John Doe" in texts
    assert "ניסיון תעסוקתי" in texts
    assert "Python, Docker, Kubernetes" in texts


def test_06_markdown_asterisks_stripped():
    """Маркдаун-звёздочки (**bold**, *italic*) из improved_resume должны
    убираться — как и в существующем fallback-пути legacy_improve_docx."""
    text = "###ITEM_001###\n**Bold Title**\n\n###ITEM_002###\n*italic note*"
    buf = mr._generate_odt(text)
    texts = _all_paragraph_texts(buf)
    assert "Bold Title" in texts
    assert "italic note" in texts
    for t in texts:
        assert "**" not in t and not t.startswith("*")


def test_07_empty_lines_do_not_crash():
    """Пустые строки (двойной перенос между блоками) не должны ронять генерацию."""
    text = "###ITEM_001###\nLine one\n\n\n###ITEM_002###\nLine two"
    buf = mr._generate_odt(text)
    doc = load(buf)  # не должно бросить исключение
    assert doc is not None
