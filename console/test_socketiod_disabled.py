import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOCKETIOD = ROOT / "console" / "socketiod"


class SocketiodDisabledTestCase(unittest.TestCase):
    """socketiod must refuse to start unless explicitly enabled."""

    def test_socketiod_exits_when_serial_console_disabled(self):
        # The settings module lives in a temporary directory, so the test does
        # not write into the source tree (read-only checkouts, --parallel).
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "socketiod_disabled_settings.py").write_text(
                "from webvirtcloud.settings import *  # noqa\n"
                "SERIAL_CONSOLE_ENABLED = False\n"
                "SOCKETIO_HOST = '127.0.0.1'\n"
                "SOCKETIO_PORT = 0\n"
            )
            env = dict(
                os.environ,
                DJANGO_SETTINGS_MODULE="socketiod_disabled_settings",
                PYTHONPATH=os.pathsep.join([tmp, str(ROOT)]),
            )
            try:
                result = subprocess.run(
                    [sys.executable, str(SOCKETIOD)],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except subprocess.TimeoutExpired:
                self.fail("socketiod kept running although SERIAL_CONSOLE_ENABLED=False")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SERIAL_CONSOLE_ENABLED", result.stderr)
