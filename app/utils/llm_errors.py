# app/utils/llm_errors.py
"""
Безопасные для пользователя тексты ошибок LLM-провайдера (Cycle E1).

Раньше сырое сообщение Groq уходило в интерфейс как есть: например
"Request too large for model `qwen/qwen3.8-27b` in organization `org_...` service tier
`on_demand` on output tokens per minute (OTPM): Limit 1000, Requested 4543 ... Upgrade to Dev Tier
at https://console.groq.com/...". Это раскрывает идентификатор организации, названия моделей, тариф
и ссылки на консоль провайдера, а пользователю ничего не объясняет.

Чистые функции, без Flask:
    is_request_too_large(status, message)  запрос не помещается в лимит модели (413 / "Request too large")
    is_rate_limit(status, message)         превышен лимит частоты или токенов (429 / "rate limit" / TPM)
    sanitize_error(message, status=None)   текст, который МОЖНО показать пользователю
Сырой текст нужно писать в лог на стороне сервера, а не в ответ.
"""
import re

MSG_BUSY = "The AI service is busy right now. Please try again in a minute."
MSG_UNAVAILABLE = "The AI service is temporarily unavailable. Please try again later."
MSG_TIMEOUT = "The request took too long. Please try again."
MSG_GENERIC = "The AI service could not process this request. Please try again."

_TOO_LARGE_RE = re.compile(r"request too large|reduce (?:your )?max_tokens|exceed(?:s|ed)? the enforced limit", re.I)
_RATE_RE = re.compile(
    r"rate limit|rate_limit|tokens per (?:minute|day)|requests per (?:minute|day)|\b[TO]?PM\b|\b[TR]PD\b|"
    r"try again in|quota", re.I)
_AUTH_RE = re.compile(r"invalid api key|incorrect api key|unauthorized|authentication|permission|api key|"
                      r"not configured|organization|forbidden", re.I)
_TIMEOUT_RE = re.compile(r"timed? ?out|timeout", re.I)
# признаки того, что текст пришёл от провайдера и не предназначен для пользователя
_PROVIDER_RE = re.compile(
    r"groq|openai|openrouter|anthropic|qwen|llama|gpt-oss|org_[a-z0-9]+|service tier|on_demand|dev tier|"
    r"console\.[a-z]+\.com|https?://|model\s*[`'\"]|max_tokens|api key|\b[TO]?PM\b|\b[TR]PD\b", re.I)
# Cycle E2: внутренние тексты Python/JSON-разбора не предназначены для пользователя
_INTERNAL_RE = re.compile(
    r"Expecting (?:value|property name|'.' delimiter)|line \d+ column \d+|\(char \d+\)|Traceback|Errno|"
    r"object has no attribute|KeyError|NoneType|JSONDecodeError", re.I)


def is_request_too_large(status, message):
    """Запрос не помещается в лимит модели (HTTP 413 или текст "Request too large")."""
    return status == 413 or bool(_TOO_LARGE_RE.search(message or ""))


def is_rate_limit(status, message):
    """Превышен лимит частоты/токенов (HTTP 429 или соответствующий текст)."""
    return status == 429 or bool(_RATE_RE.search(message or ""))


def sanitize_error(message, status=None):
    """
    Текст ошибки, безопасный для показа пользователю.
    Собственные сообщения сервиса ("Empty response from the AI model...", "Cannot read ODT file...")
    проходят без изменений. Сообщения провайдера заменяются короткими нейтральными.
    """
    text = (message or "").strip()
    if not text and status is None:
        return MSG_GENERIC
    if is_request_too_large(status, text) or is_rate_limit(status, text):
        return MSG_BUSY
    if _INTERNAL_RE.search(text):
        return MSG_UNAVAILABLE   # Cycle E2: "Expecting value: line 2 column 21", Traceback, KeyError ...
    if status in (401, 403) or _AUTH_RE.search(text):
        return MSG_UNAVAILABLE
    if _TIMEOUT_RE.search(text) and not _PROVIDER_RE.search(text):
        # собственное понятное сообщение сервиса ("Request timeout. Please try again.") не трогаем
        return text if "please try again" in text.lower() else MSG_TIMEOUT
    if status is not None and status >= 500:
        return MSG_UNAVAILABLE
    if not text or _PROVIDER_RE.search(text):
        return MSG_GENERIC
    return text


def response_error_message(resp, default=""):
    """Сырой текст ошибки из ответа провайдера (для лога и классификации, НЕ для пользователя)."""
    try:
        msg = resp.json().get("error", {}).get("message", "")
        if msg:
            return str(msg)
    except Exception:
        pass
    try:
        return (resp.text or "")[:300] or default
    except Exception:
        return default
