"""A migration is logged where it runs (migrate_instance), for the VM page
and the API alike: the mode it used on success, the error on failure."""

from unittest.mock import MagicMock, patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from logs.models import Logs
from rest_framework.test import APIClient

from instances.models import Instance
from instances.utils import migrate_instance
from vrtManager import util


class MigrationLogTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("ml-admin", "ml@example.com", "pw")
        self.source = Compute.objects.create(name="ml-src", hostname="198.51.100.8", login="root", password="", type=2)
        self.dest = Compute.objects.create(name="ml-dst", hostname="198.51.100.9", login="root", password="", type=2)
        self.vm = Instance.objects.create(compute=self.source, name="vm", uuid="u-ml")

    def migrate(self, outcome, **request):
        with patch.object(Compute, "status", True), \
             patch("instances.utils.wvmInstances") as instances, \
             patch("instances.utils.wvmInstance"), \
             patch.object(Instance, "proxy", MagicMock()), \
             patch.object(Instance, "autostart", False):
            if isinstance(outcome, Exception):
                instances.return_value.moveto.side_effect = outcome
            else:
                instances.return_value.moveto.return_value = outcome
            return migrate_instance(self.dest, self.vm, self.admin, **request)

    def test_success_names_the_mode_used(self):
        self.migrate("non-live, compressed", live=False, compress=True)
        log = Logs.objects.get()
        self.assertEqual(log.message, "Instance is migrated(non-live, compressed) to 198.51.100.9")
        self.assertEqual((log.user, log.host, log.instance), ("ml-admin", "198.51.100.8", "vm"))

    def test_success_names_the_migration_address(self):
        self.dest.migration_address = "203.0.113.9"
        self.migrate("live", live=True)
        self.assertEqual(Logs.objects.get().message, "Instance is migrated(live) to 198.51.100.9 via 203.0.113.9")
        self.vm.compute = self.source
        self.vm.save()
        self.dest.migration_address = "198.51.100.9"  # the host name itself
        self.migrate("live", live=True)
        self.assertEqual(Logs.objects.latest("id").message, "Instance is migrated(live) to 198.51.100.9")

    def test_failure_is_logged_and_raised(self):
        with self.assertRaisesRegex(util.OperationError, "A shut-off VM cannot be migrated live"):
            self.migrate(util.OperationError("A shut-off VM cannot be migrated live"), live=True)
        self.assertEqual(
            Logs.objects.get().message,
            "Instance migration to 198.51.100.9 failed: A shut-off VM cannot be migrated live",
        )

    def test_the_page_and_the_api_log_once_through_migrate_instance(self):
        self.client.force_login(self.admin)
        api = APIClient()
        api.force_authenticate(self.admin)
        with patch("instances.views.utils.migrate_instance", return_value="live") as page_migrate:
            self.client.post(reverse("instances:migrate", args=[self.vm.id]),
                             {"compute_id": self.dest.id, "live_migrate": "true"})
        with patch("instances.api.viewsets.migrate_instance", return_value="live") as api_migrate:
            response = api.post("/api/v1/migrate/", {"instance": self.vm.id, "target_compute": self.dest.id,
                                                     "live": True, "offline": False}, format="json")
        self.assertEqual(page_migrate.call_count, 1)
        self.assertEqual(api_migrate.call_count, 1)
        self.assertFalse(Logs.objects.exists())  # migrate_instance logs; it is patched here
        self.assertEqual(response.json(), {"status": "instance is migrated (live)"})
