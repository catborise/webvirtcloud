"""Regression tests for the instance creation request."""

from unittest.mock import patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.models import Instance


class CreateInstanceViewTests(TestCase):
    @patch("instances.views.wvmCreate")
    @patch("instances.views.NewVMForm")
    def test_successful_create_redirects_after_saving_instance(self, form_class, connection_class):
        user = get_user_model().objects.create_superuser(
            username="create-test-admin", password="password", email="create@example.com"
        )
        self.client.force_login(user)
        compute = Compute.objects.create(
            name="create-test-compute", hostname="localhost", type=4,
            login="", password="",
        )
        conn = connection_class.return_value
        conn.get_instances.return_value = []
        conn.get_video_models.return_value = ["vga"]
        conn.get_cache_modes.return_value = {"default": "Default"}
        conn.get_disk_device_types.return_value = ["disk"]
        conn.get_disk_bus_types.return_value = ["virtio"]
        conn.get_networks.return_value = ["default"]
        conn.get_nwfilters.return_value = []
        conn.get_network_models.return_value = ["virtio"]
        conn.get_storages.return_value = ["default"]
        conn.get_dom_capabilities.return_value = {"loader_enums": [], "loaders": []}
        conn.get_capabilities.return_value = {}
        conn.label_for_firmware_path.return_value = None
        conn.find_uefi_path_for_arch.return_value = None
        conn.get_volume_path.return_value = "/var/lib/libvirt/images/base.qcow2"
        conn.get_volume_format_type.return_value = "qcow2"

        form = form_class.return_value
        form.is_valid.return_value = True
        form.cleaned_data = {
            "name": "new-vm", "meta_prealloc": False, "hdd_size": 0,
            "template": "", "images": "base.qcow2", "cache_mode": "default",
            "firmware": "BIOS", "net_model": "virtio", "memory": 1024,
            "vcpu": 1, "vcpu_mode": "host-model", "networks": "default",
            "virtio": True, "listener_addr": "0.0.0.0", "nwfilter": "",
            "graphics": "vnc", "video": "vga", "console_pass": "",
            "mac": "52:54:00:00:00:01", "qemu_ga": False,
            "add_cdrom": "sata", "add_input": "default",
        }

        response = self.client.post(
            reverse("instances:create_instance", args=[compute.id, "x86_64", "q35"]),
            {"create": "1"},
        )

        instance = Instance.objects.get(compute=compute, name="new-vm")
        self.assertRedirects(
            response, reverse("instances:instance", args=[instance.id]),
            fetch_redirect_response=False,
        )
        conn.create_instance.assert_called_once()
