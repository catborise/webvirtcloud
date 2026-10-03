from contextlib import contextmanager
from unittest.mock import patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.models import Instance
from libvirt import libvirtError


class DestroyOrderTestCase(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser("destroy_admin", "d@example.com", "pw")
        self.client.force_login(admin)
        self.client.raise_request_exception = False
        compute = Compute.objects.create(name="destroy-compute", hostname="127.0.0.1", login="", password="", type=4)
        self.instance = Instance.objects.create(
            compute=compute, name="destroy-vm", uuid="11111111-2222-3333-4444-555555555555"
        )

    def destroy(self, undefine_error=None):
        with patch("instances.models.wvmInstance") as wvm:
            proxy = wvm.return_value
            proxy.instance.isActive.return_value = False
            proxy.split_disk_paths_by_use.return_value = (["/pool/own.qcow2"], ["/pool/shared.qcow2"])
            proxy.delete.side_effect = undefine_error
            self.client.post(reverse("instances:destroy", args=[self.instance.id]), {"delete_disk": "1"})
        return proxy

    def test_disks_are_deleted_after_the_undefine(self):
        proxy = self.destroy()
        proxy.get_volume_by_path.assert_called_once_with("/pool/own.qcow2")
        self.assertFalse(Instance.objects.filter(pk=self.instance.pk).exists())

    def test_failed_undefine_deletes_no_disk(self):
        proxy = self.destroy(undefine_error=libvirtError("refused"))
        proxy.get_volume_by_path.assert_not_called()
        proxy.delete_all_disks.assert_not_called()
        self.assertTrue(Instance.objects.filter(pk=self.instance.pk).exists())

    def test_destroy_and_delete_vol_hold_the_compute_exclusively(self):
        calls = []

        @contextmanager
        def record(compute, timeout=15.0, *, shared=False):
            calls.append(shared)
            yield

        with patch("instances.views.libvirt_compute_lock", record):
            with patch("instances.models.wvmInstance"):
                self.client.post(reverse("instances:delete_vol", args=[self.instance.id]), {"dev": "vda"})
            self.destroy()
        self.assertEqual(calls, [False, False])

    def test_confirmation_page_takes_no_lock(self):
        calls = []

        @contextmanager
        def record(compute, timeout=15.0, *, shared=False):
            calls.append(shared)
            yield

        with patch("instances.views.libvirt_compute_lock", record), patch("instances.models.wvmInstance"):
            response = self.client.get(reverse("instances:destroy", args=[self.instance.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [])
