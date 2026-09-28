# app/services/resume_guardrail.py
"""
Resume Validator / Guardrail.

Финальная проверка изменённого резюме (original vs improved) на
смысловые искажения: role escalation, helper escalation, выдуманные
факты/метрики/технологии, смена дат/должности/компании.

Закрывает пробел, который НЕ ловят существующие _validate_block() /
_FABRICATED_CLAIM_RE в app/missing_routes4.py: те ловят выдуманные
факты (числа, слова с заглавной буквы) и часть причинно-следственной
отсебятины, но не смену силы утверждения при отсутствии новых
"фактов" в узком смысле их regex — например "participated in the
project" → "led the project" проходит их проверку беспрепятственно.

БЕЗ интеграции в _run_improve_pipeline() (интеграция — Этап 8 по
исходному плану, missing_routes4.py в этом цикле не тронут).

Этап 3: заготовлен контракт (GuardrailService.run_check + providers),
все провайдеры были заглушками без сети.
Этап 4: GroqGuardrailProvider стал делать настоящий HTTP-вызов к
Groq — структура запроса, обработка 429 с Retry-After, таймауты и
парсинг JSON-ответа (с fallback через regex при markdown-обёртке)
сделаны по образцу _call_groq_json() из
app/services/openrouter_service.py. OpenAIGuardrailProvider и
AnthropicGuardrailProvider остаются заглушками (подтверждено на
Этапе 2 — первая рабочая реализация только под Groq).

ВАЖНОЕ ОТЛИЧИЕ от _call_groq_json(): там при неудачном парсинге JSON
(даже после regex-фоллбэка) функция всё равно возвращает success=True
с синтетическим "нейтральным" data — это приемлемо для фичи анализа
резюме, но НЕДОПУСТИМО для Guardrail: тихо подставленный "всё чисто"
результат обесценивает саму цель проверки. Поэтому здесь, если JSON
не удалось распарсить ни напрямую, ни через regex — возвращается
success=False (см. GroqGuardrailProvider.check()).

Этап 5: _build_judge_prompt() (бывш. _build_placeholder_prompt)
содержит настоящий Judge-промт — строгий набор из 9 типов нарушений
с фиксированной severity per type (см. докстринг функции ниже) и
явными PASS/FAIL примерами на русском и английском. Вся семантика
проверки остаётся на стороне LLM — новой Python-логики для этих
правил не добавлено, temperature=0.0 из Этапа 4 не менялась.
HTTP/parsing-слой в GroqGuardrailProvider.check() не тронут —
меняется только содержимое промта.

Этап 7 (этот файл сейчас): LocalFallbackGuardrailProvider —
fallback на существующую локальную защиту из app/missing_routes4.py
(_validate_block — Fact Validation, _check_role_escalation — Cycle
R1) на случай, если LLM-провайдер недоступен. GuardrailService.
run_check() получил параметр fallback_on_failure=True: если основной
provider вернул success=False, автоматически пробуется
LocalFallbackGuardrailProvider вместо немедленного отказа (с логом
через logger.warning). _validate_block/_check_role_escalation не
переписаны и не продублированы — вызываются напрямую, через lazy
import внутри LocalFallbackGuardrailProvider.check() (не на уровне
модуля — app/missing_routes4.py тянет app/__init__.py целиком, а тот
на уровне модуля импортирует Flask/SQLAlchemy/JWT/CORS/Mail; lazy
import держит эту зависимость только там, где она реально нужна, и
не ломает изоляцию тестов этого файла).

Контракт: как и весь остальной код проекта (AuthService, APIKeyService,
OpenRouterService) — ничего не райзит наружу, только dict success/error.
"""

import json
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Groq HTTP adapter — константы и вспомогательные функции (Этап 4)
# ---------------------------------------------------------------------------

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Открытый вопрос из Этапа 1 (расхождение с overview.md) закрыт —
# подтверждено консультационной сессией по актуальному коду пайплайна.
GROQ_GUARDRAIL_MODEL = "openai/gpt-oss-120b"

