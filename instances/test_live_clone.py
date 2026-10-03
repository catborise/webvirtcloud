"""Live checks for cloning (R-08). Runs only with TEST_LIBVIRT_HOST set."""

import os
import unittest

import libvirt
from django.test import SimpleTestCase
from vrtManager.connection import connection_manager
from vrtManager.instance import wvmInstance

from . import livetest

P = livetest.PREFIX


class LiveCloneTestCase(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        if not livetest.enabled():
            raise unittest.SkipTest("Set TEST_LIBVIRT_HOST to run the live libvirt tests")
        cls.args = (
            os.environ["TEST_LIBVIRT_HOST"],
            os.environ.get("TEST_LIBVIRT_LOGIN", ""),
            os.environ.get("TEST_LIBVIRT_PASSWORD", ""),
            int(os.environ.get("TEST_LIBVIRT_TYPE", 4)),
        )
        cls.conn = connection_manager.get_connection(*cls.args)
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

    def tearDown(self):
        livetest.cleanup(self.conn)

    def pool_volumes(self):
        pool = self.conn.storagePoolLookupByName(livetest.POOL)
        pool.refresh(0)
        return set(pool.listVolumes())

    def source(self, name):
        disks = [livetest.create_volume(self.conn, P + f"{name}-{d}") for d in ("a", "b")]
        dom = livetest.define_vm(self.conn, P + name, disks)
        return dom, wvmInstance(*self.args, None, uuid=dom.UUIDString())

    def clone_data(self, name):
        return {
            "name": P + name,
            "disk-vda": P + name + "-a.qcow2",
            "disk-vdb": P + name + "-b.qcow2",
            "disk_owner_uid": 0,
            "disk_owner_gid": 0,
        }

    def test_failed_clone_removes_only_its_own_copies(self):
        _, vm = self.source("clone-src")
        taken = livetest.create_volume(self.conn, P + "clone-dst-b")  # destination of vdb exists
        before = self.pool_volumes()

        with self.assertRaises(Exception):
            vm.clone_instance(self.clone_data("clone-dst"))

        self.assertEqual(self.pool_volumes(), before, "copies left behind or a volume deleted")
        self.assertTrue(livetest.volume_exists(self.conn, taken))
        with self.assertRaises(libvirt.libvirtError):
            self.conn.lookupByName(P + "clone-dst")

    def test_running_vm_is_not_cloned(self):
        dom, vm = self.source("clone-run")
        dom.create()
        before = self.pool_volumes()

        with self.assertRaises(Exception):
            vm.clone_instance(self.clone_data("clone-run-copy"))

        self.assertEqual(self.pool_volumes(), before)

    def test_clone_of_a_stopped_vm(self):
        _, vm = self.source("clone-ok")
        uuid = vm.clone_instance(self.clone_data("clone-ok-copy"))
        clone = self.conn.lookupByUUIDString(uuid)
        self.assertEqual(clone.name(), P + "clone-ok-copy")
        self.assertTrue({P + "clone-ok-copy-a.qcow2", P + "clone-ok-copy-b.qcow2"} <= self.pool_volumes())
