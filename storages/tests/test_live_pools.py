"""Live check that a directory pool created on the storages page starts
when its directory does not exist yet: libvirt builds it. Runs only with
TEST_LIBVIRT_HOST set."""

import os
import unittest

import libvirt
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.tests import livetest
from vrtManager.connection import connection_manager

POOL = livetest.PREFIX + "dir"
PATH = f"/var/lib/libvirt/{POOL}"


@unittest.skipUnless(livetest.enabled(), "Set TEST_LIBVIRT_HOST to run the live libvirt tests")
class LivePoolsTestCase(TestCase):
    def setUp(self):
        args = dict(
            hostname=os.environ["TEST_LIBVIRT_HOST"],
            login=os.environ.get("TEST_LIBVIRT_LOGIN", ""),
            password=os.environ.get("TEST_LIBVIRT_PASSWORD", ""),
            type=int(os.environ.get("TEST_LIBVIRT_TYPE", 1)),
        )
        self.conn = connection_manager.get_connection(*args.values())
        self.remove_pool()
        self.addCleanup(self.remove_pool)
        self.compute = Compute.objects.create(name="live-pools", **args)
        self.client.force_login(get_user_model().objects.create_superuser("live-pools", "lp@example.com", "x"))

    def remove_pool(self):
        try:
            pool = self.conn.storagePoolLookupByName(POOL)
        except libvirt.libvirtError:
            return
        if pool.isActive():
            pool.destroy()
            pool.delete(0)  # removes the directory
        pool.undefine()

    def test_a_missing_directory_is_built(self):
        response = self.client.post(
            reverse("storages", args=[self.compute.id]),
            {"create": "", "name": POOL, "stg_type": "dir", "target": PATH},
        )
        self.assertRedirects(response, reverse("storage", args=[self.compute.id, POOL]), fetch_redirect_response=False)
        pool = self.conn.storagePoolLookupByName(POOL)
        self.assertTrue(pool.isActive())
        self.assertTrue(pool.autostart())
