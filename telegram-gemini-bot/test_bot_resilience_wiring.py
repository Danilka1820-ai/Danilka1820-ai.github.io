"""Проверяет, что bot.py действительно использует устойчивую цепочку, а не
просто импортирует gemini_resilience и не пользуется её защитой.

Как test_poll_once.py, подменяет telebot/google.genai/dotenv фейками перед
загрузкой bot.py — реальные пакеты в CI (проверка без pip install) не
установлены, а секреты (TELEGRAM_BOT_TOKEN и т.д.) не нужны реальные.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


class FakeAPIError(Exception):
    def __init__(self, code, message="перегружено"):
        super().__init__(f"{code} {message}")
        self.code = code
        self.status = message


class FakeMessage:
    def __init__(self, user_id=1):
        self.from_user = types.SimpleNamespace(id=user_id)
        self.chat = types.SimpleNamespace(id=1)
        self.message_id = 1
        self.forward_from_chat = None
        self.text = "/status"


class FakeTeleBot:
    """Достаточно telebot.TeleBot, чтобы bot.py импортировался и
    зарегистрировал хендлеры, и чтобы можно было проверить, с какими
    аргументами его на самом деле сконструировали."""

    last_kwargs = None

    def __init__(self, token, **kwargs):
        self.token = token
        self.kwargs = kwargs
        FakeTeleBot.last_kwargs = kwargs
        self.handlers = {}
        self.replies = []

    def message_handler(self, commands=None, content_types=None):
        def decorator(func):
            for name in commands or content_types or ["*"]:
                self.handlers[name] = func
            return func
        return decorator

    def reply_to(self, message, text):
        self.replies.append(text)
        return types.SimpleNamespace(chat=message.chat, message_id=99)

    def edit_message_text(self, text, chat_id=None, message_id=None):
        self.replies.append(text)


def load_bot_module(gemini_models_plan: dict):
    telebot_mod = types.ModuleType("telebot")
    telebot_mod.TeleBot = FakeTeleBot

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

    class FakeModels:
        def generate_content(self, model, contents):
            queue = gemini_models_plan.get(model, [])
            if not queue:
                raise AssertionError(f"незапланированный вызов {model}")
            outcome = queue.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return types.SimpleNamespace(text=outcome)

    class FakeGenaiClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    genai_pkg.Client = FakeGenaiClient
    genai_pkg.errors = errors_mod
    genai_pkg.types = types_mod
    google_pkg.genai = genai_pkg

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *a, **kw: None

    modules = {
        "telebot": telebot_mod,
        "google": google_pkg,
        "google.genai": genai_pkg,
        "google.genai.errors": errors_mod,
        "google.genai.types": types_mod,
        "dotenv": dotenv_mod,
    }
    old = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)

    import os
    env_old = {
        k: os.environ.get(k)
        for k in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "CHANNEL_ID", "ADMIN_ID")
    }
    os.environ.update(
        TELEGRAM_BOT_TOKEN="123:fake",
        GEMINI_API_KEY="fake-key",
        CHANNEL_ID="@fake",
        ADMIN_ID="1",
    )
    try:
        path = Path(__file__).with_name("bot.py")
        spec = importlib.util.spec_from_file_location("bot_under_test", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["bot_under_test"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in old.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        for key, value in env_old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.modules.pop("bot_under_test", None)


class BotWiringTests(unittest.TestCase):
    def setUp(self):
        # bot.py делает настоящий "import gemini_resilience" (не через
        # фейковый sys.modules) — после первого теста этого файла модуль
        # остаётся в sys.modules, а вместе с ним и предохранитель с
        # диагностикой. Сбрасываем, если он уже был загружен, чтобы
        # результат одного теста не зависел от порядка выполнения.
        cached = sys.modules.get("gemini_resilience")
        if cached is not None:
            cached._circuits.clear()
            cached._diag.__init__()

    def test_bot_is_constructed_with_threaded_false(self):
        """Регресс на найденный в этой сессии баг: threaded=True (умолчание
        telebot) роняет фоновую обработку демон-потоком при выходе процесса
        poll_once.py — сообщение молча теряется. threaded=False обязателен."""
        load_bot_module({})
        self.assertEqual(FakeTeleBot.last_kwargs.get("threaded"), False)

    def test_status_command_reports_diagnostics_without_crashing(self):
        module = load_bot_module({})
        handler = module.bot.handlers.get("status")
        self.assertIsNotNone(handler, "команда /status не зарегистрирована")
        handler(FakeMessage(user_id=module.ADMIN_ID))
        self.assertTrue(module.bot.replies)
        self.assertIn("Диагностика Gemini", module.bot.replies[-1])

    def test_text_handler_shows_friendly_message_on_gemini_overload(self):
        # По умолчанию (без GEMINI_PRIMARY_MODEL в окружении) — "gemini-flash-latest".
        module = load_bot_module({"gemini-flash-latest": [FakeAPIError(503)]})
        message = FakeMessage(user_id=module.ADMIN_ID)
        message.text = "какой-то текст поста"
        message.forward_from_chat = None
        handler = module.bot.handlers.get("text")
        self.assertIsNotNone(handler)
        handler(message)
        final_text = module.bot.replies[-1]
        self.assertIn("временно недоступен", final_text)
        self.assertNotIn("503", final_text)
        self.assertNotIn("UNAVAILABLE", final_text)

    def test_text_handler_success_returns_post(self):
        module = load_bot_module({"gemini-flash-latest": ["Готовый пост про вечер"]})
        message = FakeMessage(user_id=module.ADMIN_ID)
        message.text = "какой-то текст поста"
        handler = module.bot.handlers.get("text")
        handler(message)
        self.assertIn("Готовый пост про вечер", module.bot.replies[-1])


if __name__ == "__main__":
    unittest.main()
