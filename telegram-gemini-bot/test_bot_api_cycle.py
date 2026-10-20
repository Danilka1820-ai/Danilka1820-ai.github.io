import importlib.util
import json
import tempfile
import traceback
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
    def test_bot_api_request_error_redacts_token(self):
        token = "123456789:fake-token-value"
        error = cycle.requests.ConnectionError(
            f"request failed for https://api.telegram.org/bot{token}/getUpdates"
        )
        with patch.object(cycle.requests, "post", side_effect=error):
            with self.assertRaises(cycle.TelegramApiError) as raised:
                cycle.bot_api(token, "getUpdates")
        self.assertNotIn(token, str(raised.exception))
        self.assertIn("[СКРЫТО]", str(raised.exception))
        formatted_traceback = "".join(traceback.format_exception(raised.exception))
        self.assertNotIn(token, formatted_traceback)
        self.assertIn("Telegram API getUpdates failed", formatted_traceback)
        self.assertIn("request failed", formatted_traceback)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_file_download_error_redacts_token(self):
        token = "123456789:fake-token-value"
        error = cycle.requests.ConnectionError(
            f"request failed for https://api.telegram.org/file/bot{token}/photos/file.jpg"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(cycle, "MEDIA_DIR", Path(tmp)),
                patch.object(cycle, "bot_api", return_value={"file_path": "photos/file.jpg"}),
                patch.object(cycle.requests, "get", side_effect=error),
            ):
                with self.assertRaises(cycle.TelegramApiError) as raised:
                    cycle._download_media_file(token, {"file_id": "file-id"}, 999999999, 0, "jpg")
        self.assertNotIn(token, str(raised.exception))
        self.assertIn("[СКРЫТО]", str(raised.exception))
        formatted_traceback = "".join(traceback.format_exception(raised.exception))
        self.assertNotIn(token, formatted_traceback)
        self.assertIn("Telegram file download failed", formatted_traceback)
        self.assertIn("request failed", formatted_traceback)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_channel_filter(self):
        updates = [
            channel_update(1, 10),
            channel_update(2, 11, username="other"),
        ]
        selected = cycle.select_channel_updates(updates, "@danilka2028k")
        self.assertEqual([item["update_id"] for item in selected], [1])

    def test_admin_filter_excludes_other_senders_and_channel_posts(self):
        updates = [
            {"update_id": 1, "message": {"from": {"id": 7}, "text": "owner"}},
            {"update_id": 2, "message": {"from": {"id": 8}, "text": "stranger"}},
            channel_update(3, 12, text="channel post"),
            {"update_id": 4, "message": {"text": "missing sender"}},
        ]
        self.assertEqual(cycle.select_admin_updates(updates, 7), [updates[0]])

    def test_ack_uses_next_offset(self):
        with patch.object(cycle, "bot_api", return_value=[]) as api:
            cycle.acknowledge_updates("token", 99)
        api.assert_called_once_with(
            "token",
            "getUpdates",
            {"offset": 100, "limit": 1, "timeout": 0},
        )

    def test_cycle_emits_pending_offset_only_after_processing(self):
        updates = [{"update_id": 10, "message": {}}]
        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake", "CHANNEL_ID": "@test"}),
            patch.object(cycle, "fetch_updates", return_value=updates),
            patch.object(cycle, "select_channel_updates", return_value=[]),
            patch.object(cycle, "ingest_channel_updates", return_value=0),
            patch.object(cycle, "process_admin_updates", return_value=1),
            patch.object(cycle, "_write_cycle_outputs") as write_outputs,
        ):
            cycle.main()
        write_outputs.assert_called_once_with(10, 0)

    def test_processing_failure_does_not_emit_pending_offset(self):
        updates = [{"update_id": 11, "message": {}}]
        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake", "CHANNEL_ID": "@test"}),
            patch.object(cycle, "fetch_updates", return_value=updates),
            patch.object(cycle, "select_channel_updates", return_value=[]),
            patch.object(cycle, "ingest_channel_updates", return_value=0),
            patch.object(cycle, "process_admin_updates", side_effect=RuntimeError("checkpoint failed")),
            patch.object(cycle, "_write_cycle_outputs") as write_outputs,
        ):
            with self.assertRaisesRegex(RuntimeError, "checkpoint failed"):
                cycle.main()
        write_outputs.assert_not_called()

    def test_cycle_outputs_include_channel_post_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "github-output"
            with patch.dict("os.environ", {"GITHUB_OUTPUT": str(output)}):
                cycle._write_cycle_outputs(20, 3)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "pending_offset=21\nchannel_posts=3\n",
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
