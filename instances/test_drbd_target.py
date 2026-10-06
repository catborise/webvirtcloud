"""drbd_status runs ssh with the compute's login@hostname; a compute whose
values would inject an ssh option (an existing or bypassed row) is skipped."""

import unittest
from types import SimpleNamespace

from instances.views import _valid_ssh_target


class DrbdTargetTestCase(unittest.TestCase):
    def test_valid_target(self):
        self.assertTrue(_valid_ssh_target(SimpleNamespace(login="root", hostname="192.0.2.1")))

    def test_option_injection_is_skipped(self):
        for login, hostname in (("-oProxyCommand=x", "192.0.2.1"), ("root", "-x"), ("root", "a b")):
            with self.subTest(login=login, hostname=hostname):
                self.assertFalse(_valid_ssh_target(SimpleNamespace(login=login, hostname=hostname)))