# Fallback-модель — используется на 429 (см. трёхступенчатый паттерн
# ретрая в GroqGuardrailProvider.check(): primary -> 429 -> fallback
# без ожидания -> 429 -> fallback после sleep(Retry-After)).
GROQ_GUARDRAIL_FALLBACK_MODEL = "openai/gpt-oss-20b"

GROQ_TIMEOUT_SECONDS = 60


def _extract_retry_after_seconds(error_message, default=2.0, cap=12.0):
    """
    Тот же парсер, что в app/missing_routes4.py и
    app/services/openrouter_service.py (сообщение Groq вида "Please
    try again in 5.67s"). Продублирован сознательно, а не
    импортирован из missing_routes4.py — модуль остаётся
    самостоятельным, без зависимости от остального пайплайна (в т.ч.
    чтобы тесты этого файла не тянули app/missing_routes4.py и его
    зависимости, например langid).
    """
    m = re.search(r"try again in ([\d.]+)s", error_message or "")
    if not m:
        return default
    try:
        return min(float(m.group(1)), cap)
    except ValueError:
        return default


_JUDGE_SYSTEM_PROMPT = (
    "You are a strict integrity judge for an AI resume-improvement "
    "service. You compare an ORIGINAL resume against an IMPROVED "
    "version of the same resume and report only genuine escalations "
    "or invented claims — never plain rewording, stronger verbs, or "
    "better structure that describes the same underlying facts. "
    "Respond with ONLY valid JSON, no markdown fences, no commentary "
    "before or after it."
)

# Основное тело промта (таксономия нарушений + PASS/FAIL примеры +
# формат ответа) — независимо от original_text/improved_text/language,
# поэтому вынесено в константу, а не пересобирается каждый раз внутри
# _build_judge_prompt(). Обычная (не f-) строка: внутри есть буквальные
# "{"/"}" для JSON-примера, экранировать их не нужно.
_JUDGE_INSTRUCTIONS = """Compare the ORIGINAL resume against the IMPROVED version below and
decide whether IMPROVED makes any claim that is stronger, different,
or more specific than what ORIGINAL actually supports. Apply exactly
the same standard regardless of the resume's language.

ALLOWED — do NOT report these as violations:
Rewording, stronger verbs, better structure, or more polished phrasing
that describes the SAME underlying action, scope and outcome as the
original. For example:
  - "занимался настройкой" -> "настроил"
  - "was responsible for testing" -> "tested and verified"

VIOLATION TYPES — use exactly one of these "type" values per finding.
Severity is FIXED per type as shown below — do not invent new type
values and do not change a type's severity:

severity="high" (these break trust in the resume — always report
when present):
  - ROLE_ESCALATION: the person's degree of ownership/authority over
    a task or project is increased beyond what ORIGINAL supports.
    FAIL example: "участвовал в разработке" -> "руководил командой разработки"
  - HELPER_ESCALATION: a supporting/helping role is turned into sole
    or primary execution of the task.
    FAIL example: "помогал с миграцией" -> "выполнил миграцию"
    FAIL example: "assisted team with API" -> "built the API"
  - TITLE_OR_COMPANY_CHANGE: a job title, degree, certification name,
    or company/institution name differs from ORIGINAL (even a
    slightly more senior-sounding title counts).
  - DATE_OR_DURATION_CHANGE: any date, date range, or duration of
    employment/education differs from ORIGINAL.

severity="medium" (report as a backstop — a separate fact-checker
downstream already catches some of these, this is not the only line
of defense):
  - FACT_HALLUCINATION: a concrete fact not covered by the categories
    below appears in IMPROVED with no basis in ORIGINAL.
  - METRIC_HALLUCINATION: a number, percentage, count, or scale that
    is new or more specific than anything implied by ORIGINAL.
    FAIL example: "worked with customer data" -> "processed 2 million customer records"
  - TECHNOLOGY_HALLUCINATION: a specific tool, language, framework,
    platform, or product named in IMPROVED that is not named or
    clearly implied in ORIGINAL.
    FAIL example: "worked with databases" -> "developed PostgreSQL optimization pipelines"
  - RESPONSIBILITY_DISTORTION: the scope or nature of a
    responsibility is changed in a way not already covered by
    ROLE_ESCALATION or HELPER_ESCALATION above (e.g. individual work
    reframed as team-wide policy, or a one-time task reframed as an
    ongoing duty).

severity="low" (optional — report only if clearly worth flagging, do
not force a finding here for minor style drift):
  - OTHER_FACTUAL_DISTORTION: any other factual mismatch between
    ORIGINAL and IMPROVED not covered by the categories above.

Do not report purely stylistic changes that carry no factual weight
(word choice, sentence order, formatting, tone) — these are not
violations at all, not even low severity.

Respond with ONLY this JSON object, no markdown fences, no commentary:
{
  "pass": true only if "findings" is an empty array, false otherwise,
  "findings": [
    {
      "type": "ROLE_ESCALATION" | "HELPER_ESCALATION" | "TITLE_OR_COMPANY_CHANGE" | "DATE_OR_DURATION_CHANGE" | "FACT_HALLUCINATION" | "METRIC_HALLUCINATION" | "TECHNOLOGY_HALLUCINATION" | "RESPONSIBILITY_DISTORTION" | "OTHER_FACTUAL_DISTORTION",
      "severity": "high" | "medium" | "low",
      "original_excerpt": "short exact quote from ORIGINAL",
      "improved_excerpt": "short exact quote from IMPROVED",
      "explanation": "one sentence, in English, explaining the issue"
    }
  ]
}
If there are no violations, return {"pass": true, "findings": []}.
"""


