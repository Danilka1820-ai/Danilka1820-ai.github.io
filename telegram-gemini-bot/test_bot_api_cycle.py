import importlib.util
import json
import sys
import tempfile
import traceback
import types
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


def valid_configuration_api(token, method, payload=None, timeout=35):
    if method == "getMe":
        return {"id": 123, "username": "DanilkaVlogBot"}
    if method == "getWebhookInfo":
        return {"url": ""}
    if method == "getChat":
        return {"id": -100123, "username": "danilka2028k"}
    if method == "getChatMember":
        return {"status": "administrator", "can_post_messages": True}
    raise AssertionError(method)


class BotApiCycleTests(unittest.TestCase):
    def test_ack_is_after_successful_persistence_and_pages_request_is_not_a_prerequisite(self):
        workflow = Path(__file__).parents[1] / ".github" / "workflows" / "telegram-gemini-bot.yml"
        content = workflow.read_text(encoding="utf-8")
        persist_at = content.index("id: persist")
        ack_at = content.index("Подтвердить обработанную пачку Telegram")
        pages_at = content.index("Запросить публикацию GitHub Pages")
        persist_failure_at = content.index('if [ "$pushed" != "true" ]; then')
        saved_output_at = content.index('echo "saved=true"')
        self.assertLess(persist_at, ack_at)
        self.assertLess(ack_at, pages_at)
        self.assertLess(persist_failure_at, saved_output_at)
        self.assertIn("exit 1", content[persist_failure_at:saved_output_at])
        self.assertIn(
            "if: success() && steps.persist.outputs.saved == 'true' && steps.cycle.outputs.pending_offset != ''",
            content,
        )
        self.assertIn("steps.persist.outputs.pages_pending == 'true'", content)

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
            {"update_id": 1, "message": {"from": {"id": 7}, "chat": {"type": "private"}, "text": "owner"}},
            {"update_id": 2, "message": {"from": {"id": 8}, "chat": {"type": "private"}, "text": "stranger"}},
            {"update_id": 5, "message": {"from": {"id": 7}, "chat": {"type": "group"}, "text": "group"}},
            channel_update(3, 12, text="channel post"),
            {"update_id": 4, "message": {"text": "missing sender"}},
        ]
        self.assertEqual(cycle.select_admin_updates(updates, 7), [updates[0]])

    def test_admin_updates_are_dispatched_through_telebot_instance(self):
        dispatched = []

        class FakeUpdate:
            @staticmethod
            def de_json(raw):
                return json.loads(raw)

        fake_telebot = types.SimpleNamespace(
            types=types.SimpleNamespace(
                Update=FakeUpdate,
            )
        )
        fake_bot_instance = types.SimpleNamespace(
            process_new_updates=dispatched.extend,
        )
        fake_bot_module = types.SimpleNamespace(
            bot=fake_bot_instance,
            delivery_checkpoint_errors=[],
            processing_errors=[],
            telegram_delivery=types.SimpleNamespace(
                delivery_status=lambda token, chat_id, message_id: None,
            ),
            notify_delivery_status=lambda chat_id, message_id, status: None,
            group_photo_messages=lambda update: None,
            process_album=lambda messages: True,
        )
        album_state = types.SimpleNamespace(
            record_and_get_ready=lambda updates, token, now: ([], False),
        )
        update = {
            "update_id": 7,
            "message": {
                "message_id": 70,
                "from": {"id": 17},
                "chat": {"id": 17, "type": "private"},
                "text": "материал",
            },
        }
        with (
            patch.dict(
                "os.environ",
                {"ADMIN_ID": "17", "TELEGRAM_BOT_TOKEN": "fake-token"},
            ),
            patch.dict(
                sys.modules,
                {
                    "telebot": fake_telebot,
                    "bot": fake_bot_module,
                    "telegram_album_state": album_state,
                    "bot_utils": types.SimpleNamespace(
                        require_admin_id=lambda value: int(value),
                    ),
                },
            ),
        ):
            processed = cycle.process_admin_updates([update])

        self.assertEqual(processed, 1)
        self.assertEqual(dispatched, [update])

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
            patch.dict(
                "os.environ",
                {"TELEGRAM_BOT_TOKEN": "fake", "CHANNEL_ID": "@test", "ADMIN_ID": "17"},
            ),
            patch.object(cycle, "bot_api", side_effect=valid_configuration_api),
            patch.object(cycle, "fetch_updates", return_value=updates),
            patch.object(cycle, "select_channel_updates", return_value=[]),
            patch.object(cycle, "ingest_channel_updates", return_value=0),
            patch.object(cycle, "process_admin_updates", return_value=1),
            patch.object(cycle, "_write_cycle_outputs") as write_outputs,
        ):
            cycle.main()
        write_outputs.assert_called_once_with(10, 0)

    def test_channel_media_failure_does_not_emit_pending_offset(self):
        updates = [channel_update(12, 542)]
        with (
            patch.dict(
                "os.environ",
                {"TELEGRAM_BOT_TOKEN": "fake", "CHANNEL_ID": "@test", "ADMIN_ID": "17"},
            ),
            patch.object(cycle, "bot_api", side_effect=valid_configuration_api),
            patch.object(cycle, "fetch_updates", return_value=updates),
            patch.object(cycle, "select_channel_updates", return_value=updates),
            patch.object(
                cycle,
                "ingest_channel_updates",
                side_effect=cycle.TelegramApiError("media download failed"),
            ),
            patch.object(cycle, "process_admin_updates") as process_admin,
            patch.object(cycle, "_write_cycle_outputs") as write_outputs,
        ):
            with self.assertRaisesRegex(cycle.TelegramApiError, "media download failed"):
                cycle.main()
        process_admin.assert_not_called()
        write_outputs.assert_not_called()

    def test_processing_failure_does_not_emit_pending_offset(self):
        updates = [{"update_id": 11, "message": {}}]
        with (
            patch.dict(
                "os.environ",
                {"TELEGRAM_BOT_TOKEN": "fake", "CHANNEL_ID": "@test", "ADMIN_ID": "17"},
            ),
            patch.object(cycle, "bot_api", side_effect=valid_configuration_api),
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

    def test_edit_clearing_caption_and_media_updates_existing_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "data" / "posts.json"
            posts.parent.mkdir(parents=True)
            posts.write_text(
                json.dumps(
                    [
                        {
                            "id": "danilka2028k/540",
                            "text": "Старая подпись #старое",
                            "tags": ["старое"],
                            "media": [
                                {
                                    "type": "photo",
                                    "src": "assets/posts/old.jpg",
                                    "telegramMessageId": 540,
                                    "telegramFileUniqueId": "old",
                                }
                            ],
                            "telegramMessageIds": [540],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            edited = {
                "update_id": 20,
                "edited_channel_post": {
                    "message_id": 540,
                    "date": 1780000000,
                    "chat": {"id": -100123, "username": "danilka2028k", "type": "channel"},
                },
            }
            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "POSTS_FILE", posts),
                patch.object(cycle, "MEDIA_DIR", root / "assets" / "posts"),
            ):
                self.assertEqual(cycle.ingest_channel_updates("t", [edited], "danilka2028k"), 1)

            result = json.loads(posts.read_text("utf-8"))[0]
            self.assertEqual(result["text"], " ")
            self.assertEqual(result["tags"], [])
            self.assertEqual(result["media"], [])

    def test_voice_channel_post_is_saved_as_playable_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "assets" / "posts" / "541-0.ogg"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"ogg-audio")
            message = {
                "message_id": 541,
                "voice": {"file_id": "voice-file", "file_unique_id": "voice-unique"},
            }
            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "_download_media_file", return_value=target) as download,
            ):
                media = cycle._media_from_message("fake-token", message, 541, 0)

            download.assert_called_once_with("fake-token", message["voice"], 541, 0, "ogg")
            self.assertEqual(
                media,
                [
                    {
                        "type": "audio",
                        "src": "assets/posts/541-0.ogg",
                        "size": len(b"ogg-audio"),
                        "telegramMessageId": 541,
                        "telegramFileUniqueId": "voice-unique",
                    }
                ],
            )

    def test_album_merges_across_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "data" / "posts.json"
            media = root / "assets" / "posts"

            def fake_media(_token, message, _post_number, start_index, **kwargs):
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
            self.assertEqual(len(payload[0]["media"]), 2            )
            self.assertNotEqual(payload[0]["media"][0]["src"], payload[0]["media"][1]["src"])

    def test_media_download_failure_does_not_save_a_text_only_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "data" / "posts.json"
            message = channel_update(13, 543, text="Подпись")
            message["channel_post"]["photo"] = [{"file_id": "photo-file"}]
            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "POSTS_FILE", posts),
                patch.object(cycle, "_download_media_file", side_effect=cycle.TelegramApiError("download failed")),
            ):
                with self.assertRaisesRegex(cycle.TelegramApiError, "Не удалось сохранить фото"):
                    cycle.ingest_channel_updates("fake-token", [message], "danilka2028k")
            self.assertFalse(posts.exists())

    def test_unchanged_channel_media_is_not_downloaded_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "assets" / "posts" / "danilka2028k-543-0.jpg"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"existing media")
            old_entry = {
                "type": "photo",
                "src": "assets/posts/danilka2028k-543-0.jpg",
                "telegramFileUniqueId": "same-file",
            }
            message = {
                "message_id": 543,
                "photo": [{"file_id": "new-id", "file_unique_id": "same-file"}],
            }
            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "_download_media_file") as download,
            ):
                media = cycle._media_from_message("token", message, 543, 0, old_entry)

            self.assertEqual(media, [old_entry])
            download.assert_not_called()

    def test_replaced_channel_media_is_downloaded_over_old_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "assets" / "posts" / "danilka2028k-543-0.jpg"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"old")
            old_entry = {
                "type": "photo",
                "src": "assets/posts/danilka2028k-543-0.jpg",
                "telegramFileUniqueId": "old-file",
            }
            message = {
                "message_id": 543,
                "photo": [{"file_id": "new-id", "file_unique_id": "new-file"}],
            }
            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "_download_media_file", return_value=original) as download,
                patch.object(cycle, "_optimize_image"),
            ):
                cycle._media_from_message("token", message, 543, 0, old_entry)

            self.assertTrue(download.call_args.kwargs["force_download"])

    def test_unchanged_video_repairs_only_missing_variant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "assets" / "posts" / "danilka2028k-543-0.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            old_entry = {
                "type": "video",
                "src": "assets/posts/danilka2028k-543-0.mp4",
                "srcMid": "assets/posts/missing-mid.mp4",
                "telegramFileUniqueId": "same-video",
            }
            message = {
                "message_id": 543,
                "video": {"file_id": "new-id", "file_unique_id": "same-video"},
            }
            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "_download_media_file") as download,
                patch.object(cycle, "_make_video_variant", return_value=None) as variant,
            ):
                media = cycle._media_from_message("token", message, 543, 0, old_entry)

            download.assert_not_called()
            variant.assert_called_once()
            self.assertNotIn("srcMid", media[0])

    def test_channel_group_failure_is_queued_while_other_posts_are_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "data" / "posts.json"
            broken = channel_update(13, 543, text="Не терять")
            broken["channel_post"]["photo"] = [{"file_id": "bad-photo"}]
            good = channel_update(14, 544, text="Сохранить")
            failed = []

            def media(_token, message, _post_number, _index, **kwargs):
                if message["message_id"] == 543:
                    raise cycle.TelegramApiError("download failed")
                return []

            with (
                patch.object(cycle, "ROOT", root),
                patch.object(cycle, "POSTS_FILE", posts),
                patch.object(cycle, "MEDIA_DIR", root / "assets" / "posts"),
                patch.object(cycle, "_media_from_message", side_effect=media),
            ):
                saved = cycle.ingest_channel_updates(
                    "token",
                    [broken, good],
                    "danilka2028k",
                    on_failure=lambda updates, error: failed.append((updates, error)),
                )

            self.assertEqual(saved, 1)
            self.assertEqual([item["update_id"] for item in failed[0][0]], [13])
            self.assertEqual(
                [item["id"] for item in json.loads(posts.read_text("utf-8"))],
                ["danilka2028k/544"],
            )

    def test_main_stops_before_polling_if_token_is_for_another_bot(self):
        with (
            patch.dict(
                "os.environ",
                {"TELEGRAM_BOT_TOKEN": "fake", "CHANNEL_ID": "@test", "ADMIN_ID": "17"},
            ),
            patch.object(cycle, "bot_api", return_value={"username": "WrongBot"}),
            patch.object(cycle, "fetch_updates") as fetch,
        ):
            with self.assertRaisesRegex(RuntimeError, "@WrongBot"):
                cycle.main()
        fetch.assert_not_called()

    def test_admin_failure_is_encrypted_and_does_not_block_later_messages(self):
        import telegram_retry_state

        original_path = telegram_retry_state.STATE_FILE
        with tempfile.TemporaryDirectory() as tmp:
            telegram_retry_state.STATE_FILE = Path(tmp) / "retry-state.enc"
            dispatched = []

            class FakeUpdate:
                @staticmethod
                def de_json(raw):
                    return json.loads(raw)

            def dispatch(updates):
                update = updates[0]
                dispatched.append(update["update_id"])
                if update["message"]["text"] == "fails":
                    fake_bot_module.processing_errors.append(RuntimeError("Gemini unavailable"))

            fake_bot_module = types.SimpleNamespace(
                bot=types.SimpleNamespace(process_new_updates=dispatch),
                delivery_checkpoint_errors=[],
                processing_errors=[],
                telegram_delivery=types.SimpleNamespace(
                    delivery_status=lambda token, chat_id, message_id: None,
                ),
                notify_delivery_status=lambda chat_id, message_id, status: None,
                group_photo_messages=lambda update: None,
                process_album=lambda messages: True,
            )
            album_state = types.SimpleNamespace(
                record_and_get_ready=lambda updates, token, now: ([], False),
            )
            updates = [
                {
                    "update_id": 21,
                    "message": {
                        "message_id": 210,
                        "from": {"id": 17},
                        "chat": {"id": 17, "type": "private"},
                        "text": "fails",
                    },
                },
                {
                    "update_id": 22,
                    "message": {
                        "message_id": 220,
                        "from": {"id": 17},
                        "chat": {"id": 17, "type": "private"},
                        "text": "works",
                    },
                },
            ]
            try:
                with (
                    patch.dict(
                        "os.environ",
                        {"ADMIN_ID": "17", "TELEGRAM_BOT_TOKEN": "fake-token"},
                    ),
                    patch.dict(
                        sys.modules,
                        {
                            "telebot": types.SimpleNamespace(
                                types=types.SimpleNamespace(Update=FakeUpdate)
                            ),
                            "bot": fake_bot_module,
                            "telegram_album_state": album_state,
                            "bot_utils": types.SimpleNamespace(
                                require_admin_id=lambda value: int(value),
                            ),
                        },
                    ),
                ):
                    processed = cycle.process_admin_updates(updates, now=100)
                self.assertEqual(processed, 2)
                self.assertEqual(dispatched, [21, 22])
                self.assertEqual(
                    telegram_retry_state.TelegramRetryState("fake-token").due("admin", 400),
                    [updates[0]],
                )
                ciphertext = telegram_retry_state.STATE_FILE.read_bytes()
                self.assertNotIn(b"Gemini unavailable", ciphertext)
                self.assertNotIn(b"fails", ciphertext)
            finally:
                telegram_retry_state.STATE_FILE = original_path

    def test_already_attempted_owner_post_skips_gemini_and_notifies_owner(self):
        import telegram_retry_state

        original_path = telegram_retry_state.STATE_FILE
        with tempfile.TemporaryDirectory() as tmp:
            telegram_retry_state.STATE_FILE = Path(tmp) / "retry-state.enc"
            dispatched = []
            notifications = []

            class FakeUpdate:
                @staticmethod
                def de_json(raw):
                    return json.loads(raw)

            fake_bot_module = types.SimpleNamespace(
                bot=types.SimpleNamespace(process_new_updates=dispatched.extend),
                delivery_checkpoint_errors=[],
                processing_errors=[],
                telegram_delivery=types.SimpleNamespace(
                    delivery_status=lambda token, chat_id, message_id: "attempted",
                ),
                notify_delivery_status=lambda *args: notifications.append(args),
                group_photo_messages=lambda update: None,
                process_album=lambda messages: True,
            )
            album_state = types.SimpleNamespace(
                record_and_get_ready=lambda updates, token, now: ([], False),
            )
            update = {
                "update_id": 31,
                "message": {
                    "message_id": 310,
                    "from": {"id": 17},
                    "chat": {"id": 17, "type": "private"},
                    "text": "Материал, публикация которого неопределённа",
                },
            }
            try:
                with (
                    patch.dict(
                        "os.environ",
                        {"ADMIN_ID": "17", "TELEGRAM_BOT_TOKEN": "fake-token"},
                    ),
                    patch.dict(
                        sys.modules,
                        {
                            "telebot": types.SimpleNamespace(
                                types=types.SimpleNamespace(Update=FakeUpdate)
                            ),
                            "bot": fake_bot_module,
                            "telegram_album_state": album_state,
                            "bot_utils": types.SimpleNamespace(
                                require_admin_id=lambda value: int(value),
                            ),
                        },
                    ),
                ):
                    processed = cycle.process_admin_updates([update], now=100)
                self.assertEqual(processed, 1)
                self.assertEqual(dispatched, [])
                self.assertEqual(notifications, [(17, 310, "attempted")])
            finally:
                telegram_retry_state.STATE_FILE = original_path


if __name__ == "__main__":
    unittest.main()
