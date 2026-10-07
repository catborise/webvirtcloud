"""gunicorn runs a bounded number of sync workers."""

import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

from django.conf import settings

CONF = str(Path(settings.BASE_DIR) / "gunicorn.conf.py")


def load(cpus):
    with patch("os.sysconf", return_value=cpus):
        return runpy.run_path(CONF)


class GunicornConfTestCase(unittest.TestCase):
    def test_worker_count_is_capped(self):
        self.assertEqual(load(64)["workers"], 8)

    def test_small_hosts_keep_two_per_cpu_plus_one(self):
        self.assertEqual(load(2)["workers"], 5)

    def test_unknown_cpu_count_falls_back(self):
        self.assertEqual(load(-1)["workers"], 3)
