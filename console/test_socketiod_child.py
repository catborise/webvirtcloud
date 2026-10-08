"""socketiod's console process ending on its own: the clients are told and
disconnected, the process is reaped and the next connection starts a new one."""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    import socketio
except ImportError:  # pragma: no cover
    socketio = None

ROOT = Path(__file__).resolve().parent.parent
SOCKETIOD = ROOT / "console" / "socketiod"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def children(pid):
    """(pid, state) of the processes whose parent is pid."""
    found = []
    for entry in os.listdir("/proc"):
        try:
            with open("/proc/%s/stat" % entry) as f:
                fields = f.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if int(fields[1]) == pid:
            found.append((int(entry), fields[0]))
    return found


@unittest.skipIf(socketio is None, "python-socketio is not installed")
@unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
class SocketiodChildExitTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "socketiod_child_settings.py").write_text(
            "from webvirtcloud.settings import *  # noqa\n"
            "SERIAL_CONSOLE_ENABLED = True\n"
            "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': %r}}\n"
            % str(Path(tmp.name) / "db.sqlite3")
        )
        self.port = free_port()
        env = dict(
            os.environ,
            DJANGO_SETTINGS_MODULE="socketiod_child_settings",
            PYTHONPATH=os.pathsep.join([tmp.name, str(ROOT)]),
        )
        self.server = subprocess.Popen(
            [sys.executable, str(SOCKETIOD), "-H", "127.0.0.1", "-p", str(self.port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.stop_server)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            self.assertIsNone(self.server.poll(), "socketiod exited at start")
            time.sleep(0.1)
        self.fail("socketiod did not listen within 20 s")

    def stop_server(self):
        self.server.kill()
        self.server.wait()

    def open_console(self):
        """Connect with a malformed token, so the console process fails at once;
        returns the output and whether the server disconnected the client."""
        client = socketio.Client(reconnection=False)
        output = []
        gone = threading.Event()
        client.on("pty_output", lambda data: output.append(data["output"]))
        client.on("disconnect", lambda *args: gone.set())
        client.connect(
            "http://127.0.0.1:%d" % self.port,
            headers={"Cookie": "token=no-such-vm"},
            transports=["polling"],
        )
        try:
            gone.wait(15)
        finally:
            if not gone.is_set():
                client.disconnect()
        return "".join(output), gone.is_set()

    def assert_no_child_left(self):
        end = time.monotonic() + 5
        while children(self.server.pid) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertEqual(children(self.server.pid), [])

    def test_the_client_is_told_and_the_next_connection_starts_again(self):
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                output, disconnected = self.open_console()
                self.assertIn("console closed", output)
                # no traceback of socketiod's own code (on Python 3.13 eventlet
                # prints an ignored _after_fork error in every forked child)
                self.assertNotIn('socketiod", line', output)
                self.assertTrue(disconnected)
                self.assert_no_child_left()
                self.assertIsNone(self.server.poll())