def _build_judge_prompt(original_text, improved_text, language):
    """
    Настоящий Judge-промт (Этап 5). Таксономия нарушений (9 типов,
    severity фиксирована per type), явные PASS/FAIL примеры на
    русском и английском (многоязычность — Judge должен одинаково
    оценивать эскалацию на любом языке резюме, не только на
    английском) — см. _JUDGE_INSTRUCTIONS выше.

    Форма ответа (pass/findings/type/severity/original_excerpt/
    improved_excerpt/explanation) и парсинг в GroqGuardrailProvider.
    check() не менялись — здесь меняется только содержимое промта.
    """
    system_prompt = _JUDGE_SYSTEM_PROMPT
    user_prompt = (
        f"Resume language: {language}\n\n"
        + _JUDGE_INSTRUCTIONS
        + f"\nORIGINAL:\n{original_text}\n\nIMPROVED:\n{improved_text}\n"
    )
    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class GuardrailProvider:
    """
    Базовый интерфейс провайдера Guardrail-проверки.

    check(original_text, improved_text, language, api_key) ВСЕГДА
    возвращает dict вида:
      {
        "success": bool,       # запрос к провайдеру выполнился штатно
        "pass": bool | None,   # True/False если success=True, иначе None
        "findings": [ {...}, ... ],
        "tokens_used": int,
        "error": str | None,
      }
    Ни один провайдер не должен райзить исключение — при ошибке
    возвращается success=False с текстом в "error".
    """
    PROVIDER_NAME = "base"

    @staticmethod
    def check(original_text, improved_text, language, api_key):
        raise NotImplementedError  # базовый класс напрямую не используется


