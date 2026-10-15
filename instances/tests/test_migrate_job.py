"""A migration runs as a job: the page queues it, the worker migrates."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.urls import reverse

from appsettings.settings import get_settings
from computes.models import Compute
from instances.jobs import MigrateInstance
from instances.models import Instance
from operations.jobs import Context, get_kind
from operations.models import Claim, Operation, acquire

UUID = "31313131-4242-5353-6464-757575757575"


class MigrateJobTestCase(TransactionTestCase):
    def setUp(self):
        get_settings()
        self.source = Compute.objects.create(name="mig-src", hostname="192.0.2.50", login="root", password="", type=1)
        self.dest = Compute.objects.create(name="mig-dst", hostname="192.0.2.51", login="root", password="", type=1)
        self.vm = Instance.objects.create(compute=self.source, name="mig-vm", uuid=UUID)
        self.client.force_login(get_user_model().objects.create_superuser("mig_admin", "a@example.com", "pw"))
        proxy = MagicMock()
        proxy.get_autostart.return_value = False
        proxy.get_nvram.return_value = None
        patch.object(Compute, "status", True).start()
        patch.object(Instance, "proxy", proxy).start()
        self.dest_conn = patch("instances.utils.wvmInstances").start().return_value
        self.dest_conn.moveto.return_value = "live"
        self.addCleanup(patch.stopall)

    def queue(self, **form):
        self.client.post(reverse("instances:migrate", args=[self.vm.pk]),
                         {"compute_id": self.dest.pk, "submission_key": "key-mig00001", **form})
        return Operation.objects.get()

    def start(self, op):
        """The job as the worker takes it."""
        acquire("wvc-test:1:a", "wvc-test")
        Operation.objects.filter(pk=op.pk).update(state=Operation.RUNNING, worker="wvc-test:1:a")
        op.refresh_from_db()
        return Context(op, SimpleNamespace(identity="wvc-test:1:a"))

    def test_the_worker_migrates_and_moves_the_record(self):
        op = self.queue(live_migrate="true")
        self.assertEqual(set(Claim.objects.values_list("resource", "mode")), {
            (f"instance:{UUID}", "exclusive"), (f"name:{self.dest.pk}:mig-vm", "exclusive"),
            (f"compute:{self.source.pk}", "shared"), (f"reconcile:{self.source.pk}", "exclusive"),
            (f"compute:{self.dest.pk}", "shared"), (f"reconcile:{self.dest.pk}", "exclusive")})
        self.dest_conn.moveto.assert_not_called()  # nothing ran in the request
        ctx = self.start(op)
        get_kind(op.kind).precheck(op, ctx)
        get_kind(op.kind).run(op, ctx)
        self.assertTrue(self.dest_conn.moveto.call_args.args[2])  # live
        self.vm.refresh_from_db()
        self.assertEqual(self.vm.compute, self.dest)

    def test_recovery_reads_where_the_vm_is(self):
        op = Operation(params={"instance": self.vm.pk, "target": self.dest.pk},
                       intent={"compute": self.source.pk, "uuid": UUID})
        dom = MagicMock()
        dom.isActive.return_value = False
        with patch("instances.jobs._domain", side_effect=[dom, None]):  # source, destination
            self.assertEqual(MigrateInstance().reconcile(op).state, Operation.FAILED)
        dom.isActive.return_value = True
        dom.jobInfo.return_value = [2] + [0] * 11  # VIR_DOMAIN_JOB_UNBOUNDED
        with patch("instances.jobs._domain", side_effect=[dom, dom]):
            self.assertEqual(MigrateInstance().reconcile(op).state, Operation.RUNNING)
        with patch("instances.jobs._domain", side_effect=[None, dom]):
            self.assertEqual(MigrateInstance().reconcile(op).state, Operation.SUCCEEDED)
        self.vm.refresh_from_db()
        self.assertEqual(self.vm.compute, self.dest)
