import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOCKETIOD = ROOT / "console" / "socketiod"


class SocketiodDisabledTestCase(unittest.TestCase):
    """ROADMAP S-01: socketiod must refuse to start unless explicitly enabled."""

    def test_socketiod_exits_when_serial_console_disabled(self):
        settings_dir = ROOT / "console" / "_test_settings"
        settings_dir.mkdir(exist_ok=True)
        try:
            (settings_dir / "__init__.py").write_text("")
            (settings_dir / "disabled.py").write_text(
                "from webvirtcloud.settings import *  # noqa\n"
                "SERIAL_CONSOLE_ENABLED = False\n"
                "SOCKETIO_HOST = '127.0.0.1'\n"
                "SOCKETIO_PORT = 0\n"
            )
            env = dict(
                os.environ,
                DJANGO_SETTINGS_MODULE="console._test_settings.disabled",
                PYTHONPATH=str(ROOT),
            )
            # Other test modules import novncd, which sets SIGCHLD to SIG_IGN in
            # this process; with that, the child's exit status is lost (always 0).
            previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
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
            finally:
                signal.signal(signal.SIGCHLD, previous_sigchld)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("SERIAL_CONSOLE_ENABLED", result.stderr)
        finally:
            for f in settings_dir.glob("*"):
                if f.is_file():
                    f.unlink()
            for d in settings_dir.glob("__pycache__"):
                for f in d.glob("*"):
                    f.unlink()
                d.rmdir()
            settings_dir.rmdir()
