from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import telegram_album_state as albums


def album_update(update_id: int, message_id: int, group: str, file_id: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "media_group_id": group,
            "from": {"id": 7},
            "photo": [{"file_id": file_id, "file_size": 3}],
            "caption": "личная подпись" if message_id == 1 else None,
        },
    }


class TelegramAlbumStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_path = albums.STATE_FILE
        albums.STATE_FILE = Path(self.temp.name) / "album-state.enc"

    def tearDown(self):
        albums.STATE_FILE = self.original_path
        self.temp.cleanup()

    def test_album_fragments_are_joined_across_batches_and_process_restart(self):
        token = "123456789:fake-token"
        first = album_update(10, 1, "g-1", "private-file-id-1")
        second = album_update(11, 2, "g-1", "private-file-id-2")
        ready, changed = albums.record_and_get_ready([first], token, 100)
        self.assertEqual((ready, changed), ([], True))

        ciphertext = albums.STATE_FILE.read_bytes()
        self.assertNotIn(b"private-file-id-1", ciphertext)
        self.assertNotIn("личная подпись".encode(), ciphertext)

        import importlib
        importlib.reload(albums)
        albums.STATE_FILE = Path(self.temp.name) / "album-state.enc"
        ready, changed = albums.record_and_get_ready([second], token, 200)
        self.assertEqual((ready, changed), ([], True))
        ready, changed = albums.record_and_get_ready([], token, 801)
        self.assertFalse(changed)
        self.assertEqual([[item["update_id"] for item in group] for group in ready], [[10, 11]])
        self.assertTrue(albums.STATE_FILE.exists())
        self.assertTrue(albums.remove_completed(ready, token))
        self.assertFalse(albums.STATE_FILE.exists())

    def test_duplicate_update_does_not_extend_album_settle_window(self):
        token = "123456789:fake-token"
        first = album_update(10, 1, "g-1", "private-file-id-1")
        albums.record_and_get_ready([first], token, 100)
        ready, changed = albums.record_and_get_ready([first], token, 701)
        self.assertFalse(changed)
        self.assertEqual([[item["update_id"] for item in group] for group in ready], [[10]])

    def test_tampered_album_state_is_rejected(self):
        token = "123456789:fake-token"
        albums.record_and_get_ready(
            [album_update(10, 1, "g-1", "private-file-id-1")],
            token,
            100,
        )
        payload = bytearray(albums.STATE_FILE.read_bytes())
        payload[-1] ^= 1
        albums.STATE_FILE.write_bytes(payload)
        with self.assertRaises(albums.AlbumStateError):
            albums.record_and_get_ready([], token, 800)

    def test_ready_album_is_retained_until_successful_processing(self):
        token = "123456789:fake-token"
        update = album_update(10, 1, "g-1", "private-file-id-1")
        albums.record_and_get_ready([update], token, 100)

        ready, changed = albums.record_and_get_ready([], token, 701)
        self.assertEqual(ready, [[update]])
        self.assertFalse(changed)
        self.assertTrue(albums.STATE_FILE.exists())

        albums.defer_failed(ready, token, 701)
        ready, _ = albums.record_and_get_ready([], token, 1000)
        self.assertEqual(ready, [])
        ready, _ = albums.record_and_get_ready([], token, 1001)
        self.assertEqual(ready, [[update]])
        self.assertTrue(albums.remove_completed(ready, token))
        self.assertFalse(albums.STATE_FILE.exists())


if __name__ == "__main__":
    unittest.main()
