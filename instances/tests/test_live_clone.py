"""Live checks for cloning. Runs only with TEST_LIBVIRT_HOST set."""

import hashlib
import os
import time
import unittest
from xml.etree import ElementTree

import libvirt
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase
from vrtManager.connection import connection_manager
from vrtManager.instance import wvmInstance

from appsettings.settings import get_settings
from computes.models import Compute
from instances.models import Instance
from operations import jobs
from operations.models import Operation
from operations.tests import fakes
from operations.worker import Worker

from . import livetest
from .test_live_jobs import wait_for

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
            vm.clone_instance(self.clone_data("clone-dst"), "abababab-0000-1111-2222-333333333333")

        self.assertEqual(self.pool_volumes(), before, "copies left behind or a volume deleted")
        self.assertTrue(livetest.volume_exists(self.conn, taken))
        with self.assertRaises(libvirt.libvirtError):
            self.conn.lookupByName(P + "clone-dst")

    def test_running_vm_is_not_cloned(self):
        dom, vm = self.source("clone-run")
        dom.create()
        before = self.pool_volumes()

        with self.assertRaises(Exception):
            vm.clone_instance(self.clone_data("clone-run-copy"), "abababab-0000-1111-2222-333333333333")

        self.assertEqual(self.pool_volumes(), before)

    def test_clone_of_a_uefi_vm_has_its_own_copy_of_the_nvram(self):
        disk = livetest.create_volume(self.conn, P + "clone-uefi-a")
        dom = livetest.define_vm(self.conn, P + "clone-uefi", [disk], uefi=True)
        dom.createWithFlags(libvirt.VIR_DOMAIN_START_PAUSED)  # libvirt makes the NVRAM file at the first start
        dom.destroy()
        vm = wvmInstance(*self.args, None, uuid=dom.UUIDString())
        source = vm.get_nvram()

        uuid = vm.clone_instance(
            {"name": P + "clone-uefi-copy", "disk-vda": P + "clone-uefi-copy-a.qcow2", "disk_owner_uid": 0, "disk_owner_gid": 0}, "abababab-0000-1111-2222-333333333333"
        )

        clone = self.conn.lookupByUUIDString(uuid)
        path = ElementTree.fromstring(clone.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)).findtext("os/nvram")
        self.assertEqual(path, os.path.join(os.path.dirname(source), P + "clone-uefi-copy_VARS.fd"))
        with vm._nvram_pool() as pool:
            copy = pool.storageVolLookupByName(os.path.basename(path))
            self.assertEqual(copy.info()[1], pool.storageVolLookupByName(os.path.basename(source)).info()[1])
        clone.createWithFlags(libvirt.VIR_DOMAIN_START_PAUSED)  # qemu opens the copy
        self.assertEqual(
            ElementTree.fromstring(clone.XMLDesc(0)).findtext("os/nvram"), path, "libvirt replaced the copy"
        )
        clone.destroy()

    def test_clone_of_a_stopped_vm(self):
        _, vm = self.source("clone-ok")
        uuid = vm.clone_instance(self.clone_data("clone-ok-copy"), "abababab-0000-1111-2222-333333333333")
        clone = self.conn.lookupByUUIDString(uuid)
        self.assertEqual(clone.name(), P + "clone-ok-copy")
        self.assertTrue({P + "clone-ok-copy-a.qcow2", P + "clone-ok-copy-b.qcow2"} <= self.pool_volumes())


MIB = 1024 * 1024


class LiveCloneJobTestCase(TransactionTestCase):
    """The instance.clone job copies a running VM through the worker."""

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

    def setUp(self):
        get_settings()
        host, login, password, conn_type = self.args
        self.compute = Compute.objects.create(
            name=P + "compute", hostname=host, login=login, password=password, type=conn_type
        )
        self.admin = get_user_model().objects.create_superuser("clone_live_admin", "a@example.com", "pw")
        self.worker = Worker(concurrency=1, tick=0.1, heartbeat_every=1, progress_every=1, exit_on_lost_lease=False)
        thread = fakes.start_worker(self.worker)
        self.addCleanup(fakes.stop_worker, self.worker, thread)

    def tearDown(self):
        livetest.cleanup(self.conn)

    def pool(self):
        pool = self.conn.storagePoolLookupByName(livetest.POOL)
        pool.refresh(0)
        return pool

    def upload(self, vol, data):
        stream = self.conn.newStream(0)
        vol.upload(stream, 0, len(data), 0)
        for offset in range(0, len(data), MIB):
            stream.send(data[offset : offset + MIB])
        stream.finish()

    def sha256(self, vol):
        stream = self.conn.newStream(0)
        vol.download(stream, 0, 0, 0)
        digest = hashlib.sha256()
        while chunk := stream.recv(MIB):
            digest.update(chunk)
        stream.finish()
        return digest.hexdigest()

    def submit(self, source, name, mac, key):
        fields = {"instance": source.pk, "name": P + name, "disk-vda": P + name + ".raw", "clone-net-mac-0": mac}
        return jobs.submit("instance.clone", self.admin, fields, key)

    def ended(self, op):
        self.assertTrue(wait_for(lambda: Operation.objects.get(pk=op.pk).state in Operation.TERMINAL, 300))
        op.refresh_from_db()
        return op

    def test_a_running_vm_is_cloned_and_a_cancelled_clone_leaves_nothing(self):
        path = livetest.create_volume(self.conn, P + "live-src", fmt="raw")
        self.upload(self.pool().storageVolLookupByName(P + "live-src.raw"), os.urandom(64 * MIB))
        dom = livetest.define_vm(self.conn, P + "live-src", [path], disk_format="raw")
        dom.create()
        xml = dom.XMLDesc(0)
        source = Instance.objects.create(compute=self.compute, name=dom.name(), uuid=dom.UUIDString())

        op = self.ended(self.submit(source, "live-copy", "52:54:00:00:32:01", "key-liveclone1"))
        self.assertEqual(op.state, Operation.SUCCEEDED, op.error)
        clone = self.conn.lookupByUUIDString(op.intent["uuid"])
        self.assertEqual(clone.name(), P + "live-copy")
        self.assertEqual(clone.info()[0], libvirt.VIR_DOMAIN_SHUTOFF)
        pool = self.pool()
        self.assertEqual(
            self.sha256(pool.storageVolLookupByName(P + "live-copy.raw")),
            self.sha256(pool.storageVolLookupByName(P + "live-src.raw")),
        )
        self.assertEqual(dom.info()[0], libvirt.VIR_DOMAIN_RUNNING)
        self.assertEqual(dom.XMLDesc(0), xml)

        op = self.submit(source, "live-cancel", "52:54:00:00:32:02", "key-liveclone2")
        end = time.monotonic() + 60
        while Operation.objects.get(pk=op.pk).state == Operation.QUEUED and time.monotonic() < end:
            time.sleep(0.05)
        jobs.cancel(op.pk, self.admin)
        op = self.ended(op)
        self.assertEqual(op.state, Operation.CANCELLED, op.error)
        self.assertNotIn(P + "live-cancel.raw", self.pool().listVolumes())
        with self.assertRaises(libvirt.libvirtError):
            self.conn.lookupByName(P + "live-cancel")
        self.assertEqual(dom.info()[0], libvirt.VIR_DOMAIN_RUNNING)
