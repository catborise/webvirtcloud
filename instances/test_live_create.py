"""Live checks that a failed VM creation leaves no volumes behind.

Runs only with TEST_LIBVIRT_HOST set (see instances/livetest.py).
"""

import os
import unittest

import libvirt
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from vrtManager.connection import connection_manager

from . import livetest

P = livetest.PREFIX


class LiveCreateTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        if not livetest.enabled():
            raise unittest.SkipTest("Set TEST_LIBVIRT_HOST to run the live libvirt tests")
        cls.compute_args = dict(
            hostname=os.environ["TEST_LIBVIRT_HOST"],
            login=os.environ.get("TEST_LIBVIRT_LOGIN", ""),
            password=os.environ.get("TEST_LIBVIRT_PASSWORD", ""),
            type=int(os.environ.get("TEST_LIBVIRT_TYPE", 4)),
        )
        cls.conn = connection_manager.get_connection(*cls.compute_args.values())
        livetest.cleanup(cls.conn)
        livetest.ensure_pool(cls.conn)
        cls.inventory = livetest.inventory(cls.conn)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            livetest.remove_pool(cls.conn)
        finally:
            super().tearDownClass()
        if livetest.inventory(cls.conn) != cls.inventory:
            raise AssertionError("Live tests changed objects outside the test namespace")

    def setUp(self):
        self.compute = Compute.objects.create(name="live-create", details="test", **self.compute_args)
        admin = get_user_model().objects.create_superuser("create_admin", "c@example.com", "pw")
        self.client.force_login(admin)
        self.client.raise_request_exception = False

    def tearDown(self):
        livetest.cleanup(self.conn)

    def create(self, name, **overrides):
        data = {
            "name": P + name,
            "firmware": "BIOS",
            "vcpu": 1,
            "vcpu_mode": "host-model",
            "memory": 128,
            "hdd_size": 1,
            "storage": livetest.POOL,
            "mac": "52:54:00:aa:03:01",
            "networks": "default",
            "nwfilter": "",
            "net_model": "virtio",
            "cache_mode": "none",
            "video": "vga",
            "listener_addr": "0.0.0.0",
            "console_pass": "",
            "add_cdrom": "None",
            "add_input": "None",
            "create": True,
        }
        data.update(overrides)
        return self.client.post(
            reverse("instances:create_instance", args=[self.compute.id, "x86_64", "q35"]), data
        )

    def volumes(self):
        pool = self.conn.storagePoolLookupByName(livetest.POOL)
        pool.refresh(0)
        return set(pool.listVolumes())

    def domain_exists(self, name):
        try:
            self.conn.lookupByName(name)
            return True
        except libvirt.libvirtError:
            return False

    def assert_nothing_left(self, name, response):
        self.assertLess(response.status_code, 500)
        self.assertEqual(self.volumes(), set(), "a volume was left behind")
        self.assertFalse(self.domain_exists(P + name))

    def test_invalid_cache_mode_allocates_nothing(self):
        self.assert_nothing_left("cr-cache", self.create("cr-cache", cache_mode="bogus"))

    def test_malformed_uefi_firmware_allocates_nothing(self):
        self.assert_nothing_left("cr-uefi", self.create("cr-uefi", firmware="UEFI"))

    def test_rejected_definition_removes_the_new_disk(self):
        self.assert_nothing_left("cr-define", self.create("cr-define", video="no-such-model"))

    def test_successful_creation(self):
        response = self.create("cr-ok")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.domain_exists(P + "cr-ok"))
        self.assertEqual(len(self.volumes()), 1)

    def test_api_rejected_definition_removes_the_new_disk(self):
        response = self.client.post(
            f"/api/v1/computes/{self.compute.id}/instances/create/x86_64/q35/",
            {
                "name": P + "cr-api",
                "vcpu": 1,
                "vcpu_mode": "host-model",
                "memory": 128,
                "networks": "default",
                "mac": "52:54:00:aa:03:02",
                "nwfilter": "",
                "storage": livetest.POOL,
                "hdd_size": 1,
                "cache_mode": "none",
                "meta_prealloc": False,
                "virtio": True,
                "qemu_ga": False,
                "console_pass": "",
                "listener_addr": "0.0.0.0",
                "video": "no-such-model",
                "add_cdrom": "None",
                "add_input": "None",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.volumes(), set(), "a volume was left behind")
        self.assertFalse(self.domain_exists(P + "cr-api"))

