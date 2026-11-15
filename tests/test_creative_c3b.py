"""
Cycle C3b: настоящая причина падения Creative попадает в журнал сервера.
creative_rewrite и creative_improve по-прежнему не бросают исключений и возвращают прежние константы;
в журнал пишутся тип и стек, а текст сообщения только у RuntimeError "Groq API ..." (до 300 символов).
Без сети. Запуск: FLASK_ENV=testing python -m pytest tests/test_creative_c3b.py -q
"""
import logging
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')
os.environ.setdefault('FLASK_ENV', 'testing')

from app import creative_bridge, rewrite_creative

SECRET = "СЕКРЕТНЫЙ ТЕКСТ РЕЗЮМЕ Иван Петров ivan@example.com"
KEY = "gsk_SECRET_API_KEY_123456"
TEXT = ("Иван Петров\nivan@example.com | +7 900 111 22 33\n\nОпыт работы\nИнженер, ООО Завод, 2015 - 2020\n"
        "- Руководил группой из 5 человек\n- Внедрил систему мониторинга\n\nОбразование\nМГТУ, 2008 - 2013")
CFG = {'GROQ_API_KEY': KEY, 'CREATIVE_V2_ENABLED': '1'}


# --- creative_rewrite --------------------------------------------------------------------------------

def test_groq_429_message_is_logged_and_result_is_rate_limited(caplog):
    msg = "Groq API 429: Rate limit reached for model `openai/gpt-oss-120b` ... tokens per minute (TPM): Limit 8000"
    with patch('app.rewrite_creative.rewrite_with_repair', side_effect=RuntimeError(msg)):
        with caplog.at_level(logging.ERROR):
            r = rewrite_creative.creative_rewrite(TEXT, KEY)
    assert 'creative_rewrite failed: RuntimeError: Groq API 429: Rate limit reached' in caplog.text
    assert 'tokens per minute' in caplog.text and 'File "' in caplog.text      # сообщение и стек
    assert r['status'] == 'error' and r['error'] == 'Creative rewrite rate limited'
    assert r['calls'] == 0 and r['rounds'] == 0 and r['text'] == ''


def test_groq_message_is_truncated_to_300_chars(caplog):
    long_msg = "Groq API 500: " + "x" * 1000
    with patch('app.rewrite_creative.rewrite_with_repair', side_effect=RuntimeError(long_msg)):
        with caplog.at_level(logging.ERROR):
            rewrite_creative.creative_rewrite(TEXT, KEY)
    assert 'x' * 280 in caplog.text and 'x' * 301 not in caplog.text


def test_other_groq_status_keeps_generic_error_code(caplog):
    with patch('app.rewrite_creative.rewrite_with_repair', side_effect=RuntimeError("Groq API 503: upstream")):
        with caplog.at_level(logging.ERROR):
            r = rewrite_creative.creative_rewrite(TEXT, KEY)
    assert 'Groq API 503: upstream' in caplog.text
    assert r['error'] == 'Creative rewrite failed'


def test_secret_exception_text_is_not_logged_only_type_and_stack(caplog):
    with patch('app.rewrite_creative.rewrite_with_repair', side_effect=Exception(SECRET)):
        with caplog.at_level(logging.ERROR):
            r = rewrite_creative.creative_rewrite(TEXT, KEY)
    assert 'creative_rewrite failed: Exception' in caplog.text and 'File "' in caplog.text
    assert 'СЕКРЕТНЫЙ' not in caplog.text and 'ivan@example.com' not in caplog.text and KEY not in caplog.text
    assert r['error'] == 'Creative rewrite failed' and r['status'] == 'error'


def test_runtime_error_not_from_groq_is_not_logged_with_message(caplog):
    with patch('app.rewrite_creative.rewrite_with_repair', side_effect=RuntimeError(SECRET)):
        with caplog.at_level(logging.ERROR):
            rewrite_creative.creative_rewrite(TEXT, KEY)
    assert 'creative_rewrite failed: RuntimeError' in caplog.text and 'СЕКРЕТНЫЙ' not in caplog.text


def test_result_shape_for_client_is_unchanged_on_generic_failure():
    with patch('app.rewrite_creative.rewrite_with_repair', side_effect=ValueError('boom')):
        r = rewrite_creative.creative_rewrite(TEXT, KEY)
    assert r == rewrite_creative._error_result('Creative rewrite failed')
    assert set(r) == {'status', 'acceptable', 'text', 'final_text', 'changelog', 'rounds', 'calls',
                      'final_issues', 'judge_error', 'checks', 'error', 'prompt_version'}


# --- creative_improve (мост) -------------------------------------------------------------------------

def _docx_bytes():
    import io
    from docx import Document
    d = Document()
    for line in TEXT.split('\n'):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_bridge_logs_type_and_stack_without_message(caplog):
    with patch('app.creative_bridge._source_text', side_effect=None, return_value=TEXT), \
         patch('app.rewrite_creative.creative_rewrite', side_effect=RuntimeError("Groq API 429: " + SECRET)):
        with caplog.at_level(logging.ERROR):
            r = creative_bridge.creative_improve(_docx_bytes(), 'r.docx', None, CFG)
    assert 'creative_improve failed: RuntimeError' in caplog.text and 'File "' in caplog.text
    assert 'СЕКРЕТНЫЙ' not in caplog.text and KEY not in caplog.text and 'Groq API 429' not in caplog.text
    assert r['attempted'] is True and r['status'] == 'error' and r['error'] == 'Creative mode failed'
    assert r['calls'] == 0 and r['acceptable'] is False


def test_bridge_result_for_rate_limit_error_from_rewrite_is_passed_through():
    # creative_rewrite сам не бросает: при 429 возвращает status=error с кодом rate limited, мост отдаёт его как есть
    with patch('app.rewrite_creative.call_groq', side_effect=RuntimeError("Groq API 429: Rate limit")):
        r = creative_bridge.creative_improve(_docx_bytes(), 'r.docx', None, CFG)
    assert r['attempted'] and r['status'] == 'error' and r['acceptable'] is False
    assert r['error'] == 'Creative rewrite rate limited'
