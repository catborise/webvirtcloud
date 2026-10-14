"""Live checks for the instance jobs: the worker runs them against libvirt
and recovery reads the same outcome back. Runs only with TEST_LIBVIRT_HOST
set (see instances/tests/livetest.py)."""

import os
import time
import unittest

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from vrtManager.connection import connection_manager
from vrtManager.instance import wvmInstance

from appsettings.settings import get_settings
from computes.models import Compute
from instances.jobs import DeleteExternalSnapshot
from instances.models import Instance
from operations import jobs
from operations.models import Operation
from operations.tests import fakes
from operations.worker import Worker

from . import livetest

P = livetest.PREFIX


def wait_for(predicate, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.5)
    return False


class LiveJobsTestCase(TransactionTestCase):
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
        self.compute = Compute.objects.create(name=P + "compute", hostname=host, login=login, password=password,
                                              type=conn_type)
        self.admin = get_user_model().objects.create_superuser("job_live_admin", "a@example.com", "pw")

    def tearDown(self):
        livetest.cleanup(self.conn)

    def vm(self, name, state="stopped"):
        base = livetest.create_volume(self.conn, P + name)
        dom = livetest.define_vm(self.conn, P + name, [base])
        if state == "running":
            dom.create()
        return dom, wvmInstance(*self.args, None, uuid=dom.UUIDString())

    def test_the_worker_deletes_a_snapshot_and_recovery_agrees(self):
        dom, vm = self.vm("job-del", "running")
        vm.create_external_snapshot("one")
        instance = Instance.objects.create(compute=self.compute, name=dom.name(), uuid=dom.UUIDString())
        op = jobs.submit("snapshot.delete", self.admin, {"instance": instance.pk, "name": "one"}, "key-live0001")
        worker = Worker(concurrency=1, tick=0.1, heartbeat_every=1, progress_every=1, exit_on_lost_lease=False)
        thread = fakes.start_worker(worker)
        try:
            self.assertTrue(wait_for(lambda: Operation.objects.get(pk=op.pk).state == Operation.SUCCEEDED, 180))
        finally:
            fakes.stop_worker(worker, thread)
        self.assertEqual(dom.snapshotNum(0), 0)
        Operation.objects.filter(pk=op.pk).update(state=Operation.RUNNING)
        op.refresh_from_db()
        self.assertEqual(DeleteExternalSnapshot().reconcile(op).state, Operation.SUCCEEDED)

    def run_job(self, op):
        worker = Worker(concurrency=1, tick=0.1, heartbeat_every=1, progress_every=1, exit_on_lost_lease=False)
        thread = fakes.start_worker(worker)
        try:
            self.assertTrue(wait_for(lambda: Operation.objects.get(pk=op.pk).state in Operation.TERMINAL, 300))
        finally:
            fakes.stop_worker(worker, thread)
        op.refresh_from_db()
        self.assertEqual(op.state, Operation.SUCCEEDED, op.error)
        return op

    def test_clone_create_and_volume_clone_run_as_jobs(self):
        dom, _vm = self.vm("smoke-src")
        source = Instance.objects.create(compute=self.compute, name=dom.name(), uuid=dom.UUIDString())
        op = self.run_job(jobs.submit("instance.clone", self.admin, {
            "instance": source.pk, "name": P + "smoke-clone", "disk-vda": P + "smoke-clone.qcow2",
            "clone-net-mac-0": "52:54:00:00:31:01"}, "key-smoke001"))
        self.assertEqual(self.conn.lookupByUUIDString(op.intent["uuid"]).name(), P + "smoke-clone")
        self.run_job(jobs.submit("volume.clone", self.admin, {
            "compute": self.compute.pk, "pool": livetest.POOL, "image": P + "smoke-src.qcow2",
            "name": P + "smoke-copy"}, "key-smoke002"))
        self.assertTrue(livetest.volume_exists(self.conn, f"{livetest.POOL_PATH}/{P}smoke-copy.qcow2"))
        op = self.run_job(jobs.submit("instance.create", self.admin, {
            "name": P + "smoke-new", "firmware": "BIOS", "vcpu": 1, "vcpu_mode": "host-model", "memory": 256,
            "networks": "default", "storage": livetest.POOL, "template": P + "smoke-copy.qcow2", "images": "",
            "hdd_size": 0, "cache_mode": "none", "meta_prealloc": False, "virtio": True, "qemu_ga": False,
            "mac": "", "nwfilter": "", "net_model": "virtio", "add_cdrom": "None", "add_input": "default",
            "video": "vga", "listener_addr": "127.0.0.1", "compute": self.compute.pk, "arch": "x86_64",
            "machine": "q35"}, "key-smoke003"))
        self.assertEqual(self.conn.lookupByUUIDString(op.intent["uuid"]).name(), P + "smoke-new")
