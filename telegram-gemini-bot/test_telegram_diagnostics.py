import unittest

from telegram_diagnostics import TelegramDiagnosticsError, diagnose


def valid_api(token, method, payload=None):
    if method == "getMe":
        return {"id": 123, "username": "DanilkaVlogBot"}
    if method == "getWebhookInfo":
        return {"url": ""}
    if method == "getChat":
        return {"id": -100, "username": "danilka2028k"}
    if method == "getChatMember":
        return {"status": "administrator", "can_post_messages": True}
    raise AssertionError(method)


class TelegramDiagnosticsTests(unittest.TestCase):
    def test_reports_safe_read_only_configuration_summary(self):
        calls = []

        def api(token, method, payload=None):
            calls.append((method, payload))
            return valid_api(token, method, payload)

        results = diagnose("do-not-print-token", "@channel-id", "98765", api)
        self.assertEqual(len(results), 5)
        self.assertIn("getMe: @DanilkaVlogBot — OK", results[0])
        self.assertIn("CHANNEL_ID", results[2])
        self.assertEqual(
            [method for method, _ in calls],
            ["getMe", "getWebhookInfo", "getChat", "getChatMember"],
        )
        self.assertNotIn("do-not-print-token", "\n".join(results))

    def test_rejects_wrong_bot_before_other_api_checks(self):
        calls = []

        def api(token, method, payload=None):
            calls.append(method)
            return {"id": 1, "username": "DifferentBot"}

        with self.assertRaisesRegex(TelegramDiagnosticsError, "@DifferentBot"):
            diagnose("secret", "@channel", "123", api)
        self.assertEqual(calls, ["getMe"])

    def test_rejects_active_webhook_without_changing_it(self):
        def api(token, method, payload=None):
            if method == "getMe":
                return {"id": 1, "username": "DanilkaVlogBot"}
            return {"url": "https://example.invalid/hook"}

        with self.assertRaisesRegex(TelegramDiagnosticsError, "webhook"):
            diagnose("secret", "@channel", "123", api)

    def test_rejects_wrong_channel_and_missing_post_permission(self):
        def wrong_channel(token, method, payload=None):
            if method == "getMe":
                return {"id": 1, "username": "DanilkaVlogBot"}
            if method == "getWebhookInfo":
                return {"url": ""}
            return {"username": "otherchannel"}

        with self.assertRaisesRegex(TelegramDiagnosticsError, "CHANNEL_ID"):
            diagnose("secret", "@channel", "123", wrong_channel)

        def no_post(token, method, payload=None):
            if method == "getMe":
                return {"id": 1, "username": "DanilkaVlogBot"}
            if method == "getWebhookInfo":
                return {"url": ""}
            if method == "getChat":
                return {"username": "danilka2028k"}
            return {"status": "administrator", "can_post_messages": False}

        with self.assertRaisesRegex(TelegramDiagnosticsError, "права"):
            diagnose("secret", "@channel", "123", no_post)

    def test_requires_positive_numeric_admin_id(self):
        with self.assertRaisesRegex(TelegramDiagnosticsError, "ADMIN_ID"):
            diagnose("secret", "@channel", "0", valid_api)


if __name__ == "__main__":
    unittest.main()
