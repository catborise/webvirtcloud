"""Regression tests for the instance creation request."""

from unittest.mock import patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.api.serializers import CreateInstanceSerializer
from instances.models import Instance
from instances.utils import nic_macs


class CreateInstanceViewTests(TestCase):
    @patch("instances.views.wvmCreate")
    @patch("instances.views.NewVMForm")
    def test_successful_create_redirects_after_saving_instance(self, form_class, connection_class):
        response, conn = self.create(form_class, connection_class)

        instance = Instance.objects.get(name="new-vm")
        self.assertRedirects(
            response, reverse("instances:instance", args=[instance.id]),
            fetch_redirect_response=False,
        )
        conn.create_instance.assert_called_once()

    @patch("instances.views.wvmCreate")
    @patch("instances.views.NewVMForm")
    def test_secure_boot_loader_on_q35_requests_secure_boot(self, form_class, connection_class):
        self.create(form_class, connection_class, firmware="UEFI x86_64: /usr/share/edk2/ovmf/OVMF_CODE.secboot.fd")
        firmware = connection_class.return_value.create_instance.call_args.kwargs["firmware"]
        self.assertEqual(firmware["secure"], "yes")

    @patch("instances.views.wvmCreate")
    @patch("instances.views.NewVMForm")
    def test_invalid_devices_are_refused_before_anything_is_created(self, form_class, connection_class):
        for data in (
            {"add_cdrom": "virtio"},  # no ejectable media
            {"add_cdrom": "fdc"},
            {"add_cdrom": "usb"},  # libvirt refuses an empty usb disk
            {"mac": "52:54:00:00:00:01,52:54:00:00:00:02"},  # one network
            {"mac": "52:54:00:00:00:01'/><evil/>"},
        ):
            with self.subTest(data):
                Instance.objects.all().delete()
                response, conn = self.create(form_class, connection_class, **data)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Instance.objects.exists())
                conn.create_volume.assert_not_called()
                conn.create_instance.assert_not_called()
                connection_class.reset_mock()

    def create(self, form_class, connection_class, **data):
        user = get_user_model().objects.filter(username="create-test-admin").first() or (
            get_user_model().objects.create_superuser(
                username="create-test-admin", password="password", email="create@example.com"
            )
        )
        self.client.force_login(user)
        compute, _ = Compute.objects.get_or_create(
            name="create-test-compute", hostname="localhost", type=4,
            login="", password="",
        )
        conn = connection_class.return_value
        conn.get_instances.return_value = []
        conn.get_video_models.return_value = ["vga"]
        conn.get_cache_modes.return_value = {"default": "Default"}
        conn.get_disk_device_types.return_value = ["disk"]
        conn.get_disk_bus_types.return_value = ["fdc", "virtio", "sata"]
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
            **data,
        }

        response = self.client.post(
            reverse("instances:create_instance", args=[compute.id, "x86_64", "q35"]),
            {"create": "1"},
        )
        return response, conn

    @patch("instances.views.wvmCreate")
    def test_firmware_list_uses_the_chosen_machine_type(self, connection_class):
        user = get_user_model().objects.create_superuser(
            username="create-fw-admin", password="password", email="fw@example.com"
        )
        self.client.force_login(user)
        compute = Compute.objects.create(
            name="create-fw-compute", hostname="localhost", type=4, login="", password="",
        )
        conn = connection_class.return_value
        conn.get_instances.return_value = []
        conn.get_storages.return_value = []
        conn.get_networks.return_value = []
        conn.get_nwfilters.return_value = []
        conn.get_cache_modes.return_value = {}
        # A host without loader support has no "loaders"/"loader_enums" keys
        conn.get_dom_capabilities.return_value = {"loader_support": "no"}
        conn.get_capabilities.return_value = {}
        conn.label_for_firmware_path.return_value = None
        conn.find_uefi_path_for_arch.return_value = None

        response = self.client.get(reverse("instances:create_instance", args=[compute.id, "x86_64", "q35"]))

        self.assertEqual(response.status_code, 200)
        conn.find_uefi_path_for_arch.assert_called_once_with("x86_64", "q35")


class NicMacsTestCase(TestCase):
    def test_macs_follow_the_networks(self):
        self.assertEqual(nic_macs("52:54:00:00:00:01", "a,b"), ["52:54:00:00:00:01"])
        self.assertEqual(nic_macs("", "a,b"), [])

    def test_invalid_or_extra_macs_are_refused(self):
        for mac, networks in (("52:54:00:00:00:01,52:54:00:00:00:02", "a"), ("not-a-mac", "a")):
            with self.subTest(mac=mac), self.assertRaises(ValueError):
                nic_macs(mac, networks)

    def test_api_serializer_refuses_extra_macs(self):
        serializer = CreateInstanceSerializer(data={
            "name": "api-vm", "vcpu": 1, "vcpu_mode": "host-model", "memory": 512,
            "networks": "default", "mac": "52:54:00:00:00:01,52:54:00:00:00:02", "nwfilter": "",
            "storage": "default", "hdd_size": 1, "cache_mode": "none", "meta_prealloc": False,
            "virtio": True, "qemu_ga": True, "console_pass": "", "video": "vga", "listener_addr": "0.0.0.0",
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("mac", serializer.errors)
