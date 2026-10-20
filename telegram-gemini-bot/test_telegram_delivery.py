import importlib
import json
import subprocess
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
        self.assertEqual(telegram_delivery.delivery_status("fake-token", 10, 30), "attempted")

    def test_definitive_failure_can_be_retried_after_new_delivery_attempt(self):
        with patch.object(telegram_delivery, "_persist_checkpoint"):
            telegram_delivery.begin_delivery("fake-token", 10, 31)
            telegram_delivery.finish_delivery("fake-token", 10, 31, "failed")
            status, should_send = telegram_delivery.begin_delivery("fake-token", 10, 31)
        self.assertEqual(status, "attempted")
        self.assertTrue(should_send)

    def test_crash_after_checkpoint_before_send_is_not_retried(self):
        with patch.object(telegram_delivery, "_persist_checkpoint") as checkpoint:
            status, should_send = telegram_delivery.begin_delivery("fake-token", 10, 32)
            checkpoint.assert_called_once()
            retry_status, retry_should_send = telegram_delivery.begin_delivery("fake-token", 10, 32)
        self.assertEqual((status, should_send), ("attempted", True))
        self.assertEqual((retry_status, retry_should_send), ("attempted", False))
        checkpoint.assert_called_once()

    def test_rebase_conflict_prevents_checkpoint_from_being_reported_durable(self):
        successful = subprocess.CompletedProcess([], 0, "", "")
        failed = subprocess.CompletedProcess([], 1, "", "conflict")
        responses = [successful, failed, successful, failed, successful, failed, successful]
        commands = []

        def git(*args):
            commands.append(args)
            return responses.pop(0)

        with (
            patch.dict(
                "os.environ",
                {"GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "main"},
            ),
            patch.object(telegram_delivery, "ROOT", Path(self.temp.name)),
            patch.object(telegram_delivery, "_git", side_effect=git),
        ):
            with self.assertRaises(telegram_delivery.DeliveryCheckpointError):
                telegram_delivery._persist_checkpoint()

        self.assertIn(("rebase", "FETCH_HEAD"), commands)
        self.assertIn(("rebase", "--abort"), commands)

    def test_pages_pending_flag_survives_delivery_state_updates(self):
        telegram_delivery.set_pages_build_pending(True)
        with patch.object(telegram_delivery, "_persist_checkpoint"):
            telegram_delivery.begin_delivery("fake-token", 10, 33)
        state = json.loads(telegram_delivery.STATE_FILE.read_text("utf-8"))
        self.assertTrue(state["pages_build_pending"])
        telegram_delivery.set_pages_build_pending(False)
        self.assertFalse(json.loads(telegram_delivery.STATE_FILE.read_text("utf-8"))["pages_build_pending"])


if __name__ == "__main__":
    unittest.main()
