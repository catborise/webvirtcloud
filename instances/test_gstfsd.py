import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

GSTFSD = Path(__file__).resolve().parent.parent / "conf" / "daemon" / "gstfsd"

# A stand-in for libguestfs: one partition whose files live in a JSON file.
FAKE_GUESTFS = '''
import json, os
STATE = os.environ["FAKE_GUESTFS_STATE"]
def _load():
    with open(STATE) as f:
        return json.load(f)
def _save(state):
    with open(STATE, "w") as f:
        json.dump(state, f)
class GuestFS:
    def __init__(self, **kwargs): pass
    def add_domain(self, name): pass
    def launch(self): pass
    def list_partitions(self): return ["/dev/sda1"]
    def mount(self, part, mountpoint): pass
    def umount(self, part): pass
    def shutdown(self): pass
    def close(self): pass
    def is_file(self, path): return path in _load()["files"]
    def is_dir(self, path): return path in _load()["dirs"]
    def cat(self, path): return _load()["files"][path]
    def write(self, path, content):
        state = _load(); state["files"][path] = content; _save(state)
    def mkdir(self, path):
        state = _load(); state["dirs"].append(path); _save(state)
    def chmod(self, mode, path): pass
'''


def free_port(family=socket.AF_INET, host="127.0.0.1"):
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class GstfsdTestCase(unittest.TestCase):
    """ROADMAP S-08: gstfsd hardening."""

    def setUp(self):
        # novncd (imported by other tests) sets SIGCHLD to SIG_IGN, which hides exit codes.
        previous = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        self.addCleanup(signal.signal, signal.SIGCHLD, previous)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Path(self.tmp.name, "guestfs.py").write_text(FAKE_GUESTFS)
        self.state = Path(self.tmp.name, "state.json")
        self.state.write_text(json.dumps({
            "files": {"/etc/shadow": "root:x:1::::::\n", "/root/.ssh/authorized_keys": "ssh-rsa OLD old@host\n"},
            "dirs": ["/root/.ssh"],
        }))

    def _env(self, **extra):
        return dict(
            os.environ,
            PYTHONPATH=self.tmp.name,
            FAKE_GUESTFS_STATE=str(self.state),
            **extra,
        )

    def _start(self, host, port, family=socket.AF_INET):
        proc = subprocess.Popen(
            [sys.executable, str(GSTFSD)],
            env=self._env(GSTFSD_BIND_HOST=host, GSTFSD_PORT=str(port)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        for _ in range(100):
            if proc.poll() is not None:
                self.fail("gstfsd exited: " + proc.stderr.read())
            try:
                with socket.socket(family, socket.SOCK_STREAM) as s:
                    s.connect((host, port))
                return proc
            except OSError:
                time.sleep(0.05)
        self.fail("gstfsd did not start listening")

    def _request(self, host, port, data, family=socket.AF_INET):
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect((host, port))
            s.sendall(json.dumps(data).encode())
            return json.loads(s.recv(1024))

    def test_refuses_non_loopback_bind_without_opt_in(self):
        result = subprocess.run(
            [sys.executable, str(GSTFSD)],
            env=self._env(GSTFSD_BIND_HOST="0.0.0.0"),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GSTFSD_ALLOW_REMOTE", result.stderr)

    def test_public_key_is_appended_not_overwritten(self):
        port = free_port()
        self._start("127.0.0.1", port)
        reply = self._request(
            "127.0.0.1", port, {"action": "publickey", "vname": "vm", "key": "ssh-ed25519 NEW new@host"}
        )
        self.assertEqual(reply["return"], "success")
        keys = json.loads(self.state.read_text())["files"]["/root/.ssh/authorized_keys"]
        self.assertEqual(keys.splitlines(), ["ssh-rsa OLD old@host", "ssh-ed25519 NEW new@host"])

    @unittest.skipUnless(socket.has_ipv6, "no IPv6")
    def test_listens_on_ipv6_loopback(self):
        try:
            port = free_port(socket.AF_INET6, "::1")
        except OSError:
            self.skipTest("IPv6 loopback not available")
        self._start("::1", port, socket.AF_INET6)
        reply = self._request(
            "::1", port, {"action": "publickey", "vname": "vm", "key": "ssh-ed25519 V6 v6@host"},
            socket.AF_INET6,
        )
        self.assertEqual(reply["return"], "success")
