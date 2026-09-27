from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserInstance
from computes.models import Compute
from instances.models import Instance


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
