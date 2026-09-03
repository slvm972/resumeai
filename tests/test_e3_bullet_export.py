"""
Cycle E3 — тесты на дублирование буллет-маркера "•" в экспортёрах ODT и PDF.

RTF намеренно НЕ покрыт: правка _generate_rtf отложена (см. отчёт Цикла E3 —
_rtf_type_wrap получает уже RTF-экранированный текст, где символ "•" не
существует как unicode-символ, только как \\uN? escape-последовательность;
условие "уже начинается с •" там технически невозможно проверить простым
if/return внутри самой функции).

Запуск: python -m pytest tests/test_e3_bullet_export.py -v
"""
import sys, io
sys.path.insert(0, '/home/claude/resumeai')
import importlib
import app.missing_routes4 as mr
importlib.reload(mr)

from odf.opendocument import load as odf_load
from odf.text import P
from odf import teletype

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import PyPDF2

from striprtf.striprtf import rtf_to_text

NL = chr(10)
BULLET = "\u2022"

# ---------------------------------------------------------------------------
# ODT helpers
# ---------------------------------------------------------------------------

def _odt_paragraph_texts(buf):
    """Извлечь текст всех <text:p> из сгенерированного ODT в порядке документа."""
    doc = odf_load(buf)
    texts = []
    for p in doc.text.childNodes:
        qn = getattr(p, 'qname', None)
        if qn and qn[1] == 'p':
            texts.append(teletype.extractText(p))
    return texts


# ---------------------------------------------------------------------------
# PDF helpers — регистрация шрифтов проекта недоступна в тестовом окружении
# (файлы .ttf — production-ассеты, не часть кода). Подменяем
# _ensure_pdf_font_registered на регистрацию всех 4 логических имён шрифтов
# на один локально доступный TTF — тестируется ТОЛЬКО логика построения
# display_line (наличие/отсутствие дублирования буллета), не рендеринг
# конкретных глифов.
# ---------------------------------------------------------------------------

_TEST_FONT_PATH = "/mnt/skills/examples/canvas-design/canvas-fonts/BricolageGrotesque-Regular.ttf"


def _patch_pdf_fonts(monkeypatch):
    def _fake_register():
        mr._pdf_font_registered = True
        for name in (mr._PDF_FONT_NAME, mr._PDF_FONT_NAME_LATIN,
                     mr._PDF_FONT_NAME_ARABIC, mr._PDF_FONT_NAME_CJK):
            try:
                pdfmetrics.registerFont(TTFont(name, _TEST_FONT_PATH))
            except Exception:
                pass
    monkeypatch.setattr(mr, "_ensure_pdf_font_registered", _fake_register)
    monkeypatch.setattr(mr, "_pdf_font_registered", False)


def _pdf_text(buf):
    reader = PyPDF2.PdfReader(io.BytesIO(buf.getvalue()))
    return NL.join(page.extract_text() for page in reader.pages)


# ===========================================================================
# ODT
# ===========================================================================

def test_E3a_odt_bullet_already_present_not_doubled():
    """Вход с текстом, уже содержащим '•' (A2-fallback) + BULLET —
    в результате ровно один '•' перед текстом, не два."""
    text = "###ITEM_001:BULLET###\n\u2022 Впровадив CI/CD"
    buf = mr._generate_odt(text)
    texts = _odt_paragraph_texts(buf)
    assert any(t.strip() == "\u2022 Впровадив CI/CD" for t in texts), texts
    assert not any("\u2022\u2022" in t or "\u2022 \u2022" in t for t in texts), texts


def test_E3b_odt_bullet_regression_marker_still_added():
    """Regression guard: вход БЕЗ '•' в тексте (настоящий Word-список,
    numPr-стиль -> type=BULLET) — маркер по-прежнему добавляется."""
    text = "###ITEM_001:BULLET###\nDeveloped microservices architecture"
    buf = mr._generate_odt(text)
    texts = _odt_paragraph_texts(buf)
    assert any(t.strip() == "\u2022 Developed microservices architecture" for t in texts), texts


# ===========================================================================
# PDF
# ===========================================================================

def test_E3c_pdf_bullet_already_present_not_doubled(monkeypatch):
    """Вход с текстом, уже содержащим '•' (A2-fallback) + BULLET —
    в результате ровно один '•' перед текстом, не два."""
    _patch_pdf_fonts(monkeypatch)
    text = "###ITEM_001:BULLET###\n\u2022 Vprovadyv CI/CD"
    buf = mr._generate_pdf(text)
    extracted = _pdf_text(buf)
    assert "\u2022\u2022" not in extracted.replace(" ", "")
    # Ровно один буллет-символ в извлечённом тексте для этой строки
    assert extracted.count("\u2022") == 1, repr(extracted)


def test_E3d_pdf_bullet_regression_marker_still_added(monkeypatch):
    """Regression guard: вход БЕЗ '•' в тексте (настоящий Word-список,
    numPr-стиль -> type=BULLET) — маркер по-прежнему добавляется."""
    _patch_pdf_fonts(monkeypatch)
    text = "###ITEM_001:BULLET###\nDeveloped microservices architecture"
    buf = mr._generate_pdf(text)
    extracted = _pdf_text(buf)
    assert extracted.count("\u2022") == 1, repr(extracted)


# ===========================================================================
# RTF
# ===========================================================================

def test_E3e_rtf_bullet_already_present_not_doubled():
    """Вход с текстом, уже содержащим '•' (A2-fallback) + BULLET —
    в результате ровно один '•' в извлечённом тексте, не два."""
    text = "###ITEM_001:BULLET###\n\u2022 Впровадив CI/CD"
    rtf_bytes = mr._generate_rtf(text)
    extracted = rtf_to_text(rtf_bytes.decode('utf-8'))
    assert "\u2022\u2022" not in extracted.replace(" ", ""), repr(extracted)
    assert extracted.count("\u2022") == 1, repr(extracted)
    assert "\u2022 Впровадив CI/CD" in extracted, repr(extracted)


def test_E3f_rtf_bullet_regression_marker_still_added():
    """Regression guard: вход БЕЗ '•' в тексте (настоящий Word-список,
    numPr-стиль -> type=BULLET) — маркер по-прежнему добавляется."""
    text = "###ITEM_001:BULLET###\nDeveloped microservices architecture"
    rtf_bytes = mr._generate_rtf(text)
    extracted = rtf_to_text(rtf_bytes.decode('utf-8'))
    assert extracted.count("\u2022") == 1, repr(extracted)
    assert "\u2022 Developed microservices architecture" in extracted, repr(extracted)
