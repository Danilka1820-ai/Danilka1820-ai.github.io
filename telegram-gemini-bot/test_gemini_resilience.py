"""Тесты устойчивости к сбоям Gemini — без сети и без реального google-genai.

Как и test_poll_once.py, подменяет пакет google.genai фейковыми модулями
перед загрузкой gemini_resilience.py: в CI (.github/workflows/proverka.yml)
зависимости из requirements.txt не ставятся, только синтаксис проверяется
и гоняются эти unit-тесты — поэтому модуль должен быть тестируем и без
установленного google-genai.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
import unittest.mock
from pathlib import Path


class FakeAPIError(Exception):
    """Имитирует google.genai.errors.APIError: несёт HTTP-код ответа."""

    def __init__(self, code, message="перегружено"):
        super().__init__(f"{code} {message}")
        self.code = code
        self.status = message


def load_module():
    """Собирает фейковый google.genai (errors.APIError, types.HttpOptions/
    HttpRetryOptions) и загружает gemini_resilience.py поверх него — так же,
    как test_poll_once.py подменяет telebot перед загрузкой poll_once.py."""
    google_pkg = types.ModuleType("google")
    genai_pkg = types.ModuleType("google.genai")
    errors_mod = types.ModuleType("google.genai.errors")
    errors_mod.APIError = FakeAPIError
    types_mod = types.ModuleType("google.genai.types")

    class FakeHttpRetryOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeHttpOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    types_mod.HttpRetryOptions = FakeHttpRetryOptions
    types_mod.HttpOptions = FakeHttpOptions

    google_pkg.genai = genai_pkg
    genai_pkg.errors = errors_mod
    genai_pkg.types = types_mod

    modules = {
        "google": google_pkg,
        "google.genai": genai_pkg,
        "google.genai.errors": errors_mod,
        "google.genai.types": types_mod,
    }
    old = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        path = Path(__file__).with_name("gemini_resilience.py")
        spec = importlib.util.spec_from_file_location("gemini_resilience_under_test", path)
        module = importlib.util.module_from_spec(spec)
        # dataclass() resolves annotations via sys.modules[cls.__module__] —
        # must be registered before exec_module, or it raises on 'X | None'.
        sys.modules["gemini_resilience_under_test"] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("gemini_resilience_under_test", None)
        return module
    finally:
        for name, previous in old.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class FakeModels:
    """models.generate_content, управляемый списком запрограммированных
    ответов на модель: исключение — значит модель отказала, иначе — ответ."""

    def __init__(self, plan: dict):
        # plan: {имя_модели: [результат1, результат2, ...]} — каждый вызов
        # снимает следующий элемент; исключение поднимается, иначе возвращается.
        self.plan = {model: list(results) for model, results in plan.items()}
        self.calls: list[str] = []

    def generate_content(self, model, contents):
        self.calls.append(model)
        queue = self.plan.get(model)
        if not queue:
            raise AssertionError(f"незапланированный вызов модели {model}")
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, plan: dict):
        self.models = FakeModels(plan)


class GeminiResilienceTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        # Каждый тест должен видеть чистый предохранитель/диагностику —
        # модуль хранит их в глобальных словарях уровня модуля.
        self.module._circuits.clear()
        self.module._diag.__init__()

    # 1. Основная модель отвечает с первой попытки.
    def test_primary_success_first_try(self):
        client = FakeClient({"m1": ["ok-response"]})
        response, used = self.module.generate_content_resilient(client, "текст", models=["m1", "m2"])
        self.assertEqual(response, "ok-response")
        self.assertEqual(used, "m1")
        self.assertEqual(client.models.calls, ["m1"])
        self.assertEqual(self.module._diag.fallback_uses, 0)

    # 4. Основная модель перегружена (SDK уже исчерпал свои повторы) -> резерв отвечает.
    def test_primary_exhausted_falls_back_to_secondary(self):
        client = FakeClient({
            "m1": [FakeAPIError(503, "UNAVAILABLE")],
            "m2": ["fallback-response"],
        })
        response, used = self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        self.assertEqual(response, "fallback-response")
        self.assertEqual(used, "m2")
        self.assertEqual(client.models.calls, ["m1", "m2"])
        self.assertEqual(self.module._diag.fallback_uses, 1)
        self.assertEqual(self.module._diag.failures_by_model, {"m1": 1})

    # 5. Все модели (основная и резерв) перегружены.
    def test_all_models_unavailable_raises_unavailable(self):
        client = FakeClient({
            "m1": [FakeAPIError(503)],
            "m2": [FakeAPIError(500)],
        })
        with self.assertRaises(self.module.GeminiUnavailableError):
            self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        self.assertEqual(client.models.calls, ["m1", "m2"])

    # 6. 429 — тоже переносимая категория, ведёт себя как 503.
    def test_429_treated_as_retryable_and_falls_back(self):
        client = FakeClient({
            "m1": [FakeAPIError(429, "RESOURCE_EXHAUSTED")],
            "m2": ["ok"],
        })
        response, used = self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        self.assertEqual(used, "m2")

    # 7. Таймаут/сетевой обрыв — не APIError вовсе, тоже должен уйти в резерв,
    # а не завершиться необработанным исключением.
    def test_network_timeout_is_retryable_via_fallback(self):
        client = FakeClient({
            "m1": [TimeoutError("network deadline exceeded")],
            "m2": ["ok"],
        })
        response, used = self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        self.assertEqual(used, "m2")

    # 8. 401 — не повторяется и не перебирает резерв: поднимается сразу.
    def test_401_raises_configuration_error_without_trying_fallback(self):
        client = FakeClient({
            "m1": [FakeAPIError(401, "UNAUTHENTICATED")],
            "m2": ["ok"],
        })
        with self.assertRaises(self.module.GeminiConfigurationError):
            self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        self.assertEqual(client.models.calls, ["m1"])  # резерв даже не тронут

    # 9. 403 — та же гарантия, что и для 401.
    def test_403_raises_configuration_error_without_trying_fallback(self):
        client = FakeClient({"m1": [FakeAPIError(403, "PERMISSION_DENIED")]})
        with self.assertRaises(self.module.GeminiConfigurationError):
            self.module.generate_content_resilient(client, "x", models=["m1"])

    # 10. 400 (некорректный запрос) — не бесконечный цикл: одна попытка на
    # модель, затем честный переход к следующей и в итоге — понятная ошибка.
    def test_400_bad_request_bounded_not_infinite(self):
        client = FakeClient({
            "m1": [FakeAPIError(400, "INVALID_ARGUMENT")],
            "m2": [FakeAPIError(400, "INVALID_ARGUMENT")],
        })
        with self.assertRaises(self.module.GeminiUnavailableError):
            self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        self.assertEqual(client.models.calls, ["m1", "m2"])  # ровно по одному разу каждая

    # 2/3 переупакованы в общий сценарий: без резерва модель отвечает после
    # того, как SDK внутри себя уже пережил 503 — это уровень ответственности
    # самого google-genai (retry_options), а не generate_content_resilient;
    # здесь проверяем именно то, что configuration действительно запрашивает
    # его официальный retry с джиттером.
    def test_retry_http_options_enable_official_sdk_retry_with_jitter(self):
        options = self.module.build_retry_http_options()
        retry = options.retry_options
        self.assertEqual(retry.attempts, self.module.GEMINI_MAX_RETRIES)
        self.assertGreater(retry.jitter, 0)
        for code in (408, 429, 500, 502, 503, 504):
            self.assertIn(code, retry.http_status_codes)

    # Предохранитель: после порога подряд отказов модель пропускается без
    # обращения к ней, а по истечении cooldown — снова пробуется.
    def test_circuit_breaker_skips_then_recovers_after_cooldown(self):
        self.module.GEMINI_CIRCUIT_THRESHOLD = 2
        self.module.GEMINI_CIRCUIT_COOLDOWN = 30
        client = FakeClient({
            "m1": [FakeAPIError(503), FakeAPIError(503), "recovered"],
            "m2": ["fallback-1", "fallback-2", "fallback-3"],
        })
        fake_clock = [0.0]
        original_monotonic = self.module.time.monotonic
        self.module.time.monotonic = lambda: fake_clock[0]
        try:
            # Два отказа m1 подряд открывают предохранитель.
            self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
            self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
            circuit = self.module._circuit_for("m1")
            self.assertTrue(circuit.is_open())
            self.assertEqual(client.models.calls.count("m1"), 2)

            # Пока не истёк cooldown — m1 пропускается, зовём сразу m2.
            response, used = self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
            self.assertEqual(used, "m2")
            self.assertEqual(client.models.calls.count("m1"), 2)  # m1 не тронута

            # После cooldown — пробная попытка снова достаётся m1.
            fake_clock[0] = 31.0
            response, used = self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
            self.assertEqual(used, "m1")
            self.assertEqual(response, "recovered")
        finally:
            self.module.time.monotonic = original_monotonic

    def test_all_models_open_circuit_raises_unavailable_without_calling_anyone(self):
        self.module.GEMINI_CIRCUIT_THRESHOLD = 1
        client = FakeClient({"m1": [FakeAPIError(503)], "m2": [FakeAPIError(503)]})
        with self.assertRaises(self.module.GeminiUnavailableError):
            self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        self.assertEqual(client.models.calls, ["m1", "m2"])
        # Оба теперь под предохранителем (порог=1 отказ каждая) — второй
        # заход не должен обратиться ни к одной модели: пустые очереди в
        # FakeModels подняли бы AssertionError, если бы предохранитель не сработал.
        with self.assertRaises(self.module.GeminiUnavailableError):
            self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        self.assertEqual(client.models.calls, ["m1", "m2"])  # новых вызовов не добавилось

    def test_empty_env_string_falls_back_to_default_not_crash(self):
        """GitHub Actions подставляет незаполненную ${{ vars.X }} как пустую
        строку, а не отсутствие переменной — os.getenv(name, default) в
        этом случае вернул бы "" и уронил бы float()/int() при загрузке
        модуля или отправил бы Gemini пустое имя модели."""
        import os

        with unittest.mock.patch.dict(
            os.environ,
            {
                "GEMINI_PRIMARY_MODEL": "",
                "GEMINI_REQUEST_TIMEOUT": "",
                "GEMINI_MAX_RETRIES": "",
            },
        ):
            module = load_module()
        self.assertEqual(module.GEMINI_PRIMARY_MODEL, "gemini-flash-latest")
        self.assertEqual(module.GEMINI_REQUEST_TIMEOUT, 30.0)
        self.assertEqual(module.GEMINI_MAX_RETRIES, 3)

    def test_no_models_configured_raises_unavailable(self):
        client = FakeClient({})
        with self.assertRaises(self.module.GeminiUnavailableError):
            self.module.generate_content_resilient(client, "x", models=[])

    def test_diagnostics_snapshot_reflects_success_and_failure(self):
        client = FakeClient({
            "m1": [FakeAPIError(503)],
            "m2": ["ok"],
        })
        self.module.generate_content_resilient(client, "x", models=["m1", "m2"])
        snapshot = self.module.diagnostics_snapshot()
        self.assertEqual(snapshot["последний успех"], "m2")
        self.assertEqual(snapshot["сколько раз спасал резерв"], 1)
        self.assertEqual(snapshot["отказы по моделям"], {"m1": 1})
        self.assertNotIn("GEMINI_API_KEY", str(snapshot))
        self.assertNotIn("api_key", str(snapshot))

    def test_friendly_message_hides_raw_gemini_payload(self):
        raw = self.module.GeminiUnavailableError("503 UNAVAILABLE. {'error': {'message': 'overloaded'}}")
        friendly = self.module.friendly_error_message(raw)
        self.assertNotIn("{'error'", friendly)
        self.assertIn("недоступен", friendly)

        auth_error = self.module.GeminiConfigurationError("401 UNAUTHENTICATED. details...")
        friendly_auth = self.module.friendly_error_message(auth_error)
        self.assertIn("владелец бота", friendly_auth)

        generic = friendly = self.module.friendly_error_message(ValueError("что-то своё"))
        self.assertIn("неожиданной ошибки", friendly)


if __name__ == "__main__":
    unittest.main()
