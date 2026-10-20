from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pages_build
import telegram_delivery


class PagesBuildTests(unittest.TestCase):
    def test_pages_unavailable_is_logged_but_does_not_clear_pending_flag(self):
        original = telegram_delivery.STATE_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                telegram_delivery.STATE_FILE = Path(tmp) / "delivery-state.json"
                telegram_delivery.set_pages_build_pending(True)
                failed = subprocess.CompletedProcess([], 1, b"", b"unavailable")
                with patch("builtins.print") as log:
                    requested = pages_build.request_build("owner/repo", run=lambda *a, **kw: failed)
                self.assertFalse(requested)
                self.assertTrue(telegram_delivery._load_state()["pages_build_pending"])
                self.assertIn("warning", log.call_args.args[0])
        finally:
            telegram_delivery.STATE_FILE = original

    def test_successful_pages_request_is_reported(self):
        success = subprocess.CompletedProcess([], 0, b"", b"")
        self.assertTrue(pages_build.request_build("owner/repo", run=lambda *a, **kw: success))

if __name__ == "__main__":
    unittest.main()