class GroqGuardrailProvider(GuardrailProvider):
    """
    Groq-провайдер Guardrail-проверки — реальный HTTP-вызов (Этап 4),
    настоящий Judge-промт (Этап 5, см. _build_judge_prompt). Модель —
    GROQ_GUARDRAIL_MODEL, ретрай на 429 переключается на
    GROQ_GUARDRAIL_FALLBACK_MODEL (трёхступенчатый паттерн, см.
    check() ниже) — то же, что делает реальный основной пайплайн.
    """
    PROVIDER_NAME = "groq"

    @staticmethod
    def check(original_text, improved_text, language, api_key):
        # Fail fast: без ключа даже не пытаемся уйти в сеть с пустым
        # Authorization-заголовком.
        if not api_key:
            return {
                "success": False,
                "pass": None,
                "findings": [],
                "tokens_used": 0,
                "error": "GROQ_API_KEY not configured",
            }

        system_prompt, user_prompt = _build_judge_prompt(
            original_text, improved_text, language
        )

        payload = {
            "model": GROQ_GUARDRAIL_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 1500,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            # Шаг 1: primary модель.
            response = requests.post(
                GROQ_API_URL, headers=headers, json=payload, timeout=GROQ_TIMEOUT_SECONDS
            )

            if response.status_code == 429:
                # Шаг 2: первый 429 -> переключиться на fallback-модель,
                # повторить БЕЗ ожидания (трёхступенчатый паттерн
                # реального пайплайна).
                payload["model"] = GROQ_GUARDRAIL_FALLBACK_MODEL
                response = requests.post(
                    GROQ_API_URL, headers=headers, json=payload, timeout=GROQ_TIMEOUT_SECONDS
                )

                if response.status_code == 429:
                    # Шаг 3: 429 снова (уже на fallback-модели) -> ждём
                    # Retry-After и повторяем ещё раз с той же
                    # fallback-моделью. Итого максимум 3 попытки.
                    err_msg = response.json().get("error", {}).get("message", "")
                    wait_s = _extract_retry_after_seconds(err_msg)
                    time.sleep(wait_s)
                    response = requests.post(
                        GROQ_API_URL, headers=headers, json=payload, timeout=GROQ_TIMEOUT_SECONDS
                    )

            if response.status_code != 200:
                error = response.json().get("error", {}).get(
                    "message", f"Groq API error {response.status_code}"
                )
                return {
                    "success": False,
                    "pass": None,
                    "findings": [],
                    "tokens_used": 0,
                    "error": error,
                }

            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
            tokens = data.get("usage", {}).get("total_tokens", 0)

            # Снять markdown-обёртку ```json ... ``` при её наличии.
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*", "", text)
            text = text.strip()

            parsed = None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group())
                    except json.JSONDecodeError:
                        parsed = None

            if parsed is None:
                # Намеренно НЕ повторяем поведение _call_groq_json()
                # (синтетический "success=True, всё чисто" фоллбэк) —
                # см. модуль-докстринг: для Guardrail это опасно.
                logger.warning(
                    "GroqGuardrailProvider: could not parse JSON from LLM response"
                )
                return {
                    "success": False,
                    "pass": None,
                    "findings": [],
                    "tokens_used": tokens,
                    "error": "Could not parse JSON from Groq response",
                }

            return {
                "success": True,
                "pass": bool(parsed.get("pass", False)),
                "findings": parsed.get("findings") or [],
                "tokens_used": tokens,
                "error": None,
            }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "pass": None,
                "findings": [],
                "tokens_used": 0,
                "error": "Groq API request timeout",
            }
        except Exception as e:
            logger.error(f"GroqGuardrailProvider error: {type(e).__name__}: {e}")
            return {
                "success": False,
                "pass": None,
                "findings": [],
                "tokens_used": 0,
                "error": str(e),
            }


class OpenAIGuardrailProvider(GuardrailProvider):
    """
    Заглушка — провайдер не реализован (Этап 2/подтверждено на Этапе 4:
    первая рабочая реализация — только Groq). Не райзит исключение —
    возвращает dict success=False, как и все остальные провайдеры.
    """
    PROVIDER_NAME = "openai"

    @staticmethod
    def check(original_text, improved_text, language, api_key):
        return {
            "success": False,
            "pass": None,
            "findings": [],
            "tokens_used": 0,
            "error": "OpenAI guardrail provider is not implemented",
        }


