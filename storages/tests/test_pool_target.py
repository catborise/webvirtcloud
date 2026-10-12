"""The storage pool target check runs in linear time and accepts what it did."""

import time

from django.test import SimpleTestCase
from storages.forms import AddStgPool


def target_error(target):
    form = AddStgPool({"name": "pool1", "stg_type": "dir", "target": target})
    form.is_valid()
    return "target" in form.errors


class PoolTargetTests(SimpleTestCase):
    def test_accepted_and_refused_targets(self):
        for target, refused in (
            ("/var/lib/libvirt/images", False),
            ("/srv/iso-pool", False),
            ("/a//b", False),
            ("a-b.c_d/e", False),
            ("/a/-b", True),  # a dash right after a slash
            ("-x", True),
            ("/a b", True),
        ):
            with self.subTest(target=target):
                self.assertEqual(target_error(target), refused)

    def test_a_crafted_target_is_checked_at_once(self):
        # the previous expression backtracked exponentially: 22 dashes took over a second
        start = time.monotonic()
        self.assertTrue(target_error("," + "-" * 98 + "!"))
        self.assertLess(time.monotonic() - start, 1)
