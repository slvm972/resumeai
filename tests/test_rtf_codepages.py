"""
Cycle S3: RTF читается с кодовой страницей ШРИФТА (\\fcharsetN), а не только \\ansicpgN документа.
Раньше иврит из Word-RTF с \\ansicpg1251 превращался в кириллический "мусор".
RTF в тестах собираются кодом, бинарных фикстур нет.
Запуск: FLASK_ENV=testing python -m pytest tests/test_rtf_codepages.py -q
"""
import io
import os
import sys
import uuid

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')

import importlib
import config as config_module
importlib.reload(config_module)

from app import create_app
from app.utils.resume_text import extract_resume_text, _rtf_decode_codepages


def _hx(text, cp):
    """Текст -> последовательность \\'hh в заданной кодовой странице."""
    return ''.join("\\'%02x" % b for b in text.encode(cp))


def _rtf(ansicpg, fonttbl, body, uc=1):
    head = r"{\rtf1\ansi\ansicpg%d\uc%d\deff0{\fonttbl%s}" % (ansicpg, uc, fonttbl)
    return (head + body + "}").encode('latin-1')


_FONTS_RU_HE = (r"{\f0\froman\fcharset204 Times New Roman;}"
                r"{\f46\fswiss\fcharset177 Arial;}"
                r"{\f43\fswiss\fcharset0 Calibri;}")


def test_01_hebrew_font_inside_cp1251_document():
    """Главный случай владельца: \\ansicpg1251, а иврит набран шрифтом fcharset177 (cp1255)."""
    he = "מוסתובוי סלבה"
    ru = "Администратор сети"
    body = r"\pard {\f46 %s}\par {\f0 %s}\par" % (_hx(he, 'cp1255'), _hx(ru, 'cp1251'))
    text = extract_resume_text('cv.rtf', _rtf(1251, _FONTS_RU_HE, body))
    assert he in text and ru in text
    assert not any('\u0400' <= c <= '\u04ff' for c in text.split('\n')[0])   # иврит не стал кириллицей


def test_02_cyrillic_document_without_font_override_unchanged():
    ru = "Ремонт компьютеров, Киев"
    body = r"\pard {\f0 %s}\par" % _hx(ru, 'cp1251')
    assert extract_resume_text('cv.rtf', _rtf(1251, _FONTS_RU_HE, body)) == ru


def test_03_western_document_cp1252_default_font():
    body = r"\pard \f43 Caf%s r%ssum%s\par" % (_hx('é', 'cp1252'), _hx('é', 'cp1252'), _hx('é', 'cp1252'))
    fonts = r"{\f43\fswiss\fcharset0 Calibri;}"
    text = extract_resume_text('cv.rtf', _rtf(1252, fonts, body.replace(r'\f43', r'\f43 ')))
    assert 'Café résumé' in text


def test_04_charset_zero_uses_document_codepage():
    # fcharset0 в документе с ansicpg1251: байты читаются по кодовой странице документа
    body = r"\pard {\f43 %s}\par" % _hx("Привет", 'cp1251')
    assert extract_resume_text('cv.rtf', _rtf(1251, _FONTS_RU_HE, body)) == "Привет"


def test_05_font_changes_inside_groups_are_scoped():
    he = "שלום"
    ru = "Мир"
    # шрифт 46 действует только внутри группы; после неё снова \f0 (кириллица)
    body = r"\pard \f0 {\f46 %s} %s\par" % (_hx(he, 'cp1255'), _hx(ru, 'cp1251'))
    text = extract_resume_text('cv.rtf', _rtf(1251, _FONTS_RU_HE, body))
    assert he in text and ru in text


def test_06_plain_resets_to_default_font():
    ru = "Привет"
    body = r"\pard \f46 abc \plain %s\par" % _hx(ru, 'cp1251')
    text = extract_resume_text('cv.rtf', _rtf(1251, _FONTS_RU_HE, body))   # deff0 -> fcharset204
    assert ru in text


def test_07_arabic_and_greek_charsets():
    fonts = (r"{\f0\fcharset204 A;}{\f1\fcharset178 Arab;}{\f2\fcharset161 Greek;}")
    body = r"\pard {\f1 %s}\par {\f2 %s}\par" % (_hx("مرحبا", 'cp1256'), _hx("Γειά σου", 'cp1253'))
    text = extract_resume_text('cv.rtf', _rtf(1251, fonts, body))
    assert "مرحبا" in text and "Γειά σου" in text


def test_08_multibyte_cjk_run_decoded_as_one_run():
    fonts = r"{\f0\fcharset204 A;}{\f5\fcharset134 SimSun;}"
    body = r"\pard {\f5 %s}\par" % _hx("简历分析", 'cp936')
    assert "简历分析" in extract_resume_text('cv.rtf', _rtf(1251, fonts, body))


def test_09_uc0_documents_do_not_leave_question_marks():
    body = r"\pard {\f46 %s}\par" % _hx("שלום", 'cp1255')
    text = extract_resume_text('cv.rtf', _rtf(1251, _FONTS_RU_HE, body, uc=0))
    assert text == "שלום"


def test_10_existing_unicode_escapes_and_bullets_still_work():
    body = r"\pard Title \u8212? dash\par \pard \bullet  Item one\par"
    text = extract_resume_text('cv.rtf', _rtf(1252, r"{\f0 Arial;}", body))
    assert 'Title — dash' in text and '•' in text and 'Item one' in text


def test_11_hex_in_font_table_name_is_not_converted_or_crashes():
    fonts = (r"{\f0\fcharset204 Times;}{\f34\fcharset128 MS Gothic{\*\falt %s};}"
             % _hx("ＭＳ", 'cp932'))
    body = r"\pard \f0 %s\par" % _hx("Текст", 'cp1251')
    assert extract_resume_text('cv.rtf', _rtf(1251, fonts, body)) == "Текст"


def test_12_decoder_is_idempotent_on_plain_ascii_rtf():
    src = r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}\pard Hello world\par}"
    assert _rtf_decode_codepages(src) == src


def test_13_broken_rtf_still_gives_old_errors():
    with pytest.raises(ValueError) as ei:
        extract_resume_text('cv.rtf', b'not an rtf')
    assert str(ei.value) == "Cannot read RTF file. Please check the file is not corrupted."
    with pytest.raises(ValueError):
        extract_resume_text('cv.rtf', rb"{\rtf1\ansi\deff0 {\fonttbl{\f0 Arial;}}\par}")   # нет текста


def test_14_analyze_route_reads_hebrew_rtf(monkeypatch):
    from unittest.mock import patch
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        from app.services.auth_service import AuthService
        email = 'rtf-cp-%s@example.com' % uuid.uuid4().hex[:10]
        user = AuthService.register(email, 'somepassword123')['user']
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['admin'] = 'admin'
        he = "מוסתובוי סלבה"
        body = r"\pard {\f46 %s}\par {\f0 %s}\par" % (_hx(he, 'cp1255'), _hx("Опыт работы, Киев", 'cp1251'))
        raw = _rtf(1251, _FONTS_RU_HE, body)
        fake = {'success': True, 'analysis': 'ok', 'overall_score': 70, 'summary': 's'}
        with patch('app.services.openrouter_service.OpenRouterService.analyze_resume', return_value=fake) as an:
            resp = client.post('/api/analyze', data={'file': (io.BytesIO(raw), 'cv.rtf')},
                               content_type='multipart/form-data')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        sent = an.call_args[0][1]
        assert he in sent and "Опыт работы, Киев" in sent
