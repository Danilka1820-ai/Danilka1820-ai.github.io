import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import telegram_delivery


class TelegramDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_state_file = telegram_delivery.STATE_FILE
        telegram_delivery.STATE_FILE = Path(self.temp.name) / "state.json"

    def tearDown(self):
        telegram_delivery.STATE_FILE = self.original_state_file
        self.temp.cleanup()

    def test_attempt_is_saved_before_durable_checkpoint(self):
        def inspect_checkpoint():
            state = json.loads(telegram_delivery.STATE_FILE.read_text("utf-8"))
            self.assertEqual(list(state["deliveries"].values()), ["attempted"])

        with patch.object(telegram_delivery, "_persist_checkpoint", side_effect=inspect_checkpoint):
            status, should_send = telegram_delivery.begin_delivery("fake-token", 10, 20)

        self.assertEqual(status, "attempted")
        self.assertTrue(should_send)

    def test_state_uses_opaque_hashes_and_survives_module_reload(self):
        token = "123456789:fake-token"
        with patch.object(telegram_delivery, "_persist_checkpoint"):
            telegram_delivery.begin_delivery(token, 13579, 24680)
            telegram_delivery.finish_delivery(token, 13579, 24680, "published")

        serialized = telegram_delivery.STATE_FILE.read_text("utf-8")
        self.assertNotIn(token, serialized)
        self.assertNotIn("13579", serialized)
        self.assertNotIn("24680", serialized)

        importlib.reload(telegram_delivery)
        telegram_delivery.STATE_FILE = Path(self.temp.name) / "state.json"
        status, should_send = telegram_delivery.begin_delivery(token, 13579, 24680)
        self.assertEqual(status, "published")
        self.assertFalse(should_send)

    def test_ambiguous_attempt_is_not_automatically_retried(self):
        with patch.object(telegram_delivery, "_persist_checkpoint"):
            telegram_delivery.begin_delivery("fake-token", 10, 30)
            status, should_send = telegram_delivery.begin_delivery("fake-token", 10, 30)
        self.assertEqual(status, "attempted")
        self.assertFalse(should_send)

    def test_definitive_failure_can_be_retried_after_new_delivery_attempt(self):
        with patch.object(telegram_delivery, "_persist_checkpoint"):
            telegram_delivery.begin_delivery("fake-token", 10, 31)
            telegram_delivery.finish_delivery("fake-token", 10, 31, "failed")
            status, should_send = telegram_delivery.begin_delivery("fake-token", 10, 31)
        self.assertEqual(status, "attempted")
        self.assertTrue(should_send)


if __name__ == "__main__":
    unittest.main()
