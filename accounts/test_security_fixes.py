from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserInstance, UserSSHKey
from accounts.utils import validate_ssh_key
from computes.models import Compute
from instances.models import Instance


class AccountsSecurityFixesTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="sec_acc_admin", email="admin@example.com", password="password"
        )
        self.user = User.objects.create_user(
            username="sec_acc_user", password="password"
        )
        self.compute = Compute.objects.create(
            name="acc-comp", hostname="127.0.0.1", login="root", type=1
        )
        self.instance = Instance.objects.create(
            compute=self.compute, name="acc-vm", uuid="99999999-9999-9999-9999-999999999999"
        )
        self.user_inst = UserInstance.objects.create(
            instance=self.instance, user=self.user
        )

    def test_open_redirect_prevented_in_user_instance_delete(self):
        self.client.force_login(self.admin)
        url = reverse("accounts:user_instance_delete", args=[self.user_inst.id])

        # Attempt external malicious redirect
        res = self.client.post(f"{url}?next=https://malicious-attacker.com")
        self.assertEqual(res.status_code, 302)
        # Should redirect to safe internal URL (user account page), NOT external site
        self.assertNotIn("malicious-attacker.com", res.url)
        self.assertEqual(res.url, reverse("accounts:account", args=[self.user.id]))

    def test_ssh_key_cascade_deletion(self):
        key = UserSSHKey.objects.create(
            user=self.user,
            keyname="my-key",
            keypublic="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGabcdef123456 test@example.com",
        )
        self.assertTrue(UserSSHKey.objects.filter(id=key.id).exists())

        # When user is deleted, key should be deleted via CASCADE (not left orphan)
        self.user.delete()
        self.assertFalse(UserSSHKey.objects.filter(id=key.id).exists())

    def test_validate_ssh_key_supports_keys_with_and_without_comment(self):
        rsa_base = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAgQC6OOdbfv27QVnSC6sKxGaHb6YFc+3gxCkyVR3cTSXE/n5BEGf8aOgBpepULWa1RZfxYHY14PlKULDygdXSdrrR2kNSwoKz/Oo4d+3EE92L7ocl1+djZbptzgWgtw1OseLwbFik+iKlIdqPsH+IUQvX7yV545ZQtAP8Qj1R+uCqkw=="
        # Key with comment
        key_with_comment = f"{rsa_base} test@test"
        self.assertTrue(validate_ssh_key(key_with_comment))

        # Key without comment
        key_without_comment = rsa_base
        self.assertTrue(validate_ssh_key(key_without_comment))

        # Invalid key
        self.assertFalse(validate_ssh_key("not a valid key string"))
