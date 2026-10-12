"""The app log is bounded (rotates) and written to a deterministic absolute
path, not the process working directory."""

import os

from django.conf import settings
from django.test import TestCase


class LoggingConfigTestCase(TestCase):
    def test_default_handler_rotates(self):
        handler = settings.LOGGING["handlers"]["default"]
        self.assertEqual(handler["class"], "logging.handlers.RotatingFileHandler")
        self.assertGreater(handler["maxBytes"], 0)
        self.assertGreater(handler["backupCount"], 0)

    def test_log_path_is_absolute_and_not_cwd(self):
        handler = settings.LOGGING["handlers"]["default"]
        self.assertTrue(os.path.isabs(handler["filename"]), handler["filename"])

    def test_missing_log_directory_is_created_on_settings_import(self):
        # A clean checkout / CI / Docker build may not have the log's parent dir;
        # importing settings (logging config) must create it, not crash.
        import subprocess
        import sys
        import tempfile

        tmp = tempfile.mkdtemp()
        log = os.path.join(tmp, "nested", "wvc.log")
        env = {
            **os.environ,
            "WEBVIRTCLOUD_LOG_FILE": log,
            "DJANGO_SETTINGS_MODULE": "webvirtcloud.settings",
        }
        result = subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isdir(os.path.dirname(log)), log)
