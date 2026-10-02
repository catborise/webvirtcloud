import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path

GSTFSD = Path(__file__).resolve().parent.parent / "conf" / "daemon" / "gstfsd"


class GstfsdBindTestCase(unittest.TestCase):
    """ROADMAP S-08: gstfsd is an unauthenticated root service; no remote bind by default."""

    def _run(self, **env):
        # novncd (imported by other tests) sets SIGCHLD to SIG_IGN, which hides exit codes.
        previous = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        try:
            return subprocess.run(
                [sys.executable, str(GSTFSD)],
                env=dict(os.environ, **env),
                capture_output=True,
                text=True,
                timeout=20,
            )
        finally:
            signal.signal(signal.SIGCHLD, previous)

    def test_refuses_non_loopback_bind_without_opt_in(self):
        result = self._run(GSTFSD_BIND_HOST="0.0.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GSTFSD_ALLOW_REMOTE", result.stderr)
