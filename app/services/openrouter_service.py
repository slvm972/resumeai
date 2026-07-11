# app/services/openrouter_service.py
# Groq API (бесплатно: 14,400 req/day)

from flask import current_app
import requests
import json
import logging
import re
import time
import langid

logger = logging.getLogger(__name__)

GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'
GROQ_MODEL = 'llama-3.1-8b-instant'


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


def _detect_language(text):
    """Определяет язык текста по Unicode символам."""
    if not text:
        return 'en'
    total = sum(1 for c in text if c.isalpha())
    if total == 0:
        return 'en'
    hebrew  = sum(1 for c in text if '\u0590' <= c <= '\u05FF')
    arabic  = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    if hebrew / total > 0.15:
        return 'he'
    if arabic / total > 0.15:
        return 'ar'
    if cyrillic / total > 0.3:
        # Украинские буквы-маркеры, отсутствующие в русском алфавите:
        # і/І, ї/Ї, є/Є, ґ/Ґ
        ukrainian_markers = sum(1 for c in text if c in 'іїєґІЇЄҐ')
        if ukrainian_markers > 0:
            return 'uk'
        return 'ru'
    if chinese / total > 0.1:
        return 'zh'
    return 'en'


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
        response = requests.post(
            GROQ_API_URL,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': GROQ_MODEL,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                'temperature': 0.3,
                'max_tokens': 1500,
            },
            timeout=60,
        )

        if response.status_code == 429:
            # Groq подсказывает точное время ожидания в тексте ошибки —
            # используем его вместо мгновенного отказа.
            err_msg = response.json().get('error', {}).get('message', '')
            wait_s = _extract_retry_after_seconds(err_msg)
            time.sleep(wait_s)
            response = requests.post(
                GROQ_API_URL,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': GROQ_MODEL,
                    'messages': [
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    'temperature': 0.3,
                    'max_tokens': 1500,
                },
                timeout=60,
            )

        if response.status_code != 200:
            error = response.json().get('error', {}).get('message', f'API error {response.status_code}')
            return {'success': False, 'error': error}

        data = response.json()
        text = data['choices'][0]['message']['content'].strip()
        tokens = data.get('usage', {}).get('total_tokens', 0)

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
        return {'success': False, 'error': str(e)}


def _call_groq_text(prompt):
    """Простой текстовый запрос к Groq."""
    api_key = current_app.config.get('GROQ_API_KEY')
    if not api_key:
        return {'success': False, 'error': 'GROQ_API_KEY not configured'}

    try:
        response = requests.post(
            GROQ_API_URL,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': GROQ_MODEL,
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.5,
                'max_tokens': 3000,
            },
            timeout=60,
        )

        if response.status_code == 200:
            data = response.json()
            return {
                'success': True,
                'text': data['choices'][0]['message']['content'],
                'tokens': data.get('usage', {}).get('total_tokens', 0),
            }

        error = response.json().get('error', {}).get('message', f'Error {response.status_code}')
        return {'success': False, 'error': error}

    except Exception as e:
        return {'success': False, 'error': str(e)}


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
