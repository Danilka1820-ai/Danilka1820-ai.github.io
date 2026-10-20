import tempfile
import unittest
from pathlib import Path

import telegram_retry_state as retry


class TelegramRetryStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_path = retry.STATE_FILE
        retry.STATE_FILE = Path(self.temp.name) / "retry-state.enc"

    def tearDown(self):
        retry.STATE_FILE = self.original_path
        self.temp.cleanup()

    def test_failed_update_is_encrypted_and_retried_with_backoff(self):
        token = "123456789:fake-token"
        update = {
            "update_id": 12,
            "message": {"text": "private material", "chat": {"id": 12345}},
        }
        state = retry.TelegramRetryState(token)
        state.defer("admin", update, 100)
        state.save()

        ciphertext = retry.STATE_FILE.read_bytes()
        self.assertNotIn(b"private material", ciphertext)
        self.assertNotIn(b"12345", ciphertext)
        state = retry.TelegramRetryState(token)
        self.assertEqual(state.due("admin", 399), [])
        self.assertEqual(state.due("admin", 400), [update])

        state.defer("admin", update, 400, retry_after=900)
        state.save()
        state = retry.TelegramRetryState(token)
        self.assertEqual(state.due("admin", 1299), [])
        self.assertEqual(state.due("admin", 1300), [update])

    def test_completed_update_removes_encrypted_queue(self):
        token = "123456789:fake-token"
        update = {"update_id": 3, "message": {"text": "retry"}}
        state = retry.TelegramRetryState(token)
        state.defer("channel", update, 100)
        state.save()

        state = retry.TelegramRetryState(token)
        state.complete([3])
        self.assertTrue(state.save())
        self.assertFalse(retry.STATE_FILE.exists())

    def test_tampered_queue_is_rejected(self):
        token = "123456789:fake-token"
        state = retry.TelegramRetryState(token)
        state.defer("admin", {"update_id": 1, "message": {}}, 100)
        state.save()
        payload = bytearray(retry.STATE_FILE.read_bytes())
        payload[-1] ^= 1
        retry.STATE_FILE.write_bytes(payload)
        with self.assertRaises(retry.RetryStateError):
            retry.TelegramRetryState(token)


if __name__ == "__main__":
    unittest.main()
