import unittest
from types import SimpleNamespace

from bot_utils import (
    MAX_SOURCE_TEXT_CHARS,
    TELEGRAM_MESSAGE_LIMIT,
    clip_for_telegram,
    require_admin_id,
    user_id_from_message,
    validate_media_size,
    validate_source_text,
)


class BotUtilsTests(unittest.TestCase):
    def test_admin_id_is_required_and_numeric(self):
        for value in (None, "", "abc", "0", "-5"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                require_admin_id(value)
        self.assertEqual(require_admin_id("123456"), 123456)

    def test_sender_id_is_read_safely(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=42))
        self.assertEqual(user_id_from_message(message), 42)
        self.assertIsNone(user_id_from_message(SimpleNamespace()))

    def test_long_reply_is_clipped_to_telegram_limit(self):
        result = clip_for_telegram("x" * (TELEGRAM_MESSAGE_LIMIT + 100))
        self.assertLessEqual(len(result), TELEGRAM_MESSAGE_LIMIT)
        self.assertIn("Ответ сокращён", result)

    def test_source_text_validation(self):
        self.assertEqual(validate_source_text("  текст  "), "текст")
        with self.assertRaises(ValueError):
            validate_source_text(" ")
        with self.assertRaises(ValueError):
            validate_source_text("x" * (MAX_SOURCE_TEXT_CHARS + 1))

    def test_media_limit(self):
        validate_media_size(None)
        validate_media_size(1024)
        with self.assertRaises(ValueError):
            validate_media_size(21 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
