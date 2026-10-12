from unittest.mock import patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from libvirt import libvirtError

FORM = {
    "name": "rb-vm", "firmware": "BIOS", "vcpu": 1, "vcpu_mode": "host-model", "memory": 128,
    "hdd_size": 1, "storage": "default", "mac": "52:54:00:aa:04:01", "networks": "default",
    "nwfilter": "", "net_model": "virtio", "cache_mode": "none", "video": "vga",
    "listener_addr": "0.0.0.0", "console_pass": "", "add_cdrom": "None", "add_input": "None",
    "create": True,
}


class CreateRollbackTestCase(TestCase):
    """Volumes are removed only while the VM is not defined yet."""

    def setUp(self):
        admin = get_user_model().objects.create_superuser("rb_admin", "r@example.com", "pw")
        self.client.force_login(admin)
        self.client.raise_request_exception = False
        self.compute = Compute.objects.create(name="rb", hostname="127.0.0.1", login="", password="", type=4)

    def post(self, **conn_setup):
        with patch("instances.views.wvmCreate") as wvm:
            conn = wvm.return_value
            conn.get_cache_modes.return_value = {"none": "Disable cache"}
            conn.get_instances.return_value = []
            conn.get_storages.return_value = ["default"]
            conn.get_networks.return_value = ["default"]
            conn.create_volume.return_value = "/pool/rb-vm.qcow2"
            for attr, value in conn_setup.items():
                getattr(conn, attr).side_effect = value
            self.client.post(reverse("instances:create_instance", args=[self.compute.id, "x86_64", "q35"]), FORM)
        return conn

    def test_failed_definition_removes_the_new_volume(self):
        conn = self.post(create_instance=libvirtError("rejected"))
        conn.delete_volume.assert_called_once_with("/pool/rb-vm.qcow2")

    def test_failure_after_the_definition_keeps_the_volume(self):
        with patch("instances.views.Instance.objects.get_or_create", side_effect=RuntimeError("db down")):
            conn = self.post()
        conn.create_instance.assert_called_once()
        conn.delete_volume.assert_not_called()
