"""Изолированные тесты цепочки polling без сети и сторонних пакетов."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeTelegramError(Exception):
    def __init__(self, code=None):
        super().__init__(f"Telegram error {code}")
        self.error_code = code


class FakeBot:
    def __init__(self, errors=None):
        self.errors = list(errors or [])
        self.deleted_webhook = False

    def remove_webhook(self, drop_pending_updates=False):
        self.deleted_webhook = not drop_pending_updates

    def get_updates(self, **_kwargs):
        if self.errors:
            raise self.errors.pop(0)
        return []

    def process_new_updates(self, _updates):
        raise AssertionError("в этих тестах обновлений быть не должно")


def load_module(fake_bot):
    telebot = types.ModuleType("telebot")
    apihelper = types.ModuleType("telebot.apihelper")
    apihelper.ApiTelegramException = FakeTelegramError
    telebot.apihelper = apihelper

    bot_module = types.ModuleType("bot")
    bot_module.bot = fake_bot

    requests = types.ModuleType("requests")
    requests.RequestException = OSError
    requests.post = lambda *_args, **_kwargs: None

    modules = {
        "telebot": telebot,
        "telebot.apihelper": apihelper,
        "bot": bot_module,
        "requests": requests,
    }
    old = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        path = Path(__file__).with_name("poll_once.py")
        spec = importlib.util.spec_from_file_location("poll_once_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in old.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class PollOnceTests(unittest.TestCase):
    def test_409_stops_without_chaining(self):
        module = load_module(FakeBot([FakeTelegramError(409)]))
        module.POLL_SECONDS = 10
        with patch.object(module.time, "time", return_value=0):
            self.assertFalse(module.poll())

    def test_repeated_transient_errors_fail_the_job(self):
        errors = [FakeTelegramError(500) for _ in range(3)]
        module = load_module(FakeBot(errors))
        module.POLL_SECONDS = 10
        with patch.object(module.time, "time", return_value=0), patch.object(module.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "3 попыток"):
                module.poll()

    def test_missing_github_context_does_not_claim_successful_dispatch(self):
        module = load_module(FakeBot())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(module.trigger_next_run())


if __name__ == "__main__":
    unittest.main()
