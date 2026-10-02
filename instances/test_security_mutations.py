from unittest.mock import MagicMock, patch
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import Http404
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserInstance
from computes.models import Compute
from instances.models import Instance
from instances.views import get_instance


class InstanceSecurityMutationsTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="sec_super", email="super@example.com", password="password"
        )
        self.owner = User.objects.create_user(
            username="sec_owner", password="password"
        )
        self.change_user = User.objects.create_user(
            username="sec_change_user", password="password"
        )
        self.viewer = User.objects.create_user(
            username="sec_viewer", password="password"
        )
        # Viewer only has global view_instances permission
        view_perm = Permission.objects.get(codename="view_instances")
        self.viewer.user_permissions.add(view_perm)

        self.compute = Compute.objects.create(
            name="sec-compute",
            hostname="127.0.0.1",
            login="root",
            password="",
            type=1,
        )
        self.instance = Instance.objects.create(
            compute=self.compute,
            name="sec-test-vm",
            uuid="11111111-2222-3333-4444-555555555555",
        )
        # Owner has basic userinstance (power enabled, change/delete disabled)
        self.owner_ui = UserInstance.objects.create(
            instance=self.instance, user=self.owner, is_change=False, is_delete=False
        )
        # Change user has is_change=True
        self.change_ui = UserInstance.objects.create(
            instance=self.instance, user=self.change_user, is_change=True, is_delete=False
        )

        # Mock instance.proxy on instance dict
        self.mock_proxy = MagicMock()
        self.instance.__dict__["proxy"] = self.mock_proxy

    def test_power_endpoints_reject_get_requests(self):
        self.client.force_login(self.owner)
        endpoints = [
            reverse("instances:poweron", args=[self.instance.id]),
            reverse("instances:poweroff", args=[self.instance.id]),
            reverse("instances:powercycle", args=[self.instance.id]),
            reverse("instances:force_off", args=[self.instance.id]),
            reverse("instances:suspend", args=[self.instance.id]),
            reverse("instances:resume", args=[self.instance.id]),
        ]
        for url in endpoints:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 405, f"GET {url} should return 405")

    def test_viewer_without_ownership_cannot_power_mutate(self):
        self.client.force_login(self.viewer)
        endpoints = [
            reverse("instances:poweron", args=[self.instance.id]),
            reverse("instances:poweroff", args=[self.instance.id]),
            reverse("instances:powercycle", args=[self.instance.id]),
            reverse("instances:force_off", args=[self.instance.id]),
        ]
        for url in endpoints:
            res = self.client.post(url)
            self.assertEqual(res.status_code, 403, f"POST {url} should be 403 for read-only viewer")

    def test_owner_can_power_mutate(self):
        self.client.force_login(self.owner)
        with patch.object(Instance, "proxy", new_callable=lambda: self.mock_proxy):
            res = self.client.post(reverse("instances:poweron", args=[self.instance.id]))
            self.assertEqual(res.status_code, 302)

    def test_set_root_pass_requires_is_change(self):
        # Owner without is_change -> 403
        self.client.force_login(self.owner)
        res = self.client.post(reverse("instances:rootpasswd", args=[self.instance.id]), {"passwd": "newpass"})
        self.assertEqual(res.status_code, 403)

        # Viewer without ownership -> 403
        self.client.force_login(self.viewer)
        res_v = self.client.post(reverse("instances:rootpasswd", args=[self.instance.id]), {"passwd": "newpass"})
        self.assertEqual(res_v.status_code, 403)

    def test_drf_compute_pk_mismatch_returns_404(self):
        other_compute = Compute.objects.create(
            name="other-compute", hostname="127.0.0.2", login="root", type=1
        )
        self.client.force_login(self.owner)
        # Query instance via wrong compute_pk in nested router
        url = f"/api/v1/computes/{other_compute.id}/instances/{self.instance.id}/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 404)

    def test_disk_media_and_snapshots_reject_get_requests(self):
        self.client.force_login(self.change_user)
        endpoints = [
            reverse("instances:add_new_vol", args=[self.instance.id]),
            reverse("instances:add_existing_vol", args=[self.instance.id]),
            reverse("instances:edit_volume", args=[self.instance.id]),
            reverse("instances:delete_vol", args=[self.instance.id]),
            reverse("instances:detach_vol", args=[self.instance.id]),
            reverse("instances:add_cdrom", args=[self.instance.id]),
            reverse("instances:detach_cdrom", args=[self.instance.id, "hda"]),
            reverse("instances:mount_iso", args=[self.instance.id]),
            reverse("instances:unmount_iso", args=[self.instance.id]),
            reverse("instances:snapshot", args=[self.instance.id]),
            reverse("instances:delete_snapshot", args=[self.instance.id]),
            reverse("instances:revert_snapshot", args=[self.instance.id]),
        ]
        for url in endpoints:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 405, f"GET {url} should return 405")

    def test_disk_and_media_mutations_require_is_change(self):
        # Viewer (global read-only) -> 403
        self.client.force_login(self.viewer)
        urls_to_test = [
            reverse("instances:add_new_vol", args=[self.instance.id]),
            reverse("instances:detach_cdrom", args=[self.instance.id, "hda"]),
            reverse("instances:mount_iso", args=[self.instance.id]),
            reverse("instances:delete_vol", args=[self.instance.id]),
        ]
        for url in urls_to_test:
            res = self.client.post(url, {})
            self.assertEqual(res.status_code, 403, f"Viewer POST {url} should be 403")

        # Owner without is_change (is_change=False, is_delete=False) -> 403
        self.client.force_login(self.owner)
        for url in urls_to_test:
            res = self.client.post(url, {})
            self.assertEqual(res.status_code, 403, f"Owner without is_change POST {url} should be 403")

    def test_resize_views_reject_get_requests(self):
        self.client.force_login(self.change_user)
        endpoints = [
            reverse("instances:resize_disk", args=[self.instance.id]),
            reverse("instances:resizevm_cpu", args=[self.instance.id]),
            reverse("instances:resize_memory", args=[self.instance.id]),
        ]
        for url in endpoints:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 405, f"GET {url} should return 405")

    def test_resize_views_require_is_change(self):
        self.client.force_login(self.viewer)
        endpoints = [
            reverse("instances:resize_disk", args=[self.instance.id]),
            reverse("instances:resizevm_cpu", args=[self.instance.id]),
            reverse("instances:resize_memory", args=[self.instance.id]),
        ]
        for url in endpoints:
            res = self.client.post(url, {})
            self.assertEqual(res.status_code, 403, f"Viewer POST {url} should return 403")

        self.client.force_login(self.owner)
        for url in endpoints:
            res = self.client.post(url, {})
            self.assertEqual(res.status_code, 403, f"Owner without is_change POST {url} should return 403")

    def test_get_instance_fail_closed_for_all_users_on_invalid_perm(self):
        with self.assertRaises(PermissionDenied):
            get_instance(self.superuser, self.instance.pk, perm_type="unknown_perm")

        with self.assertRaises(PermissionDenied):
            get_instance(self.owner, self.instance.pk, perm_type="unknown_perm")

    def test_get_instance_staff_user_access(self):
        User = get_user_model()
        staff_user = User.objects.create_user(username="sec_staff", password="password", is_staff=True)
        # Staff user without ownership or view_instances permission cannot view instance
        with self.assertRaises(Http404):
            get_instance(staff_user, self.instance.pk, perm_type="view")

        # Staff user with explicit view_instances permission can view instance
        view_perm = Permission.objects.get(codename="view_instances")
        staff_user.user_permissions.add(view_perm)
        staff_user = User.objects.get(pk=staff_user.pk)
        inst = get_instance(staff_user, self.instance.pk, perm_type="view")
        self.assertEqual(inst.pk, self.instance.pk)

        # Staff user with view permission but without ownership cannot perform power, change, or delete operations
        for perm in ["power", "change", "delete"]:
            with self.assertRaises(PermissionDenied):
                get_instance(staff_user, self.instance.pk, perm_type=perm)

    def test_migrate_endpoint_requires_post(self):
        self.client.force_login(self.superuser)
        res = self.client.get(reverse("instances:migrate", args=[self.instance.id]))
        self.assertEqual(res.status_code, 405)

    def test_post_only_mutation_endpoints(self):
        self.client.force_login(self.change_user)
        # All mutation endpoints reject GET with 405 Method Not Allowed
        endpoints_405 = ["change_options", "update_console", "rootpasswd", "add_public_key"]
        for ep in endpoints_405:
            res = self.client.get(reverse(f"instances:{ep}", args=[self.instance.id]))
            self.assertEqual(res.status_code, 405, f"{ep} allowed GET request instead of 405")

    def test_opposite_direction_migrations_no_deadlock(self):
        import threading
        import time
        from instances.utils import migrate_instance

        c1 = MagicMock()
        c1.id = 101
        c1.pk = 101
        c1.hostname = "127.0.0.1"
        c1.type = 1
        c1.login = "root"
        c1.password = ""

        c2 = MagicMock()
        c2.id = 102
        c2.pk = 102
        c2.hostname = "127.0.0.2"
        c2.type = 1
        c2.login = "root"
        c2.password = ""

        inst1 = MagicMock()
        inst1.compute = c1
        inst1.name = "inst1"
        inst1.autostart = False
        inst1.proxy = MagicMock()
        inst1.uuid = "u1"

        inst2 = MagicMock()
        inst2.compute = c2
        inst2.name = "inst2"
        inst2.autostart = False
        inst2.proxy = MagicMock()
        inst2.uuid = "u2"

        barrier = threading.Barrier(2)
        errors = []

        def worker_1_to_2():
            try:
                barrier.wait(timeout=5)
                migrate_instance(c2, inst1, self.superuser)
            except Exception as e:
                errors.append(("worker_1_to_2", e))

        def worker_2_to_1():
            try:
                barrier.wait(timeout=5)
                migrate_instance(c1, inst2, self.superuser)
            except Exception as e:
                errors.append(("worker_2_to_1", e))

        def slow_moveto(*args, **kwargs):
            time.sleep(0.05)

        with patch("instances.utils.connection_manager.host_is_up", return_value=True), \
             patch("instances.utils.wvmInstances") as mock_instances_cls, \
             patch("instances.utils.wvmInstance") as mock_instance_cls, \
             patch("instances.utils.transaction.atomic"), \
             patch("instances.utils.Instance") as mock_instance_model:

            mock_instances_cls.return_value.moveto.side_effect = slow_moveto
            mock_instance_model.objects.filter.return_value.first.return_value = None

            t1 = threading.Thread(target=worker_1_to_2)
            t2 = threading.Thread(target=worker_2_to_1)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            self.assertFalse(t1.is_alive(), "Worker 1 to 2 timed out / deadlocked")
            self.assertFalse(t2.is_alive(), "Worker 2 to 1 timed out / deadlocked")
            self.assertEqual(errors, [], f"Unexpected errors during concurrent migrations: {errors}")

    def test_resize_disk_empty_selection_warns_cleanly(self):
        self.client.force_login(self.change_user)
        with patch("instances.models.wvmInstance") as mock_wvm_cls:
            mock_wvm_cls.return_value.get_disk_devices.return_value = []
            res = self.client.post(reverse("instances:resize_disk", args=[self.instance.id]), {})
            self.assertEqual(res.status_code, 302)
            self.assertIn(f"/instances/{self.instance.id}/#resize", res.url)

    def test_disk_mutation_denied_with_is_change(self):
        # change_user has is_change=True, is_delete=False. Disk views are
        # superuser-only (S-02/S-03/S-04), so is_change alone is not enough.
        self.client.force_login(self.change_user)
        with patch("instances.models.wvmInstance"), patch("instances.views.wvmStorage") as mock_storage:
            res = self.client.post(
                reverse("instances:delete_vol", args=[self.instance.id]),
                {"storage": "default", "dev": "vda", "path": "/path"},
                HTTP_REFERER=f"/instances/{self.instance.id}/",
            )
            self.assertEqual(res.status_code, 403)
            mock_storage.return_value.del_volume.assert_not_called()

    def test_destroy_vm_authorization(self):
        # 1. User with is_change=True but is_delete=False gets 403 when trying to destroy VM
        self.client.force_login(self.change_user)
        res = self.client.post(reverse("instances:destroy", args=[self.instance.id]), {})
        self.assertEqual(res.status_code, 403)

        # 2. Staff user without ownership or is_delete cannot destroy VM
        User = get_user_model()
        staff_user = User.objects.create_user(username="staff_destroyer", password="password", is_staff=True)
        self.client.force_login(staff_user)
        res_staff = self.client.post(reverse("instances:destroy", args=[self.instance.id]), {})
        self.assertEqual(res_staff.status_code, 404)

        # 2b. Staff user with view_instances permission but is_delete=False gets 403
        view_perm = Permission.objects.get(codename="view_instances")
        staff_user.user_permissions.add(view_perm)
        res_staff_view = self.client.post(reverse("instances:destroy", args=[self.instance.id]), {})
        self.assertEqual(res_staff_view.status_code, 403)

        # 3. Superuser can destroy VM
        self.client.force_login(self.superuser)
        with patch("instances.models.wvmInstance") as mock_wvm_cls:
            mock_wvm_cls.return_value.get_status.return_value = 5  # shutoff
            res = self.client.post(reverse("instances:destroy", args=[self.instance.id]), {})
            self.assertEqual(res.status_code, 302)
            self.assertFalse(Instance.objects.filter(id=self.instance.id).exists())

    def test_migration_deduplicate_instances_logic(self):
        import importlib
        migration_mod = importlib.import_module("instances.migrations.0012_instance_unique_compute_uuid")
        dedup_fn = getattr(migration_mod, "deduplicate_instances")

        mock_apps = MagicMock()
        mock_instance_model = MagicMock()
        mock_userinstance_model = MagicMock()

        def get_model(app_label, model_name):
            if model_name == "Instance":
                return mock_instance_model
            elif model_name == "UserInstance":
                return mock_userinstance_model
            return MagicMock()

        mock_apps.get_model = get_model

        empty_inst = MagicMock(uuid="")
        primary_inst = MagicMock(id=1)
        dup_inst = MagicMock(id=2)

        def mock_inst_filter(*args, **kwargs):
            if args:
                # Called for empty_uuid_instances query
                return [empty_inst]
            # Called with compute_id and uuid
            ret = MagicMock()
            ret.order_by.return_value = [primary_inst, dup_inst]
            return ret

        mock_instance_model.objects.filter.side_effect = mock_inst_filter
        mock_instance_model.objects.exclude.return_value.exclude.return_value.values.return_value.annotate.return_value.filter.return_value = [
            {"compute_id": 1, "uuid": "test-uuid-1234"}
        ]

        primary_ui = MagicMock(is_change=False, is_delete=False, is_vnc=False)
        dup_ui = MagicMock(user="user1", is_change=True, is_delete=False, is_vnc=True)

        mock_userinstance_model.objects.filter.side_effect = lambda **kwargs: (
            [dup_ui] if kwargs.get("instance") == dup_inst else
            MagicMock(first=lambda: primary_ui)
        )

        dedup_fn(mock_apps, None)

        self.assertTrue(len(empty_inst.uuid) > 0)
        empty_inst.save.assert_called_once_with(update_fields=['uuid'])
        dup_inst.delete.assert_called_once()
        dup_ui.delete.assert_called_once()
        self.assertTrue(primary_ui.is_change)
        self.assertTrue(primary_ui.is_vnc)
        self.assertFalse(primary_ui.is_delete)
        primary_ui.save.assert_called_once()

    def test_unique_compute_uuid_db_constraint_enforced(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Instance.objects.create(
                compute=self.compute,
                name="duplicate_vm",
                uuid=self.instance.uuid,
            )
