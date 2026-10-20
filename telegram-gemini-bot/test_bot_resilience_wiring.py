"""Проверяет, что bot.py действительно использует устойчивую цепочку, а не
просто импортирует gemini_resilience и не пользуется её защитой.

Как тесты bot_api_cycle.py, подменяет telebot/google.genai/dotenv фейками перед
загрузкой bot.py — реальные пакеты в CI (проверка без pip install) не
установлены, а секреты (TELEGRAM_BOT_TOKEN и т.д.) не нужны реальные.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import call, patch


class FakeAPIError(Exception):
    def __init__(self, code, message="перегружено"):
        super().__init__(f"{code} {message}")
        self.code = code
        self.status = message


class FakeTelegramError(Exception):
    error_code = 400


class FakeMessage:
    def __init__(self, user_id=1, message_id=1, text="/status"):
        self.from_user = types.SimpleNamespace(id=user_id)
        self.chat = types.SimpleNamespace(id=1, type="private")
        self.message_id = message_id
        self.forward_from_chat = None
        self.text = text
        self.caption = None
        self.photo = []


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
        self.sent_messages = []
        self.sent_media = []

    def message_handler(self, commands=None, content_types=None):
        def decorator(func):
            for name in commands or content_types or ["*"]:
                self.handlers[name] = func
            return func
        return decorator

    def reply_to(self, message, text):
        self.replies.append(text)
        return types.SimpleNamespace(chat=message.chat, message_id=99)

    def send_message(self, chat_id, text):
        self.sent_messages.append((chat_id, text))
        return types.SimpleNamespace(message_id=700 + len(self.sent_messages))

    def send_photo(self, chat_id, photo, caption=None):
        self.sent_media.append(("photo", chat_id, photo.read(), caption))
        return types.SimpleNamespace(message_id=800 + len(self.sent_media))

    def send_video(self, chat_id, video, caption=None):
        self.sent_media.append(("video", chat_id, video.read(), caption))
        return types.SimpleNamespace(message_id=800 + len(self.sent_media))

    def send_voice(self, chat_id, voice, caption=None):
        self.sent_media.append(("voice", chat_id, voice.read(), caption))
        return types.SimpleNamespace(message_id=800 + len(self.sent_media))

    def send_video_note(self, chat_id, data):
        self.sent_media.append(("video_note", chat_id, data.read(), None))
        return types.SimpleNamespace(message_id=800 + len(self.sent_media))

    def send_media_group(self, chat_id, media):
        payload = [
            (item.media.read(), item.caption)
            for item in media
        ]
        self.sent_media.append(("album", chat_id, payload))
        return [types.SimpleNamespace(message_id=900 + index) for index in range(len(payload))]

    def edit_message_text(self, text, chat_id=None, message_id=None):
        self.replies.append(text)


def load_bot_module(gemini_models_plan: dict):
    telebot_mod = types.ModuleType("telebot")
    telebot_mod.TeleBot = FakeTeleBot

    class FakeInputMediaPhoto:
        def __init__(self, media, caption=None):
            self.media = media
            self.caption = caption

    telebot_mod.types = types.SimpleNamespace(InputMediaPhoto=FakeInputMediaPhoto)

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
    def prepare_delivery(self, module, directory):
        module.telegram_delivery.STATE_FILE = Path(directory) / "delivery-state.json"
        return patch.object(module.telegram_delivery, "_persist_checkpoint")

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
        """Проверяет, что цикл дождётся обработчика до подтверждения обновления."""
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
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, text="исходный материал")
            handler = module.bot.handlers.get("text")
            handler(message)
            self.assertEqual(
                module.bot.sent_messages,
                [(module.CHANNEL_ID, "Готовый пост про вечер")],
            )
            self.assertIn("Опубликовано в канале", module.bot.replies[-1])

    def test_non_admin_text_is_never_sent_to_gemini_or_channel(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID + 1, text="чужой текст")
            with patch.object(module, "ask_gemini_text") as ask:
                module.handle_text(message)
            ask.assert_not_called()
            self.assertEqual(module.bot.sent_messages, [])
            self.assertIn("только владельцу", module.bot.replies[-1])

    def test_admin_message_from_group_is_not_processed(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, text="не публиковать из группы")
            message.chat.type = "group"
            with patch.object(module, "ask_gemini_text") as ask:
                module.handle_text(message)
            ask.assert_not_called()
            self.assertEqual(module.bot.sent_messages, [])
            self.assertIn("только владельцу", module.bot.replies[-1])

    def test_ask_command_returns_answer_without_channel_publication(self):
        module = load_bot_module({"gemini-flash-latest": ["Ответ на вопрос"]})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, text="/ask Какой сегодня день?")
            with patch.object(
                module,
                "_generate_content",
                return_value=types.SimpleNamespace(text="Обычный ответ без хэштегов"),
            ) as generate:
                module.bot.handlers["ask"](message)
            self.assertEqual(module.bot.sent_messages, [])
            self.assertIn("не опубликован", module.bot.replies[-1])
            self.assertIn("Обычный ответ без хэштегов", module.bot.replies[-1])
            prompt = generate.call_args.args[0]
            self.assertIn("Какой сегодня день?", prompt)
            self.assertNotIn(module.POST_STYLE_HINT, prompt)

    def test_unknown_command_is_not_sent_to_gemini_or_channel(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, text="/unexpected publish this")
            with patch.object(module, "ask_gemini_text") as ask:
                module.handle_text(message)
            ask.assert_not_called()
            self.assertEqual(module.bot.sent_messages, [])
            self.assertIn("Неизвестная команда", module.bot.replies[-1])

    def test_gemini_error_does_not_publish_original_material(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, text="исходник нельзя публиковать")
            with patch.object(
                module,
                "ask_gemini_text",
                side_effect=module.gemini_resilience.GeminiUnavailableError("недоступен"),
            ):
                module.handle_text(message)
            self.assertEqual(module.bot.sent_messages, [])
            self.assertIn("временно недоступен", module.bot.replies[-1])

    def test_empty_gemini_result_does_not_publish(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, text="материал")
            with patch.object(module, "ask_gemini_text", return_value="  "):
                module.handle_text(message)
            self.assertEqual(module.bot.sent_messages, [])
            self.assertIn("пуст", module.bot.replies[-1])

    def test_redelivery_after_send_before_offset_ack_is_not_duplicated(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, message_id=81, text="материал")
            with patch.object(module, "ask_gemini_text", return_value="Готовый пост"):
                module.handle_text(message)
                # Simulate a process exit before the cycle can acknowledge getUpdates.
                module.handle_text(message)
            self.assertEqual(len(module.bot.sent_messages), 1)
            self.assertIn("уже было опубликовано", module.bot.replies[-1])

    def test_restart_uses_persisted_attempt_and_does_not_publish_twice(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, message_id=82, text="материал")
            with patch.object(module, "ask_gemini_text", return_value="Готовый пост"):
                module.handle_text(message)
                importlib.reload(module.telegram_delivery)
                module.telegram_delivery.STATE_FILE = Path(tmp) / "delivery-state.json"
                module.handle_text(message)
            self.assertEqual(len(module.bot.sent_messages), 1)

    def test_ambiguous_telegram_failure_is_not_retried_automatically(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            message = FakeMessage(user_id=module.ADMIN_ID, message_id=83, text="материал")

            def timed_out(*_args, **_kwargs):
                raise TimeoutError("connection ended after request")

            module.bot.send_message = timed_out
            with patch.object(module, "ask_gemini_text", return_value="Готовый пост"):
                module.handle_text(message)
                self.assertIn("Результат отправки неопределён", module.bot.replies[-1])
                module.handle_text(message)
            self.assertIn("мог успеть опубликовать", module.bot.replies[-1])

    def test_photo_result_is_published_with_original_attachment(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            media = Path(tmp) / "photo.jpg"
            media.write_bytes(b"photo")
            message = FakeMessage(user_id=module.ADMIN_ID, message_id=84)
            message.photo = [types.SimpleNamespace(file_id="photo-file", file_size=5)]
            with (
                patch.object(module, "download_telegram_file", return_value=str(media)) as download,
                patch.object(module, "ask_gemini_about_media", return_value="Подпись к фотографии"),
            ):
                module.handle_photo(message)
            download.assert_called_once_with("photo-file", ".jpg")
            self.assertEqual(
                module.bot.sent_media,
                [("photo", module.CHANNEL_ID, b"photo", "Подпись к фотографии")],
            )
            self.assertEqual(module.bot.sent_messages, [])

    def test_video_result_is_published_with_original_attachment(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            media = Path(tmp) / "video.mp4"
            media.write_bytes(b"video")
            message = FakeMessage(user_id=module.ADMIN_ID, message_id=841)
            message.content_type = "video"
            with (
                patch.object(module, "download_telegram_file", return_value=str(media)),
                patch.object(module, "ask_gemini_about_media", return_value="Подпись к видео"),
            ):
                module.process_media_message(message, "video-file", ".mp4", "Опиши видео", 5)
            self.assertEqual(
                module.bot.sent_media,
                [("video", module.CHANNEL_ID, b"video", "Подпись к видео")],
            )

    def test_voice_and_video_note_keep_their_media_in_channel(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            voice = Path(tmp) / "voice.ogg"
            note = Path(tmp) / "note.mp4"
            voice.write_bytes(b"voice")
            note.write_bytes(b"note")
            with (
                patch.object(module, "ask_gemini_about_media", return_value="Результат Gemini"),
                patch.object(
                    module,
                    "download_telegram_file",
                    side_effect=[str(voice), str(note)],
                ),
            ):
                voice_message = FakeMessage(user_id=module.ADMIN_ID, message_id=842)
                voice_message.content_type = "voice"
                module.process_media_message(voice_message, "voice-file", ".ogg", "Транскрибируй")
                note_message = FakeMessage(user_id=module.ADMIN_ID, message_id=843)
                note_message.content_type = "video_note"
                module.process_media_message(note_message, "note-file", ".mp4", "Транскрибируй")
            self.assertEqual(
                module.bot.sent_media,
                [
                    ("voice", module.CHANNEL_ID, b"voice", "Результат Gemini"),
                    ("video_note", module.CHANNEL_ID, b"note", None),
                ],
            )
            self.assertIn((module.CHANNEL_ID, "Результат Gemini"), module.bot.sent_messages)

    def test_long_media_caption_is_sent_as_text_after_attachment(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            media = Path(tmp) / "photo.jpg"
            media.write_bytes(b"photo")
            message = FakeMessage(user_id=module.ADMIN_ID, message_id=844)
            with patch.object(module.bot, "send_message") as send_text:
                module.publish_to_channel(
                    message,
                    "x" * 1200,
                    media_paths=[str(media)],
                    media_type="photo",
                )
            self.assertEqual(module.bot.sent_media[0][:3], ("photo", module.CHANNEL_ID, b"photo"))
            self.assertIsNone(module.bot.sent_media[0][3])
            send_text.assert_called_once_with(module.CHANNEL_ID, "x" * 1200)

    def test_successful_channel_send_calls_site_publication_handler(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            media = Path(tmp) / "photo.jpg"
            media.write_bytes(b"photo")
            message = FakeMessage(user_id=module.ADMIN_ID, message_id=8441)
            published = []
            module.published_post_handler = (
                lambda text, sent, paths, kind: published.append((text, sent, paths, kind))
            )

            self.assertTrue(
                module.publish_to_channel(
                    message,
                    "Опубликовано",
                    media_paths=[str(media)],
                    media_type="photo",
                )
            )

            self.assertEqual(len(published), 1)
            text, sent_messages, paths, kind = published[0]
            self.assertEqual(text, "Опубликовано")
            self.assertEqual([item.message_id for item in sent_messages], [801])
            self.assertEqual(paths, [str(media)])
            self.assertEqual(kind, "photo")

    def test_partial_media_delivery_is_not_marked_retryable(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            media = Path(tmp) / "photo.jpg"
            media.write_bytes(b"photo")
            message = FakeMessage(user_id=module.ADMIN_ID, message_id=845)

            def fail_after_media(*_args, **_kwargs):
                raise FakeTelegramError("rejected")

            module.bot.send_message = fail_after_media

            module.publish_to_channel(
                message,
                "x" * 1200,
                media_paths=[str(media)],
                media_type="photo",
            )

            state = json.loads(module.telegram_delivery.STATE_FILE.read_text("utf-8"))
            self.assertEqual(list(state["deliveries"].values()), ["attempted"])
            self.assertIn("Часть поста уже могла появиться", module.bot.replies[-1])
            self.assertEqual(len(module.bot.sent_media), 1)

            module.publish_to_channel(
                message,
                "x" * 1200,
                media_paths=[str(media)],
                media_type="photo",
            )
            self.assertEqual(len(module.bot.sent_media), 1)

    def test_album_result_is_published_as_one_message(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp, self.prepare_delivery(module, tmp):
            first_media = Path(tmp) / "first.jpg"
            second_media = Path(tmp) / "second.jpg"
            first_media.write_bytes(b"first")
            second_media.write_bytes(b"second")
            first = FakeMessage(user_id=module.ADMIN_ID, message_id=85)
            second = FakeMessage(user_id=module.ADMIN_ID, message_id=86)
            first.photo = [types.SimpleNamespace(file_id="photo-1", file_size=5)]
            second.photo = [types.SimpleNamespace(file_id="photo-2", file_size=5)]
            with (
                patch.object(
                    module,
                    "download_telegram_file",
                    side_effect=[str(first_media), str(second_media)],
                ) as download,
                patch.object(module, "ask_gemini_about_album", return_value="Один пост по альбому"),
            ):
                module.process_album([first, second])
            self.assertEqual(
                download.call_args_list,
                [
                    call("photo-1", ".jpg"),
                    call("photo-2", ".jpg"),
                ],
            )
            self.assertEqual(module.bot.sent_messages, [])
            self.assertEqual(len(module.bot.sent_media), 1)
            kind, chat_id, photos = module.bot.sent_media[0]
            self.assertEqual((kind, chat_id), ("album", module.CHANNEL_ID))
            self.assertEqual(photos, [(b"first", "Один пост по альбому"), (b"second", None)])

    def test_checkpoint_failure_prevents_channel_send(self):
        module = load_bot_module({})
        with tempfile.TemporaryDirectory() as tmp:
            module.telegram_delivery.STATE_FILE = Path(tmp) / "delivery-state.json"
            with patch.object(
                module.telegram_delivery,
                "_persist_checkpoint",
                side_effect=module.telegram_delivery.DeliveryCheckpointError("checkpoint failed"),
            ), patch.object(module, "ask_gemini_text", return_value="Готовый пост"):
                module.handle_text(FakeMessage(user_id=module.ADMIN_ID, message_id=87, text="материал"))
            self.assertEqual(module.bot.sent_messages, [])
            self.assertTrue(module.delivery_checkpoint_errors)


if __name__ == "__main__":
    unittest.main()
