# app/services/openrouter_service.py
# Groq API (бесплатно: 14,400 req/day)

from flask import current_app
import requests
import json
import logging
import re
import time
import langid

from app.services.groq_params import groq_model, post_groq, message_text
from app.utils.llm_errors import sanitize_error, MSG_UNAVAILABLE

def _detect_language(text):
    """
    Определяет язык текста.
    1. Fast-path на Unicode-диапазонах для иврита/арабского (RTL) —
       проверено на живых данных, не трогаем.
    2. Для остальных языков — langid (детерминированный, точный на
       коротком тексте, без тяжёлых зависимостей).
    3. Если langid вернул код не из поддерживаемого набора языков,
       или произошёл сбой библиотеки — fallback на 'en'.
    """
    if not text:
        return 'en'

    total = sum(1 for c in text if c.isalpha())
    if total == 0:
        return 'en'

    # --- Fast-path: RTL детекция по Unicode (не трогаем) ---
    hebrew = sum(1 for c in text if '\u0590' <= c <= '\u05FF')
    arabic = sum(1 for c in text if '\u0600' <= c <= '\u06FF')

    if hebrew / total > 0.15:
        return 'he'
    if arabic / total > 0.15:
        return 'ar'

    # --- langid для остальных языков ---
    try:
        lang_code, _confidence = langid.classify(text)
    except Exception:
        return 'en'

    if lang_code not in LANGUAGE_NAMES:
        return 'en'

    return lang_code

logger = logging.getLogger(__name__)

GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'
# Имена моделей - в настройках (config.py: GROQ_MODEL_ANALYSIS / GROQ_MODEL_EDITOR), см. groq_params.py.


def _extract_retry_after_seconds(error_message, default=2.0, cap=12.0):
    """
    Извлечь время ожидания из сообщения об ошибке Groq вида
    "Please try again in 5.67s." Возвращает секунды, ограниченные [0, cap].
    Если не удалось распарсить — возвращает default.
    """
    m = re.search(r"try again in ([\d.]+)s", error_message or "")
    if not m:
        return default
    try:
        return min(float(m.group(1)), cap)
    except ValueError:
        return default

_TRANSIENT_RETRY_PAUSE = 2.5   # Cycle E2: короткая пауза перед единственным повтором при временном сбое


def _safe_json(response):
    """Тело ответа как dict; {} при ЛЮБОЙ ошибке разбора (страница шлюза, пустое тело, не-JSON). Без исключений."""
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _error_message(response, default=''):
    """Сырой текст ошибки провайдера (для лога и sanitize_error, НЕ для пользователя). Не бросает исключений."""
    err = _safe_json(response).get('error')
    if isinstance(err, dict):
        msg = err.get('message')
    elif isinstance(err, str):
        msg = err
    else:
        msg = None
    return str(msg) if msg else default


def _is_transient(response):
    """Временный сбой Groq: 5xx или статус 200 с не-JSON/пустым телом."""
    status = response.status_code
    return status >= 500 or (status == 200 and not _safe_json(response))


LANGUAGE_NAMES = {
    'he': 'Hebrew',
    'ar': 'Arabic',
    'ru': 'Russian',
    'uk': 'Ukrainian',
    'zh': 'Chinese',
    'en': 'English',
}