class AnthropicGuardrailProvider(GuardrailProvider):
    """Заглушка — провайдер не реализован. См. OpenAIGuardrailProvider."""
    PROVIDER_NAME = "anthropic"

    @staticmethod
    def check(original_text, improved_text, language, api_key):
        return {
            "success": False,
            "pass": None,
            "findings": [],
            "tokens_used": 0,
            "error": "Anthropic guardrail provider is not implemented",
        }


class LocalFallbackGuardrailProvider(GuardrailProvider):
    """
    Fallback-провайдер (Этап 7) — переиспользует существующую
    локальную защиту из app/missing_routes4.py вместо LLM:
      - _validate_block(orig, new) -> (bool, str) — Fact Validation
      - _check_role_escalation(orig, new) -> (bool, str) — Cycle R1

    Обе — (True, "") если нарушения нет, (False, reason) если есть;
    reason уже человекочитаемый и используется напрямую как
    explanation, без переформулировки. Ни одна из функций здесь не
    переписана и не продублирована — только вызывается.

    whole-resume, как и остальные провайдеры этого модуля (НЕ
    per-block), хотя обе исходные функции писались для per-block
    цикла в _run_improve_pipeline() — original_text/improved_text
    передаются сюда целиком, поэтому original_excerpt/improved_excerpt
    в finding тоже целиком (нет более точной локализации без LLM).

    Import — LAZY, внутри check(), а не на уровне модуля: см. Этап 7
    в докстринге модуля про app/__init__.py и Flask-стек. При
    ImportError (модуль недоступен, как в изолированном тестовом
    окружении этого файла) — success=False, без падения.
    """
    PROVIDER_NAME = "local_fallback"

    @staticmethod
    def check(original_text, improved_text, language, api_key):
        try:
            from app.missing_routes4 import _validate_block, _check_role_escalation
        except ImportError as e:
            logger.error(f"LocalFallbackGuardrailProvider: import failed: {e}")
            return {
                "success": False,
                "pass": None,
                "findings": [],
                "tokens_used": 0,
                "error": f"Local fallback unavailable: {e}",
            }

        try:
            # Построчное сравнение: обе функции писались для отдельных
            # блоков, а на целом многострочном тексте _validate_block
            # принимает глагол в начале строки (не первой) за "факт".
            orig_lines = original_text.split("\n")
            impr_lines = improved_text.split("\n")

            if len(orig_lines) != len(impr_lines):
                return {
                    "success": False,
                    "pass": None,
                    "findings": [],
                    "tokens_used": 0,
                    "error": "Local fallback cannot align original and improved text line by line",
                }

            findings = []

            for o, n in zip(orig_lines, impr_lines):
                if o.strip() == n.strip():
                    continue

                fact_ok, fact_reason = _validate_block(o, n)
                if not fact_ok:
                    findings.append({
                        "type": "invented_fact",
                        "severity": "high",  # подстраховка последней линии, без LLM-нюансов — при срабатывании лучше перебдеть
                        "original_excerpt": o,
                        "improved_excerpt": n,
                        "explanation": fact_reason,
                    })

                role_ok, role_reason = _check_role_escalation(o, n)
                if not role_ok:
                    findings.append({
                        "type": "role_escalation",
                        "severity": "high",
                        "original_excerpt": o,
                        "improved_excerpt": n,
                        "explanation": role_reason,
                    })

            return {
                "success": True,
                "pass": len(findings) == 0,
                "findings": findings,
                "tokens_used": 0,
                "error": None,
            }
        except Exception as e:
            logger.error(f"LocalFallbackGuardrailProvider: check failed: {type(e).__name__}: {e}")
            return {
                "success": False,
                "pass": None,
                "findings": [],
                "tokens_used": 0,
                "error": f"Local fallback error: {e}",
            }


_PROVIDERS = {
    "groq": GroqGuardrailProvider,
    "openai": OpenAIGuardrailProvider,
    "anthropic": AnthropicGuardrailProvider,
}


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

