"""
A VM operation waits for the VM's compute lock while a migration holds it.
When the migration moves the VM to another compute meanwhile, the waiting
operation holds the old compute's lock, not the new one's: it must not run
on the new host but answer "busy, retry", and the retry locks the right
compute.
"""
from contextlib import contextmanager
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from computes.models import Compute
from instances.models import Instance


class MovedWhileWaitingTestCase(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("moved_admin", "m@example.com", "x")
        self.source = Compute.objects.create(name="src", hostname="192.0.2.1", login="root", password="", type=1)
        self.target = Compute.objects.create(name="dst", hostname="192.0.2.2", login="root", password="", type=1)
        self.vm = Instance.objects.create(
            compute=self.source, name="moved-vm", uuid="eeeeeeee-ffff-0000-1111-222222222222"
        )
        self.client.force_login(self.admin)

    def poweroff(self, lock):
        with patch("instances.views.libvirt_instance_lock", lock), patch(
            "instances.models.wvmInstance"
        ) as wvm, patch.object(Compute, "status", new_callable=PropertyMock, return_value=True):
            response = self.client.post(reverse("instances:poweroff", args=[self.vm.id]))
        return response, wvm.return_value.shutdown.called

    def test_a_vm_moved_while_the_request_waited_is_not_touched(self):
        @contextmanager
        def lock_after_a_migration(instance, timeout=15.0):
            # the migration that held the lock moved the VM before releasing it
            Instance.objects.filter(pk=instance.pk).update(compute=self.target)
            yield

        response, touched = self.poweroff(lock_after_a_migration)
        self.assertFalse(touched)
        self.assertEqual(response.status_code, 302)
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn("Instance operation is busy. Please retry.", messages)

    def test_a_vm_that_stayed_is_changed(self):
        @contextmanager
        def plain_lock(instance, timeout=15.0):
            yield

        response, touched = self.poweroff(plain_lock)
        self.assertTrue(touched)

    def test_destroy_of_a_vm_moved_while_it_waited_is_not_run(self):
        # destroy holds the whole compute; the migration moved the VM before
        # releasing it
        @contextmanager
        def compute_lock_after_a_migration(compute, timeout=15.0, *, shared=False):
            Instance.objects.filter(pk=self.vm.pk).update(compute=self.target)
            yield

        with patch("instances.views.libvirt_compute_lock", compute_lock_after_a_migration), patch(
            "instances.models.wvmInstance"
        ) as wvm, patch.object(Compute, "status", new_callable=PropertyMock, return_value=True):
            response = self.client.post(reverse("instances:destroy", args=[self.vm.id]), {"delete_disk": "on"})
        self.assertFalse(wvm.return_value.delete.called)
        self.assertTrue(Instance.objects.filter(pk=self.vm.pk).exists())
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn("Instance operation is busy. Please retry.", messages)

    def clone(self, user, change):
        @contextmanager
        def compute_lock_meanwhile(compute, timeout=15.0, *, shared=False):
            change()
            yield

        self.client.force_login(user)
        with patch("instances.views.libvirt_compute_lock", compute_lock_meanwhile), patch(
            "instances.models.wvmInstance"
        ) as wvm, patch.object(Compute, "status", new_callable=PropertyMock, return_value=True), patch(
            "instances.views.utils.check_user_quota", return_value=""  # not what this tests
        ):
            wvm.return_value.get_disk_devices.return_value = []
            wvm.return_value.get_vcpu.return_value = 1
            wvm.return_value.get_memory.return_value = 512
            response = self.client.post(reverse("instances:clone", args=[self.vm.id]), {"name": "moved-clone"})
        return response, wvm.return_value.clone_instance.called

    def test_a_clone_of_a_vm_moved_while_it_waited_is_not_made(self):
        from accounts.models import UserInstance
        from django.contrib.auth.models import Permission

        # an owner who may clone it (a superuser would also choose MACs)
        owner = get_user_model().objects.create_user("clone_owner", password="x")
        owner.user_permissions.add(Permission.objects.get(codename="clone_instances"))
        UserInstance.objects.create(instance=self.vm, user=owner, is_change=True)
        response, cloned = self.clone(
            owner, lambda: Instance.objects.filter(pk=self.vm.pk).update(compute=self.target)
        )
        self.assertFalse(cloned)
        self.assertIn("Instance operation is busy. Please retry.", [str(m) for m in response.wsgi_request._messages])

    def test_a_template_unmarked_while_the_request_waited_is_not_cloned_by_a_viewer(self):
        from django.contrib.auth.models import Permission

        viewer = get_user_model().objects.create_user("clone_viewer", password="x")
        for codename in ("view_instances", "clone_instances"):
            viewer.user_permissions.add(Permission.objects.get(codename=codename))
        Instance.objects.filter(pk=self.vm.pk).update(is_template=True)
        response, cloned = self.clone(viewer, lambda: Instance.objects.filter(pk=self.vm.pk).update(is_template=False))
        self.assertFalse(cloned)
        self.assertEqual(response.status_code, 403)

    def test_a_clone_checks_the_quota_against_the_source_as_it_is_under_the_lock(self):
        from accounts.models import UserInstance
        from django.contrib.auth.models import Permission

        owner = get_user_model().objects.create_user("clone_quota", password="x")
        owner.user_permissions.add(Permission.objects.get(codename="clone_instances"))
        UserInstance.objects.create(instance=self.vm, user=owner, is_change=True)
        self.client.force_login(owner)
        with patch("instances.models.wvmInstance") as wvm:
            wvm.return_value.get_disk_devices.return_value = []
            wvm.return_value.get_vcpu.return_value = 1
            wvm.return_value.get_memory.return_value = 512

            @contextmanager
            def compute_lock_after_a_resize(compute, timeout=15.0, *, shared=False):
                wvm.return_value.get_vcpu.return_value = 8  # resized meanwhile
                yield

            with patch("instances.views.libvirt_compute_lock", compute_lock_after_a_resize), patch.object(
                Compute, "status", new_callable=PropertyMock, return_value=True
            ), patch(
                "instances.views.utils.check_user_quota",
                side_effect=lambda user, count, cpu, memory, disk: "cpu" if cpu > 4 else "",
            ):
                response = self.client.post(reverse("instances:clone", args=[self.vm.id]), {"name": "grown-clone"})
        self.assertFalse(wvm.return_value.clone_instance.called)
        self.assertTrue(any("quota" in str(m) for m in response.wsgi_request._messages))
