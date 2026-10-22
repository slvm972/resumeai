# app/creative_bridge.py
"""
Мост между сервисом и app.rewrite_creative.creative_rewrite (шаг 2а интеграции creative-режима).

Без Flask-импортов. Ничего не меняет в маршрутах: только готовит данные в том же формате,
что отдаёт _run_improve_pipeline (improved_resume, display_text, detected_language, ...).
Функции моста не бросают исключений наружу; в результат не попадают ключ API и детали ошибок.
"""

DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_MAX_SECONDS = 60
MAX_ISSUES = 5
MAX_QUOTE_LEN = 80
_TRUE_VALUES = ("1", "true", "yes", "on")

_ERR_TOO_SHORT = "Resume text too short"
_ERR_READ = "Could not read resume file"
_ERR_FAILED = "Creative mode failed"


def is_creative_v2_enabled(cfg):
    """CREATIVE_V2_ENABLED: "1"/"true"/"yes"/"on" (без учёта регистра) или True -> True, иначе False."""
    try:
        value = cfg.get("CREATIVE_V2_ENABLED")
    except Exception:
        return False
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_VALUES
    return False


def split_creative_output(text):
    """(основной текст, DERIVED, CHANGELOG/Changes/Изменения/Зміни) - через rewrite_creative.split_sections."""
    from app.rewrite_creative import split_sections
    return split_sections(text or "")


def _cfg_str(cfg, key, default):
    value = cfg.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _cfg_seconds(cfg):
    try:
        seconds = int(cfg.get("CREATIVE_MAX_SECONDS") or DEFAULT_MAX_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_MAX_SECONDS
    return seconds if seconds > 0 else DEFAULT_MAX_SECONDS


def _failure(error, status="error", attempted=False):
    """Результат без правок: полный набор ключей, acceptable False, общий текст ошибки."""
    from app.rewrite_creative import PROMPT_VERSION
    return {
        "attempted": attempted, "acceptable": False, "status": status,
        "improved_resume": "", "display_text": "", "derived": "", "changelog": "",
        "detected_language": "English", "rounds": 0, "calls": 0,
        "prompt_version": PROMPT_VERSION, "checks": {}, "issues": [], "error": error,
    }


def _source_text(original_bytes, filename, resume_text_fallback):
    """Тот же способ, что в _run_improve_pipeline: .docx -> _extract_full_text_from_docx,
    прочие файлы -> decode utf-8 (errors=ignore), без файла -> текст из запроса."""
    if original_bytes is not None:
        if filename and filename.lower().endswith(".docx"):
            from app.missing_routes4 import _extract_full_text_from_docx
            return _extract_full_text_from_docx(original_bytes)
        return original_bytes.decode("utf-8", errors="ignore")
    return (resume_text_fallback or "").strip()


def _short_issues(final_issues):
    out = []
    for item in (final_issues or [])[:MAX_ISSUES]:
        out.append({"type": str(item.get("type", "")), "quote": str(item.get("quote", ""))[:MAX_QUOTE_LEN]})
    return out


def creative_improve(original_bytes, filename, resume_text_fallback, cfg):
    """
    Creative-улучшение резюме. Никогда не бросает исключений.
    Ключи результата: attempted, acceptable, status, improved_resume, display_text, derived,
    changelog, detected_language, rounds, calls, prompt_version, checks, issues, error.
    При слишком коротком тексте: {"attempted": False, "acceptable": False, "error": ...}.
    """
    try:
        try:
            text = _source_text(original_bytes, filename, resume_text_fallback)
        except Exception:
            return _failure(_ERR_READ)

        if not text or len(text) < 20:
            return {"attempted": False, "acceptable": False, "error": _ERR_TOO_SHORT}

        from app import rewrite_creative
        r = rewrite_creative.creative_rewrite(
            text, cfg.get("GROQ_API_KEY"),
            model=_cfg_str(cfg, "GROQ_MODEL_EDITOR", DEFAULT_MODEL),
            judge_model=_cfg_str(cfg, "GROQ_MODEL_JUDGE", DEFAULT_MODEL),
            repair_rounds=2, pause=0, max_seconds=_cfg_seconds(cfg),
        )

        main, derived, _model_changelog = split_creative_output(r.get("final_text") or r.get("text") or "")
        try:
            from app.missing_routes4 import _detect_language_simple
            language = _detect_language_simple(text)
        except Exception:
            language = "English"

        return {
            "attempted": True,
            "acceptable": bool(r.get("acceptable")),
            "status": r.get("status", "error"),
            "improved_resume": main,
            "display_text": main,
            "derived": derived,
            "changelog": r.get("changelog") or "",
            "detected_language": language,
            "rounds": r.get("rounds", 0),
            "calls": r.get("calls", 0),
            "prompt_version": r.get("prompt_version"),
            "checks": r.get("checks") or {},
            "issues": _short_issues(r.get("final_issues")),
            "error": r.get("error"),
        }
    except Exception:
        return _failure(_ERR_FAILED, attempted=True)
