"""The migrate form ticks auto converge for a running VM, and the API uses
the same default: it only slows a VM that the migration cannot keep up with."""

import re
from unittest.mock import patch

import libvirt
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.models import Instance
from rest_framework.test import APIClient

DOMAIN = """<domain type='test'><name>migrate-form-vm</name><memory unit='MiB'>128</memory><vcpu>1</vcpu>
<os><type arch='i686'>hvm</type></os></domain>"""


class MigrateFormTests(TestCase):
    def setUp(self):
        self.conn = libvirt.open("test:///default")
        self.addCleanup(self.conn.close)
        self.domain = self.conn.defineXML(DOMAIN)
        self.addCleanup(self.remove_domain)
        self.compute = Compute.objects.create(name="mf", hostname="localhost", login="", password="", type=4)
        self.other = Compute.objects.create(name="mf-other", hostname="198.51.100.30", login="", password="", type=4)
        self.vm = Instance.objects.create(compute=self.compute, name=self.domain.name(), uuid=self.domain.UUIDString())
        for target, value in (("get_machine_types", ["pc"]), ("get_nwfilters", [])):
            patcher = patch(f"vrtManager.connection.wvmConnect.{target}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.admin = get_user_model().objects.create_superuser("mf-admin", "mf@example.com", "pw")

    def remove_domain(self):
        if self.domain.isActive():
            self.domain.destroy()
        self.domain.undefine()

    def autoconverge_input(self):
        self.client.force_login(self.admin)
        with patch("vrtManager.connection.connection_manager.get_connection", return_value=self.conn):
            page = self.client.get(reverse("instances:instance", args=[self.vm.id])).content.decode()
        return re.search(r'<input[^>]*id="autoconverge"[^>]*>', page).group(0)

    def test_auto_converge_is_ticked_for_a_running_vm(self):
        self.domain.create()
        self.assertIn("checked", self.autoconverge_input())

    def test_a_shut_off_vm_has_no_auto_converge(self):
        field = self.autoconverge_input()
        self.assertIn("disabled", field)
        self.assertNotIn("checked", field)

    def test_the_api_uses_auto_converge_unless_told_otherwise(self):
        api = APIClient()
        api.force_authenticate(self.admin)
        request = {"instance": self.vm.id, "target_compute": self.other.id, "live": True, "offline": False}
        with patch("instances.api.viewsets.migrate_instance", return_value="live") as migrate:
            for encoding in ("json", "multipart"):
                api.post("/api/v1/migrate/", request, format=encoding)
            api.post("/api/v1/migrate/", {**request, "autoconverge": False}, format="json")
            api.post("/api/v1/migrate/", {**request, "unsafe": True}, format="json")
        self.assertEqual([c.kwargs["autoconverge"] for c in migrate.call_args_list], [True, True, False, True])
        self.assertEqual([c.kwargs["unsafe"] for c in migrate.call_args_list], [False, False, False, True])