def _call_groq_json(resume_text, job_description=None):
    """Анализ резюме через Groq с возвратом структурированного JSON."""
    api_key = current_app.config.get('GROQ_API_KEY')
    if not api_key:
        return {'success': False, 'error': 'GROQ_API_KEY not configured'}

    lang_code = _detect_language(resume_text)
    lang_name = LANGUAGE_NAMES.get(lang_code, 'English')

    job_section = f"\nJOB DESCRIPTION:\n{job_description}" if job_description else ""

    system_prompt = """You are an expert resume analyst and career coach.
Analyze resumes and return ONLY valid JSON. No markdown, no code blocks, just JSON."""

    user_prompt = f"""Analyze this resume and return a JSON object with exactly these fields.
Respond in {lang_name} language (same language as the resume).

RESUME:
{resume_text}{job_section}

Return ONLY this JSON structure (no other text):
{{
  "overall_score": <number 0-100>,
  "ats_score": <number 0-100, ATS optimization score>,
  "formatting": <number 0-100, formatting score>,
  "content": <number 0-100, content quality score>,
  "summary": "<2-3 sentence overall assessment in {lang_name}>",
  "strengths": ["<strength 1 in {lang_name}>", "<strength 2 in {lang_name}>", "<strength 3 in {lang_name}>"],
  "improvements": ["<improvement 1 in {lang_name}>", "<improvement 2 in {lang_name}>", "<improvement 3 in {lang_name}>"],
  "key_skills": ["<skill 1>", "<skill 2>", "<skill 3>", "<skill 4>", "<skill 5>"]
}}

Base scores on actual resume quality. Be honest and constructive."""

    try:
        groq_headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
        groq_payload = {
            'model': groq_model('GROQ_MODEL_ANALYSIS'),
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.3,
            'max_tokens': 3000,   # запас на рассуждение "думающих" моделей
        }
        response = post_groq(requests.post, GROQ_API_URL, groq_headers, groq_payload, 60)

        if response.status_code == 429:
            # Groq подсказывает точное время ожидания в тексте ошибки —
            # используем его вместо мгновенного отказа.
            err_msg = _error_message(response)
            wait_s = _extract_retry_after_seconds(err_msg)
            time.sleep(wait_s)
            response = post_groq(requests.post, GROQ_API_URL, groq_headers, groq_payload, 60)

        # Cycle E2: одна повторная попытка при временном сбое (5xx или не-JSON тело).
        # Всего запросов на анализ не больше трёх: первый, повтор после 429, повтор после сбоя.
        if _is_transient(response):
            logger.warning("Groq analysis transient failure (status %s), retrying once", response.status_code)
            time.sleep(_TRANSIENT_RETRY_PAUSE)
            response = post_groq(requests.post, GROQ_API_URL, groq_headers, groq_payload, 60)

        if response.status_code != 200:
            error = _error_message(response, f'API error {response.status_code}')
            logger.warning("Groq analysis error (status %s): %s", response.status_code, str(error)[:500])
            return {'success': False, 'error': sanitize_error(error, response.status_code)}   # Cycle E1

        data = _safe_json(response)
        if not data:
            logger.warning("Groq analysis: status 200 but body is not valid JSON (len=%s)", len(getattr(response, 'text', '') or ''))
            return {'success': False, 'error': MSG_UNAVAILABLE}
        text = message_text(response)   # без <think>...</think>
        tokens = data.get('usage', {}).get('total_tokens', 0)
        if not text:
            logger.warning("Groq returned empty content for analysis")
            return {'success': False, 'error': 'Empty response from the AI model. Please try again.'}

        # Очистить от markdown
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()

        try:
            result = json.loads(text)
            return {'success': True, 'data': result, 'tokens': tokens, 'lang': lang_code}
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                result = json.loads(match.group())
                return {'success': True, 'data': result, 'tokens': tokens, 'lang': lang_code}

            logger.warning("Could not parse JSON from Groq response, using fallback")
            return {
                'success': True,
                'data': {
                    'overall_score': 65,
                    'ats_score': 60,
                    'formatting': 65,
                    'content': 65,
                    'summary': text[:500],
                    'strengths': ['Resume received for analysis'],
                    'improvements': ['Please try again for detailed analysis'],
                    'key_skills': [],
                },
                'tokens': tokens,
                'lang': lang_code,
            }

    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Request timeout. Please try again.'}
    except Exception as e:
        logger.error(f"Groq error: {str(e)}")
        return {'success': False, 'error': sanitize_error(str(e))}   # Cycle E1


