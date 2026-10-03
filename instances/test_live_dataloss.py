"""Live reproductions of the Wave 1a data-loss findings.

Each test asserts the safe behavior and is marked expectedFailure until
its finding is fixed; a fix turns it into an unexpected success, which
fails the run until the marker is removed. Runs only with
TEST_LIBVIRT_HOST set (see instances/livetest.py).
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
from .models import Instance
from .utils import refr

P = livetest.PREFIX


class LiveDataLossTestCase(TestCase):
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
        # Last: a failure above must not leave the class transaction open,
        # which would lock the shared test database for later test classes.
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
        self.compute = Compute.objects.create(name="live-dataloss", details="test", **self.compute_args)
        admin = get_user_model().objects.create_superuser("live_admin", "l@example.com", "pw")
        self.client.force_login(admin)
        self.client.raise_request_exception = False

    def tearDown(self):
        livetest.cleanup(self.conn)

    # helpers

    def vm(self, name, disks, uefi=False):
        dom = livetest.define_vm(self.conn, P + name, disks, uefi=uefi)
        refr(self.compute)
        return dom, Instance.objects.get(compute=self.compute, uuid=dom.UUIDString())

    def post(self, view, instance, data=None):
        return self.client.post(
            reverse(f"instances:{view}", args=[instance.id]),
            data or {},
            HTTP_REFERER=reverse("instances:instance", args=[instance.id]),
        )

    def disk_sources(self, dom, flags=0):
        from lxml import etree

        return {
            d.find("target").get("dev"): d.find("source").get("file")
            for d in etree.fromstring(dom.XMLDesc(flags)).findall("devices/disk")
        }

    # R-03: external snapshots

    @unittest.expectedFailure
    def test_r03_revert_external_snapshot_keeps_disk_added_later(self):
        base = livetest.create_volume(self.conn, P + "r03-a")
        dom, inst = self.vm("r03-a", [base])
        self.post("create_external_snapshot", inst, {"name": "snap"})

        later = livetest.create_volume(self.conn, P + "r03-later")
        dom.attachDeviceFlags(
            f"<disk type='file' device='disk'><driver name='qemu' type='qcow2'/>"
            f"<source file='{later}'/><target dev='vdb' bus='virtio'/></disk>",
            libvirt.VIR_DOMAIN_AFFECT_CONFIG,
        )
        self.post("revert_external_snapshot", inst, {"name": "s1.snap", "date": "", "desc": ""})

        self.assertTrue(livetest.volume_exists(self.conn, later), "revert deleted a disk the snapshot never had")

    @unittest.expectedFailure
    def test_r03_delete_older_external_snapshot_keeps_newer_snapshot_files(self):
        base = livetest.create_volume(self.conn, P + "r03-b")
        dom, inst = self.vm("r03-b", [base])
        self.post("create_external_snapshot", inst, {"name": "older"})
        self.post("create_external_snapshot", inst, {"name": "newer"})
        sources = set(self.disk_sources(dom).values())

        self.post("delete_external_snapshot", inst, {"name": "s1.older"})

        for path in sources:
            self.assertTrue(livetest.volume_exists(self.conn, path), f"{path} was deleted")
        self.assertIn("s1.newer", dom.snapshotListNames(0))
