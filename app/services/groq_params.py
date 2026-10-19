# app/services/groq_params.py
"""
Общие помощники для обращений к Groq (одно место вместо копий в
openrouter_service.py, missing_routes4.py и resume_guardrail.py):

  groq_model(key)              имя модели из app.config (env-переменная -> config.py);
                               вне контекста приложения - из окружения/по умолчанию
  groq_extra_params(model)     доп. параметры "думающих" моделей (скрыть рассуждения)
  strip_think(text)            убрать блоки <think>...</think> из ответа
  post_groq(post, ...)         POST с extra params; на 400 ОДИН повтор без них
  message_text(resp)           текст ответа без <think>; пустая строка, если пусто

Логику 429 (ожидание "try again in Xs", переключение на запасную модель) эти
функции НЕ затрагивают - она остаётся в вызывающем коде.
"""
import os
import re

# Значения по умолчанию - те же, что в config.py (там они берутся из env).
DEFAULT_MODELS = {
    "GROQ_MODEL_ANALYSIS": "openai/gpt-oss-120b",
    "GROQ_MODEL_EDITOR": "openai/gpt-oss-120b",
    "GROQ_MODEL_FALLBACK": "qwen/qwen3.8-27b",
    "GROQ_MODEL_JUDGE": "openai/gpt-oss-120b",
}


def groq_model(key):
    """Имя модели по ключу настройки (GROQ_MODEL_ANALYSIS/EDITOR/FALLBACK/JUDGE)."""
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            value = current_app.config.get(key)
            if value:
                return value
    except Exception:
        pass
    return os.environ.get(key) or DEFAULT_MODELS[key]


def groq_extra_params(model):
    """Параметры, скрывающие рассуждения: gpt-oss -> reasoning_effort, qwen3 -> reasoning_format."""
    name = model or ""
    if name.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    if name.startswith("qwen/qwen3"):
        return {"reasoning_format": "hidden"}
    return {}


def strip_think(text):
    """Убрать блоки <think>...</think> (и оборванный/одиночный тег) из ответа модели."""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    if re.search(r"</think>", text, re.I):
        text = re.split(r"</think>", text, flags=re.I)[-1]
    m = re.search(r"<think>", text, re.I)
    if m:
        text = text[:m.start()]
    return text.strip()


def post_groq(post, url, headers, payload, timeout):
    """
    POST к Groq с extra params для модели из payload["model"] (payload не
    изменяется: при переключении модели extra params пересчитываются). Если
    Groq отвечает 400 (параметр не поддерживается) - ОДИН раз повторяем запрос
    без extra params.
    """
    body = dict(payload)
    extras = groq_extra_params(body.get("model"))
    body.update(extras)
    resp = post(url, headers=headers, json=body, timeout=timeout)
    if extras and resp.status_code == 400:
        resp = post(url, headers=headers, json=dict(payload), timeout=timeout)
    return resp


def message_text(resp):
    """Текст ответа (choices[0].message.content) без <think>; "" если пусто/нет."""
    try:
        content = resp.json()["choices"][0]["message"].get("content")
    except Exception:
        return ""
    return strip_think(content or "")