# severity, при которой блокирующий режим откатывает improved-текст к
# оригиналу целиком (Этап 2, решение №1). severity medium/low не
# блокируют — только попадают в findings.
_BLOCKING_SEVERITY = "high"


class GuardrailService:
    """
    Единая точка вызова Guardrail-проверки. Вызывается из
    _run_improve_pipeline() на Этапе 8 (сейчас туда не подключена —
    missing_routes4.py не изменялся).
    """

    @staticmethod
    def run_check(original_text, improved_text, language, api_key, provider="groq", fallback_on_failure=True):
        """
        Прогнать whole-resume Guardrail-проверку через указанного
        провайдера и применить политику блокировки по severity.

        fallback_on_failure=True (по умолчанию): если провайдер вернул
        success=False (LLM недоступен, timeout, невалидный JSON и
        т.п.), автоматически пробуется LocalFallbackGuardrailProvider
        (Этап 7 — _validate_block/_check_role_escalation из
        app/missing_routes4.py) вместо немедленного отказа. Факт
        перехода на fallback логируется через logger.warning().
        fallback_on_failure=False отключает это — при неудаче
        основного провайдера сразу возвращается его ошибка.

        Возвращает dict:
          {
            "success": bool,             # запрос к провайдеру (или fallback) выполнился штатно
            "pass": bool | None,         # None если success=False
            "findings": [...],
            "guardrail_rejected": bool,  # True если есть finding severity="high"
            "tokens_used": int,
            "error": str | None,
          }

        Никогда не райзит исключение: неизвестный provider возвращает
        success=False с понятной ошибкой вместо KeyError; если сам
        провайдер (основной или fallback) всё же нарушит контракт и
        райзнет, это тоже гасится здесь, чтобы Guardrail-проверка не
        могла уронить весь improve-пайплайн.
        """
        provider_cls = _PROVIDERS.get(provider)

        if provider_cls is None:
            return {
                "success": False,
                "pass": None,
                "findings": [],
                "guardrail_rejected": False,
                "tokens_used": 0,
                "error": (
                    f"Unknown guardrail provider: {provider!r}. "
                    f"Available: {', '.join(sorted(_PROVIDERS))}"
                ),
            }

        try:
            result = provider_cls.check(original_text, improved_text, language, api_key)
        except Exception as e:
            logger.error(f"Guardrail provider '{provider}' raised: {type(e).__name__}: {e}")
            result = {
                "success": False,
                "pass": None,
                "findings": [],
                "tokens_used": 0,
                "error": f"Guardrail provider error: {e}",
            }

        # provider_cls is not LocalFallbackGuardrailProvider защищает от
        # самоссылающегося fallback, если этот провайдер когда-нибудь
        # тоже окажется зарегистрирован в _PROVIDERS (сейчас — нет).
        if (
            not result.get("success")
            and fallback_on_failure
            and provider_cls is not LocalFallbackGuardrailProvider
        ):
            logger.warning(
                f"Guardrail provider '{provider}' failed ({result.get('error')}); "
                f"falling back to local checks (_validate_block / _check_role_escalation)."
            )
            try:
                result = LocalFallbackGuardrailProvider.check(
                    original_text, improved_text, language, api_key
                )
            except Exception as e:
                logger.error(f"LocalFallbackGuardrailProvider raised: {type(e).__name__}: {e}")
                result = {
                    "success": False,
                    "pass": None,
                    "findings": [],
                    "tokens_used": 0,
                    "error": f"Guardrail fallback error: {e}",
                }

        findings = result.get("findings") or []
        guardrail_rejected = any(f.get("severity") == _BLOCKING_SEVERITY for f in findings)

        return {
            "success": result.get("success", False),
            "pass": result.get("pass"),
            "findings": findings,
            "guardrail_rejected": guardrail_rejected,
            "tokens_used": result.get("tokens_used", 0),
            "error": result.get("error"),
        }
