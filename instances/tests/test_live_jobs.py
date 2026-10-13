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
