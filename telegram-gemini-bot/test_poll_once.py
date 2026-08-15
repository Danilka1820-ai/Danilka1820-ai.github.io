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


class RecordingBot(FakeBot):
    """Как FakeBot, но запоминает пачки апдейтов вместо падения на них —
    нужен тестам dispatch_updates, где process_new_updates как раз должен
    вызваться (для одиночных сообщений, не альбомов)."""

    def __init__(self):
        super().__init__()
        self.processed_batches = []

    def process_new_updates(self, updates):
        self.processed_batches.append(list(updates))


class FakeGroupedMessage:
    def __init__(self, message_id, media_group_id):
        self.message_id = message_id
        self.media_group_id = media_group_id


class FakeUpdate:
    """update_id — как у настоящего Update. _grouped — Message из альбома,
    если он есть у этого апдейта (see fake group_photo_messages ниже)."""

    def __init__(self, update_id, grouped=None):
        self.update_id = update_id
        self._grouped = grouped


def load_module(fake_bot, group_photo_messages=None, process_album=None):
    telebot = types.ModuleType("telebot")
    apihelper = types.ModuleType("telebot.apihelper")
    apihelper.ApiTelegramException = FakeTelegramError
    telebot.apihelper = apihelper

    bot_module = types.ModuleType("bot")
    bot_module.bot = fake_bot
    bot_module.group_photo_messages = group_photo_messages or (lambda update: None)
    bot_module.process_album = process_album or (lambda messages: None)

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


class DispatchUpdatesTests(unittest.TestCase):
    """dispatch_updates — новый код, разбирающий пачку апдейтов на одиночные
    сообщения (как раньше) и фото одного альбома (в один process_album)."""

    @staticmethod
    def grouper(update):
        return update._grouped  # noqa: SLF001 - доступ к приватному полю фейка, это тест

    def test_album_photos_go_to_process_album_as_one_group(self):
        recording_bot = RecordingBot()
        album_calls = []
        module = load_module(
            recording_bot,
            group_photo_messages=self.grouper,
            process_album=lambda messages: album_calls.append(list(messages)),
        )

        text_update = FakeUpdate(10)
        # message_id намеренно не по порядку — dispatch_updates должен сам
        # отсортировать альбом, чтобы подпись и фото не перепутались местами.
        photo_2 = FakeUpdate(12, grouped=FakeGroupedMessage(2, "album-1"))
        photo_1 = FakeUpdate(11, grouped=FakeGroupedMessage(1, "album-1"))

        module.dispatch_updates([text_update, photo_2, photo_1])

        self.assertEqual(recording_bot.processed_batches, [[text_update]])
        self.assertEqual(len(album_calls), 1)
        self.assertEqual([m.message_id for m in album_calls[0]], [1, 2])

    def test_two_different_albums_stay_separate(self):
        recording_bot = RecordingBot()
        album_calls = []
        module = load_module(
            recording_bot,
            group_photo_messages=self.grouper,
            process_album=lambda messages: album_calls.append(
                [m.media_group_id for m in messages]
            ),
        )

        updates = [
            FakeUpdate(1, grouped=FakeGroupedMessage(1, "a")),
            FakeUpdate(2, grouped=FakeGroupedMessage(2, "b")),
            FakeUpdate(3, grouped=FakeGroupedMessage(3, "a")),
        ]
        module.dispatch_updates(updates)

        self.assertEqual(recording_bot.processed_batches, [])
        self.assertEqual(sorted(len(group) for group in album_calls), [1, 2])

    def test_no_albums_sends_everything_as_singles(self):
        recording_bot = RecordingBot()
        module = load_module(recording_bot, group_photo_messages=lambda update: None)
        updates = [FakeUpdate(1), FakeUpdate(2)]

        module.dispatch_updates(updates)

        self.assertEqual(recording_bot.processed_batches, [updates])

    def test_no_updates_calls_nothing(self):
        recording_bot = RecordingBot()
        module = load_module(recording_bot, group_photo_messages=lambda update: None)

        module.dispatch_updates([])

        self.assertEqual(recording_bot.processed_batches, [])


if __name__ == "__main__":
    unittest.main()
