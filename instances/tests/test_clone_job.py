"""VM clone runs as a job: the page queues it, the worker clones and records it."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import libvirt
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TransactionTestCase
from django.urls import reverse

from accounts.models import UserInstance
from appsettings.settings import get_settings
from computes.models import Compute
from instances.jobs import CloneInstance
from instances.models import Instance
from operations.jobs import Context
from vrtManager.util import OperationError
from operations.models import Claim, Operation, acquire


def proxy():
    p = MagicMock()
    p.get_status.return_value = 5
    p.get_disk_devices.return_value = [{"dev": "vda", "image": "src.qcow2", "size": 1 << 30}]
    p.get_net_devices.return_value = [{"mac": "52:54:00:aa:bb:cc"}]
    p.get_vcpu.return_value = 1
    p.get_memory.return_value = 512
    return p


def pool(volumes):
    p = MagicMock()
    p.listVolumes.return_value = volumes
    return p


class CloneJobTestCase(TransactionTestCase):
    def setUp(self):
        get_settings()
        self.admin = get_user_model().objects.create_superuser("clone_admin", "a@example.com", "pw")
        self.client.force_login(self.admin)
        self.compute = Compute.objects.create(name="clone-c", hostname="192.0.2.30", login="root", password="", type=1)
        self.source = Instance.objects.create(compute=self.compute, name="src", uuid="12121212-3434-5656-7878-909090909090")
        patcher = patch("instances.models.wvmInstance", return_value=proxy())
        self.proxy = patcher.start().return_value
        self.addCleanup(patcher.stop)

    def test_the_page_queues_the_clone_and_the_worker_makes_and_records_it(self):
        response = self.client.post(reverse("instances:clone", args=[self.source.pk]),
                                    {"name": "copy", "disk-vda": "copy.qcow2", "clone-net-mac-0": "52:54:00:00:00:01",
                                     "submission_key": "key-clone001"})
        self.assertRedirects(response, reverse("instances:instance", args=[self.source.pk]),
                             fetch_redirect_response=False)
        op = Operation.objects.get()
        c = self.compute.pk
        self.assertEqual(set(Claim.objects.values_list("resource", "mode")), {
            (f"compute:{c}", "shared"), (f"reconcile:{c}", "exclusive"),
            (f"instance:{self.source.uuid}", "shared"), (f"name:{c}:copy", "exclusive")})
        self.proxy.clone_instance.assert_not_called()

        acquire("wvc-test:1:a", "wvc-test")
        Operation.objects.filter(pk=op.pk).update(state=Operation.RUNNING, worker="wvc-test:1:a")
        op.refresh_from_db()
        CloneInstance().run(op, Context(op, SimpleNamespace(identity="wvc-test:1:a")))
        self.proxy.clone_instance.assert_called_once_with(op.intent["clone"], op.intent["uuid"])
        clone = Instance.objects.get(uuid=op.intent["uuid"])
        self.assertTrue(UserInstance.objects.filter(instance=clone, user=self.admin).exists())

    def queued(self):
        self.client.post(reverse("instances:clone", args=[self.source.pk]),
                         {"name": "copy", "disk-vda": "copy.qcow2", "clone-net-mac-0": "52:54:00:00:00:01",
                          "submission_key": "key-clone002"})
        return Operation.objects.get()

    def test_the_worker_refuses_a_clone_whose_access_was_revoked(self):
        op = self.queued()
        self.admin.is_superuser = False
        self.admin.save()
        with self.assertRaises(PermissionDenied):
            CloneInstance().precheck(op, None)

    def test_the_worker_refuses_a_clone_whose_source_moved(self):
        op = self.queued()
        other = Compute.objects.create(name="clone-d", hostname="192.0.2.31", login="root", password="", type=1)
        Instance.objects.filter(pk=self.source.pk).update(compute=other)
        with self.assertRaises(OperationError):
            CloneInstance().precheck(op, None)

    @patch("instances.jobs.wvmConnect")
    def test_recovery(self, conn):
        op = Operation(user=self.admin, intent={"compute": self.compute.pk, "uuid": "abababab-0000-1111-2222-333333333333",
                                                "name": "copy", "clone": {"name": "copy", "disk-vda": "copy.qcow2"}})
        self.assertEqual(CloneInstance().reconcile(op).state, Operation.SUCCEEDED)  # the VM is defined
        self.assertTrue(Instance.objects.filter(uuid=op.intent["uuid"]).exists())
        missing = libvirt.libvirtError("no domain")
        missing.err = (libvirt.VIR_ERR_NO_DOMAIN, libvirt.VIR_FROM_QEMU, "no domain")
        conn.return_value.wvm.lookupByUUIDString.side_effect = missing
        conn.return_value.wvm.listAllStoragePools.return_value = [pool(["copy.qcow2", "other.qcow2"])]
        outcome = CloneInstance().reconcile(op)
        self.assertEqual(outcome.state, Operation.UNCERTAIN)
        self.assertIn("copy.qcow2", outcome.error)

    @patch("instances.jobs.wvmConnect")
    def test_a_clone_that_left_nothing_failed(self, conn):
        op = Operation(user=self.admin, intent={"compute": self.compute.pk, "uuid": "abababab-0000-1111-2222-333333333333",
                                                "name": "copy", "clone": {"name": "copy", "disk-vda": "copy.qcow2"}})
        missing = libvirt.libvirtError("no domain")
        missing.err = (libvirt.VIR_ERR_NO_DOMAIN, libvirt.VIR_FROM_QEMU, "no domain")
        conn.return_value.wvm.lookupByUUIDString.side_effect = missing
        conn.return_value.wvm.listAllStoragePools.return_value = [pool(["other.qcow2"])]
        self.assertEqual(CloneInstance().reconcile(op).state, Operation.FAILED)
