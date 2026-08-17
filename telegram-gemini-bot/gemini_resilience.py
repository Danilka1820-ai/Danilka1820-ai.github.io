"""Устойчивый вызов Gemini: SDK-повтор + резервные модели + предохранитель.

Три независимых слоя защиты от временной перегрузки Gemini (503 UNAVAILABLE,
429 RESOURCE_EXHAUSTED и сетевых сбоев), от внешнего к внутреннему:

  1. Повтор внутри ОДНОЙ модели — не наш код, а встроенный в google-genai
     SDK механизм (tenacity: экспоненциальная задержка с джиттером на
     HTTP-транспорте), который мы включаем через build_retry_http_options().
     Это ровно то, что рекомендует сама документация Gemini
     (https://ai.google.dev/gemini-api/docs/troubleshooting) — своего
     повторителя поверх официального не пишем, только настраиваем его.
  2. Резервные модели — если основная модель не ответила даже после своих
     повторов, generate_content_resilient() пробует следующую модель из
     GEMINI_FALLBACK_MODELS по порядку.
  3. Предохранитель — если модель подряд отказывает несколько раз за один
     запуск poll_once.py, её на время исключают из перебора, чтобы не тратить
     весь бюджет запроса на заведомо мёртвую модель.

Важная честная оговорка про предохранитель и диагностику: это модульные
Python-переменные, они живут только в памяти ОДНОГО процесса poll_once.py
(один цикл опроса, обычно ~5 минут) и не сохраняются между перезапусками —
у проекта нет бесплатной постоянной базы, и притворяться, что состояние
надёжно хранится, было бы враньём. Между процессами защиту от повторной
обработки одного и того же сообщения даёт offset getUpdates() в poll_once.py
(Telegram сам не пришлёт апдейт снова после подтверждённого offset) — это
не зависит от gemini_resilience и не теряется при перезапуске.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from google.genai import errors as genai_errors
from google.genai import types as genai_types

# ---------------------------------------------------------------------------
# Конфигурация — переменные окружения, у всех есть безопасные значения
# по умолчанию, ничего не ломается, если их не задавать вовсе.
# ---------------------------------------------------------------------------


def _env(name: str, default: str) -> str:
    """os.getenv() с default, но не пойманным на пустую строку.

    GitHub Actions подставляет незаполненную ${{ vars.X }} как пустую
    строку, а не отсутствующую переменную — обычный os.getenv(name, default)
    в этом случае вернул бы "" вместо default и уронил бы float()/int() ниже
    или отправил бы Gemini запрос с пустым именем модели."""
    value = os.getenv(name)
    return value if value else default


def _split_models(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


GEMINI_PRIMARY_MODEL = _env("GEMINI_PRIMARY_MODEL", "gemini-flash-latest")
# gemini-flash-lite-latest — официальный псевдоним облегчённого уровня Gemini
# (https://ai.google.dev/gemini-api/docs/models), у него отдельная от
# gemini-flash-latest квота и обычно меньше нагрузки — при 503 на основной
# модели резерв реально спасает, а не просто откладывает тот же отказ.
# Переопределяется через GEMINI_FALLBACK_MODELS (через запятую, если моделей
# несколько), если владелец бота захочет проверить и указать другой список
# в своём Google AI Studio.
GEMINI_FALLBACK_MODELS = _split_models(_env("GEMINI_FALLBACK_MODELS", "gemini-flash-lite-latest"))
# Таймаут одного HTTP-запроса к Gemini, секунды.
GEMINI_REQUEST_TIMEOUT = float(_env("GEMINI_REQUEST_TIMEOUT", "30"))
# Сколько раз SDK повторит запрос к ОДНОЙ модели (включая первую попытку)
# при 408/429/500/502/503/504 и обрыве соединения, прежде чем сдаться и
# перейти к резервной модели.
GEMINI_MAX_RETRIES = max(1, int(_env("GEMINI_MAX_RETRIES", "3")))
# После скольких подряд отказов модель считается перегруженной и временно
# исключается из перебора.
GEMINI_CIRCUIT_THRESHOLD = max(1, int(_env("GEMINI_CIRCUIT_THRESHOLD", "3")))
# На сколько секунд исключается — после этого следующий вызов снова
# пробует эту модель (пробная попытка), а не ждёт полный интервал вслепую.
GEMINI_CIRCUIT_COOLDOWN = float(_env("GEMINI_CIRCUIT_COOLDOWN", "60"))

RETRYABLE_HTTP_CODES = (408, 429, 500, 502, 503, 504)
# Неверный ключ/нет доступа — не лечится ни повтором, ни сменой модели:
# та же причина сломает и остальные модели тем же образом.
AUTH_HTTP_CODES = (401, 403)


def build_retry_http_options() -> genai_types.HttpOptions:
    """HttpOptions с включённым официальным SDK-повтором (см. модульный докстринг).

    Без явного retry_options google-genai вообще не повторяет запросы —
    именно поэтому голый 503 раньше долетал прямо до пользователя.
    """
    return genai_types.HttpOptions(
        timeout=int(GEMINI_REQUEST_TIMEOUT * 1000),
        retry_options=genai_types.HttpRetryOptions(
            attempts=GEMINI_MAX_RETRIES,
            initial_delay=1.0,
            max_delay=8.0,
            exp_base=2.0,
            jitter=1.0,
            http_status_codes=list(RETRYABLE_HTTP_CODES),
        ),
    )


# ---------------------------------------------------------------------------
# Классификация ошибок
# ---------------------------------------------------------------------------


def _classify(error: Exception) -> str:
    """'auth' — ключ/доступ, дальше пробовать бессмысленно; иначе 'retryable' —
    стоит попробовать следующую модель (сам SDK уже отработал повторы внутри
    текущей модели, если дело было в 5xx/429/сети)."""
    if isinstance(error, genai_errors.APIError) and getattr(error, "code", None) in AUTH_HTTP_CODES:
        return "auth"
    return "retryable"


class GeminiUnavailableError(RuntimeError):
    """Основная и все резервные модели не ответили после исчерпания повторов."""


class GeminiConfigurationError(RuntimeError):
    """Ключ/доступ недействителен — не лечится повтором ни для одной модели."""


# ---------------------------------------------------------------------------
# Предохранитель — см. честную оговорку в докстринге модуля про область
# действия (память одного процесса poll_once.py, не переживает перезапуск).
# ---------------------------------------------------------------------------


@dataclass
class _ModelCircuit:
    consecutive_failures: int = 0
    opened_until: float = 0.0

    def is_open(self) -> bool:
        return time.monotonic() < self.opened_until

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= GEMINI_CIRCUIT_THRESHOLD:
            self.opened_until = time.monotonic() + GEMINI_CIRCUIT_COOLDOWN

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = 0.0


_circuits: dict[str, _ModelCircuit] = {}


def _circuit_for(model: str) -> _ModelCircuit:
    return _circuits.setdefault(model, _ModelCircuit())


# ---------------------------------------------------------------------------
# Диагностика — только для /status, без секретов и без текста сообщений.
# ---------------------------------------------------------------------------


@dataclass
class _Diagnostics:
    last_success_model: str | None = None
    last_success_at: float | None = None
    last_error_category: str | None = None
    last_error_model: str | None = None
    last_error_at: float | None = None
    fallback_uses: int = 0
    failures_by_model: dict[str, int] = field(default_factory=dict)


_diag = _Diagnostics()


def diagnostics_snapshot() -> dict:
    """Безопасный снимок состояния для команды /status: ни ключей, ни текста
    сообщений здесь нет и быть не может — эта функция их никогда не видит."""
    return {
        "основная модель": GEMINI_PRIMARY_MODEL,
        "резервные модели": list(GEMINI_FALLBACK_MODELS) or "не заданы",
        "последний успех": _diag.last_success_model,
        "последний успех когда": _diag.last_success_at,
        "последняя ошибка — категория": _diag.last_error_category,
        "последняя ошибка — модель": _diag.last_error_model,
        "последняя ошибка когда": _diag.last_error_at,
        "сколько раз спасал резерв": _diag.fallback_uses,
        "отказы по моделям": dict(_diag.failures_by_model),
        "модели под предохранителем сейчас": [m for m, c in _circuits.items() if c.is_open()],
        "область действия": (
            "состояние живёт только в этом запуске poll_once.py "
            "и обнуляется при следующем"
        ),
    }


def _log(event: str, **fields) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"gemini[{event}] {details}")


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------


def generate_content_resilient(client, contents, *, models: list[str] | None = None):
    """generate_content с резервными моделями и предохранителем.

    Возвращает (response, имя_использованной_модели). Поднимает
    GeminiConfigurationError сразу при 401/403 (без перебора моделей) или
    GeminiUnavailableError, если ни одна модель (или ни одна не под
    предохранителем) не ответила.
    """
    model_list = list(models) if models is not None else [GEMINI_PRIMARY_MODEL, *GEMINI_FALLBACK_MODELS]
    model_list = [m for m in model_list if m]
    if not model_list:
        raise GeminiUnavailableError("не задана ни основная, ни резервная модель Gemini")

    last_error: Exception | None = None
    tried_any = False
    for index, model in enumerate(model_list):
        circuit = _circuit_for(model)
        if circuit.is_open():
            _log("circuit-skip", model=model)
            continue
        tried_any = True
        try:
            response = client.models.generate_content(model=model, contents=contents)
        except Exception as error:  # noqa: BLE001 - классифицируем сами, см. _classify
            category = _classify(error)
            _log("model-failed", model=model, category=category, error_type=type(error).__name__)
            _diag.last_error_category = category
            _diag.last_error_model = model
            _diag.last_error_at = time.time()
            if category == "auth":
                raise GeminiConfigurationError(str(error)) from error
            circuit.record_failure()
            _diag.failures_by_model[model] = _diag.failures_by_model.get(model, 0) + 1
            last_error = error
            continue

        circuit.record_success()
        _diag.last_success_model = model
        _diag.last_success_at = time.time()
        if index > 0:
            _diag.fallback_uses += 1
            _log("fallback-used", model=model)
        return response, model

    if not tried_any:
        raise GeminiUnavailableError(
            "все модели Gemini временно исключены предохранителем в этом запуске"
        )
    raise GeminiUnavailableError(str(last_error) if last_error else "Gemini не ответил")


def friendly_error_message(error: Exception) -> str:
    """Текст для пользователя — без сырого JSON/стектрейса Gemini.

    Полную техническую причину печатаем отдельно через print() (это уходит
    в лог запуска Actions, а не в чат) там, где эта функция вызывается."""
    if isinstance(error, GeminiConfigurationError):
        return (
            "Gemini недоступен из-за проблемы с доступом (ключ или права API). "
            "Повторять запрос бессмысленно — это может поправить только владелец бота."
        )
    if isinstance(error, GeminiUnavailableError):
        return "Gemini временно недоступен. Попробуйте повторить запрос позже."
    return "Не удалось обработать запрос из-за неожиданной ошибки."
