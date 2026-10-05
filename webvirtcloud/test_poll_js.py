"""Pages poll the server through static/js/poll.js: one request at a time,
none while the page is hidden. Its behaviour is checked with Node."""

import shutil
import subprocess
import unittest
from pathlib import Path

from django.conf import settings

BASE = Path(settings.BASE_DIR)
POLLING_TEMPLATES = ["instances/templates/instance.html", "computes/templates/overview.html"]


class PollTestCase(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_poll_js(self):
        result = subprocess.run(
            ["node", str(BASE / "webvirtcloud/js_tests/poll.test.js"), str(BASE / "static/js/poll.js")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pages_poll_through_poll_js(self):
        for template in POLLING_TEMPLATES:
            with self.subTest(template=template):
                source = (BASE / template).read_text()
                self.assertNotIn("setInterval", source)
                self.assertIn("pollWhileVisible(", source)
