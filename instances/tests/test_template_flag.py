"""
Only superusers and staff may mark or unmark a VM as a template.
The UI only disables the checkbox for other users, and a disabled checkbox is
not submitted, so the server must not derive is_template from its absence for
them.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserInstance
from computes.models import Compute
from instances.models import Instance


class TemplateFlagTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="tpl_super", email="tpl_super@example.com", password="x"
        )
        self.owner = User.objects.create_user(username="tpl_owner", password="x")
        self.staff_owner = User.objects.create_user(
            username="tpl_staff_owner", password="x", is_staff=True
        )
        self.compute = Compute.objects.create(
            name="tpl-compute", hostname="127.0.0.1:1", login="root", password="", type=1
        )
        self.instance = Instance.objects.create(
            compute=self.compute,
            name="tpl-vm",
            uuid="dddddddd-eeee-ffff-0000-111111111111",
        )
        for user in (self.owner, self.staff_owner):
            UserInstance.objects.create(instance=self.instance, user=user, is_change=True)

    def _change_options(self, user, data, status=302):
        self.client.force_login(user)
        with patch("instances.models.wvmInstance") as wvm:
            # unmarking a template checks the owner's quota with the VM's size
            wvm.return_value.get_vcpu.return_value = 1
            wvm.return_value.get_memory.return_value = 1024
            wvm.return_value.get_disk_devices.return_value = []
            res = self.client.post(
                reverse("instances:change_options", args=[self.instance.id]), data
            )
        self.assertEqual(res.status_code, status)
        self.instance.refresh_from_db()

    def _make_template(self):
        self.instance.is_template = True
        self.instance.save(update_fields=["is_template"])

    def test_owner_cannot_mark_vm_as_template(self):
        self._change_options(self.owner, {"title": "t", "is_template": "True"})
        self.assertFalse(self.instance.is_template)

    def test_owner_cannot_change_a_template(self):
        # a template is changed only by superusers and staff owners
        self._make_template()
        self._change_options(self.owner, {"title": "new title", "description": ""}, status=403)
        self.assertTrue(self.instance.is_template)

    def test_staff_owner_can_mark_and_unmark_template(self):
        self._change_options(self.staff_owner, {"title": "t", "is_template": "True"})
        self.assertTrue(self.instance.is_template)
        # An unchecked checkbox is not submitted: for staff that means "not a template".
        self._change_options(self.staff_owner, {"title": "t"})
        self.assertFalse(self.instance.is_template)

    def test_superuser_can_unmark_template(self):
        self._make_template()
        self._change_options(self.superuser, {"title": "t"})
        self.assertFalse(self.instance.is_template)
