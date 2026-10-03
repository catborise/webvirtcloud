"""
Regression tests for ROADMAP Wave 0 item 4 (S-05): cloning another user's VM
must require change permission on it (templates excepted), and the clone
data must not be taken blindly from the POST body.
"""
from contextlib import contextmanager
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserInstance
from appsettings.settings import app_settings
from computes.models import Compute
from instances.models import Instance

SRC_DISK = {
    "dev": "vda",
    "image": "src-vm.qcow2",
    "storage": "default",
    "path": "/var/lib/libvirt/images/src-vm.qcow2",
    "size": 10 << 30,
    "format": "qcow2",
}


class ClonePermissionTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        clone_perm = Permission.objects.get(codename="clone_instances")
        view_perm = Permission.objects.get(codename="view_instances")

        self.superuser = User.objects.create_superuser(
            username="clone_super", email="clone_super@example.com", password="password"
        )
        self.viewer = User.objects.create_user(username="clone_viewer", password="password")
        self.viewer.user_permissions.add(clone_perm, view_perm)
        self.owner_readonly = User.objects.create_user(
            username="clone_owner_ro", password="password"
        )
        self.owner_readonly.user_permissions.add(clone_perm)
        self.owner_change = User.objects.create_user(
            username="clone_owner_rw", password="password"
        )
        self.owner_change.user_permissions.add(clone_perm)

        self.compute = Compute.objects.create(
            name="clone-compute", hostname="127.0.0.1", login="root", password="", type=1
        )
        self.instance = Instance.objects.create(
            compute=self.compute,
            name="src-vm",
            uuid="12121212-3434-5656-7878-909090909090",
        )
        UserInstance.objects.create(
            instance=self.instance, user=self.owner_readonly, is_change=False
        )
        UserInstance.objects.create(
            instance=self.instance, user=self.owner_change, is_change=True
        )

    @contextmanager
    def mocked_libvirt(self):
        with patch("instances.models.wvmInstance") as mock_wvm, patch(
            "instances.views.utils.check_user_quota", return_value=""
        ):
            proxy = mock_wvm.return_value
            proxy.get_disk_devices.return_value = [SRC_DISK]
            proxy.get_net_devices.return_value = [{"mac": "52:54:00:aa:bb:cc"}]
            proxy.get_vcpu.return_value = 1
            proxy.get_memory.return_value = 512
            proxy.clone_instance.return_value = "abababab-cdcd-efef-0101-232323232323"
            yield proxy

    def _clone(self, user, data):
        self.client.force_login(user)
        return self.client.post(
            reverse("instances:clone", args=[self.instance.id]),
            data,
            HTTP_REFERER=f"/instances/{self.instance.id}/",
        )

    def _valid_post(self, **extra):
        data = {
            "name": "clone-vm",
            "clone-net-mac-0": "52:54:00:12:34:56",
            "disk-vda": "clone-vm.qcow2",
            "clone-title": "",
            "clone-description": "",
        }
        data.update(extra)
        return data

    def test_viewer_cannot_clone_someone_elses_vm(self):
        with self.mocked_libvirt() as proxy:
            res = self._clone(self.viewer, self._valid_post())
        self.assertEqual(res.status_code, 403)
        proxy.clone_instance.assert_not_called()

    def test_owner_without_is_change_cannot_clone(self):
        with self.mocked_libvirt() as proxy:
            res = self._clone(self.owner_readonly, self._valid_post())
        self.assertEqual(res.status_code, 403)
        proxy.clone_instance.assert_not_called()

    def test_viewer_can_clone_a_template(self):
        self.instance.is_template = True
        self.instance.save(update_fields=["is_template"])
        with self.mocked_libvirt() as proxy:
            self._clone(self.viewer, self._valid_post())
        proxy.clone_instance.assert_called_once()

    def test_posted_disk_owner_and_unknown_keys_are_ignored(self):
        with self.mocked_libvirt() as proxy:
            self._clone(
                self.owner_change,
                self._valid_post(disk_owner_uid="4242", disk_owner_gid="4242", evil="x"),
            )
        proxy.clone_instance.assert_called_once()
        clone_data = proxy.clone_instance.call_args[0][0]
        self.assertEqual(
            clone_data["disk_owner_uid"],
            int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_UID),
        )
        self.assertEqual(
            clone_data["disk_owner_gid"],
            int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_GID),
        )
        self.assertNotIn("evil", clone_data)

    def test_non_superuser_disk_names_are_derived_by_the_server(self):
        with self.mocked_libvirt() as proxy:
            self._clone(
                self.owner_change,
                self._valid_post(**{"disk-vda": "../../../etc/passwd"}),
            )
        proxy.clone_instance.assert_called_once()
        clone_data = proxy.clone_instance.call_args[0][0]
        self.assertEqual(clone_data["disk-vda"], "clone-vm.qcow2")

    def test_superuser_unsafe_disk_name_is_rejected(self):
        with self.mocked_libvirt() as proxy:
            self._clone(
                self.superuser,
                self._valid_post(**{"disk-vda": "x</name><evil/>"}),
            )
        proxy.clone_instance.assert_not_called()

    def test_superuser_invalid_second_mac_is_rejected(self):
        with self.mocked_libvirt() as proxy:
            self._clone(
                self.superuser,
                self._valid_post(**{"clone-net-mac-1": "not-a-mac"}),
            )
        proxy.clone_instance.assert_not_called()

    def test_clone_holds_the_compute_exclusively(self):
        # Clones of different VMs must not race for the destination name.
        calls = []

        @contextmanager
        def record(compute, timeout=15.0, *, shared=False):
            calls.append(shared)
            yield

        with self.mocked_libvirt() as proxy, patch("instances.views.libvirt_compute_lock", record):
            self._clone(self.superuser, self._valid_post())
        proxy.clone_instance.assert_called_once()
        self.assertEqual(calls, [False])

    def test_superuser_valid_clone_passes_posted_values(self):
        with self.mocked_libvirt() as proxy:
            self._clone(
                self.superuser,
                self._valid_post(**{"disk-vda": "custom-name.qcow2", "meta-vda": "true"}),
            )
        proxy.clone_instance.assert_called_once()
        clone_data = proxy.clone_instance.call_args[0][0]
        self.assertEqual(clone_data["disk-vda"], "custom-name.qcow2")
        self.assertEqual(clone_data["meta-vda"], "true")
        self.assertEqual(clone_data["clone-net-mac-0"], "52:54:00:12:34:56")

    def test_non_superuser_clone_skips_disks_without_a_volume(self):
        empty_disk = {"dev": "vdb", "image": None, "storage": None, "path": None,
                      "size": None, "format": None}
        with self.mocked_libvirt() as proxy:
            proxy.get_disk_devices.return_value = [SRC_DISK, empty_disk]
            self._clone(self.owner_change, self._valid_post())
        proxy.clone_instance.assert_called_once()
        clone_data = proxy.clone_instance.call_args[0][0]
        self.assertEqual(clone_data["disk-vda"], "clone-vm.qcow2")
        self.assertIsNone(clone_data["disk-vdb"])

    def test_derived_disk_names_are_sanitized_instead_of_rejected(self):
        odd_disk = dict(SRC_DISK, image="my disk@2.qcow2")
        with self.mocked_libvirt() as proxy:
            proxy.get_disk_devices.return_value = [odd_disk]
            self._clone(self.owner_change, self._valid_post())
        proxy.clone_instance.assert_called_once()
        self.assertEqual(
            proxy.clone_instance.call_args[0][0]["disk-vda"], "my-disk-2-clone.qcow2"
        )
