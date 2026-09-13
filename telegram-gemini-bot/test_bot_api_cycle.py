import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("bot_api_cycle.py")
SPEC = importlib.util.spec_from_file_location("bot_api_cycle_under_test", MODULE_PATH)
cycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cycle)


def channel_update(update_id, message_id, *, text="", username="danilka2028k", group=None):
    message = {
        "message_id": message_id,
        "date": 1780000000,
        "chat": {"id": -100123, "username": username, "type": "channel"},
    }
    if text:
        message["text"] = text
    if group:
        message["media_group_id"] = group
    return {"update_id": update_id, "channel_post": message}


class BotApiCycleTests(unittest.TestCase):
    def test_channel_filter(self):
        updates = [
            channel_update(1, 10),
            channel_update(2, 11, username="other"),
        ]
        selected = cycle.select_channel_updates(updates, "@danilka2028k")
        self.assertEqual([item["update_id"] for item in selected], [1])

    def test_ack_uses_next_offset(self):
        with patch.object(cycle, "bot_api", return_value=[]) as api:
            cycle.acknowledge_updates("token", 99)
        api.assert_called_once_with(
            "token",
            "getUpdates",
            {"offset": 100, "limit": 1, "timeout": 0},
        )

    def test_text_post_is_idempotent_and_edit_updates_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "data" / "posts.json"
            media = root / "assets" / "posts"
            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "POSTS_FILE", posts),
                patch.object(cycle, "MEDIA_DIR", media),
                patch.object(cycle, "_media_from_message", return_value=[]),
            ):
                first = channel_update(1, 540, text="Первый текст #москва")
                self.assertEqual(
                    cycle.ingest_channel_updates("t", [first], "danilka2028k"),
                    1,
                )
                edited = {
                    "update_id": 2,
                    "edited_channel_post": {
                        **first["channel_post"],
                        "text": "Исправленный текст #дневник",
                    },
                }
                self.assertEqual(
                    cycle.ingest_channel_updates("t", [edited], "danilka2028k"),
                    1,
                )

            payload = json.loads(posts.read_text("utf-8"))
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["id"], "danilka2028k/540")
            self.assertEqual(payload[0]["text"], "Исправленный текст #дневник")
            self.assertEqual(payload[0]["tags"], ["дневник"])

    def test_album_merges_across_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "data" / "posts.json"
            media = root / "assets" / "posts"

            def fake_media(_token, message, _post_number, start_index):
                message_id = message["message_id"]
                return [
                    {
                        "type": "photo",
                        "src": f"assets/posts/{message_id}-{start_index}.jpg",
                        "telegramMessageId": message_id,
                        "telegramFileUniqueId": f"f{message_id}",
                    }
                ]

            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "POSTS_FILE", posts),
                patch.object(cycle, "MEDIA_DIR", media),
                patch.object(cycle, "_media_from_message", side_effect=fake_media),
            ):
                first = channel_update(1, 600, text="Альбом", group="g1")
                second = channel_update(2, 601, group="g1")
                cycle.ingest_channel_updates("t", [first], "danilka2028k")
                cycle.ingest_channel_updates("t", [second], "danilka2028k")

            payload = json.loads(posts.read_text("utf-8"))
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["telegramMessageIds"], [600, 601])
            self.assertEqual(len(payload[0]["media"]), 2)


if __name__ == "__main__":
    unittest.main()
