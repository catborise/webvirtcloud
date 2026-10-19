"""After a refused submission the page starts at the error: base.html's
script focuses the first visible invalid field, else the error message.
The script runs in Node against a stand-in document."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from django.conf import settings

SCRIPT = re.search(
    r"<script>\s*(// after a refused submission.*?)</script>",
    Path(settings.BASE_DIR, "templates/base.html").read_text(),
    re.S,
).group(1)

HARNESS = """
const els = %s;
let focused = null;
for (const e of els) e.focus = () => { focused = e; };
const document = {
  querySelectorAll: sel => els.filter(e => e.sel === sel),
  querySelector: sel => els.find(e => e.sel === sel) || null,
};
%s
console.log(JSON.stringify({focused: focused && focused.name, tabIndex: focused && focused.tabIndex !== undefined ? focused.tabIndex : null}));
"""


class ErrorFocusTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_focus_goes_to_the_error(self):
        invalid = {"sel": ".is-invalid", "offsetParent": {}, "name": "field"}
        hidden = {"sel": ".is-invalid", "offsetParent": None, "name": "hidden"}
        alert = {"sel": "#messages .alert-danger", "offsetParent": {}, "name": "message"}
        for els, expected in (
            ([hidden, invalid, alert], {"focused": "field", "tabIndex": None}),
            ([hidden, alert], {"focused": "message", "tabIndex": -1}),
            ([], {"focused": None, "tabIndex": None}),
        ):
            with self.subTest(expected=expected["focused"]):
                result = subprocess.run(
                    ["node", "-e", HARNESS % (json.dumps(els), SCRIPT)], capture_output=True, text=True, timeout=30
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)