def _call_groq_text(prompt):
    """Простой текстовый запрос к Groq."""
    api_key = current_app.config.get('GROQ_API_KEY')
    if not api_key:
        return {'success': False, 'error': 'GROQ_API_KEY not configured'}

    try:
        groq_payload = {
            'model': groq_model('GROQ_MODEL_EDITOR'),
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.5,
            'max_tokens': 6000,   # запас на рассуждение "думающих" моделей
        }
        response = post_groq(
            requests.post,
            GROQ_API_URL,
            {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            groq_payload,
            60,
        )

        if response.status_code == 200:
            data = _safe_json(response)
            if not data:
                logger.warning("Groq text: status 200 but body is not valid JSON")
                return {'success': False, 'error': MSG_UNAVAILABLE}
            text = message_text(response)   # без <think>...</think>
            if not text:
                return {'success': False, 'error': 'Empty response from the AI model. Please try again.'}
            return {
                'success': True,
                'text': text,
                'tokens': data.get('usage', {}).get('total_tokens', 0),
            }

        error = _error_message(response, f'Error {response.status_code}')
        logger.warning("Groq text error (status %s): %s", response.status_code, str(error)[:500])
        return {'success': False, 'error': sanitize_error(error, response.status_code)}   # Cycle E1

    except Exception as e:
        logger.error(f"Groq text error: {str(e)}")
        return {'success': False, 'error': sanitize_error(str(e))}   # Cycle E1


class OpenRouterService:

    @staticmethod
    def get_user_api_key(user):
        return {'has_custom_key': False, 'key': None, 'use_server_key': True}

    @staticmethod
    def analyze_resume(user, resume_text, job_description=None):
        """Анализ резюме — возвращает структурированные данные."""
        result = _call_groq_json(resume_text, job_description)

        if result['success']:
            data = result['data']
            lang = result.get('lang', 'en')

            analysis_text = f"""**Overall Assessment:**
{data.get('summary', '')}

**Scores:**
- Overall: {data.get('overall_score', 0)}/100
- ATS: {data.get('ats_score', 0)}/100
- Formatting: {data.get('formatting', 0)}/100
- Content: {data.get('content', 0)}/100

**Strengths:**
{chr(10).join([f'• {s}' for s in data.get('strengths', [])])}

**Areas for Improvement:**
{chr(10).join([f'• {i}' for i in data.get('improvements', [])])}"""

            return {
                'success': True,
                'analysis': analysis_text,
                'overall_score': data.get('overall_score', 0),
                'ats_score': data.get('ats_score', 0),
                'formatting': data.get('formatting', 0),
                'content': data.get('content', 0),
                'summary': data.get('summary', ''),
                'strengths': data.get('strengths', []),
                'improvements': data.get('improvements', []),
                'key_skills': data.get('key_skills', []),
                'detected_language': lang,
                'tokens_used': result['tokens'],
                'key_used': 'server',
            }

        return {'success': False, 'error': result['error']}

    @staticmethod
    def improve_resume(user, resume_text, improvement_type='both'):
        """
        Улучшение резюме с сохранением всех оригинальных данных:
        имён, дат, мест работы, учёбы, компаний.
        Ответ на языке оригинала.
        """
        lang_code = _detect_language(resume_text)
        lang_name = LANGUAGE_NAMES.get(lang_code, 'English')

        prompt = f"""You are a professional resume writer and career coach.

IMPORTANT RULES:
1. Write the improved resume in {lang_name} language (same as the original).
2. PRESERVE ALL specific data from the original: names, dates, company names, 
   job titles, locations, universities, degrees, phone numbers, emails, URLs.
3. Only improve: wording, action verbs, sentence structure, formatting, impact.
4. Do NOT invent or add any new facts not present in the original.
5. Return ONLY the improved resume text — no explanations, no comments.

ORIGINAL RESUME:
{resume_text}

IMPROVED RESUME:"""

        result = _call_groq_text(prompt)
        if result['success']:
            return {
                'success': True,
                'improved_resume': result['text'],
                'original_language': lang_code,
                'tokens_used': result['tokens'],
            }
        return {'success': False, 'error': result['error']}

    @staticmethod
    def make_request(user, messages, model=None, temperature=0.7, max_tokens=2000):
        prompt = '\n'.join([m.get('content', '') for m in messages if m.get('role') == 'user'])
        result = _call_groq_text(prompt)
        if result['success']:
            return {
                'success': True,
                'data': {'choices': [{'message': {'content': result['text']}}]},
                'tokens_used': result['tokens'],
                'key_used': 'server',
                'error': None,
            }
        return {'success': False, 'data': None, 'error': result['error'], 'tokens_used': 0, 'key_used': None}
