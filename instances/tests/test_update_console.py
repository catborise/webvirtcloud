from unittest.mock import patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.models import Instance


class UpdateConsoleTestCase(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser("console_admin", "c@example.com", "pw")
        self.client.force_login(admin)
        compute = Compute.objects.create(name="console-compute", hostname="127.0.0.1", login="", password="", type=4)
        self.instance = Instance.objects.create(
            compute=compute, name="console-vm", uuid="11111111-2222-3333-4444-555555555555"
        )

    def post(self, data):
        with patch("instances.models.wvmInstance") as wvm:
            self.client.post(reverse("instances:update_console", args=[self.instance.id]), data)
        return wvm.return_value

    def test_console_settings_are_applied(self):
        proxy = self.post({"listen_on": "127.0.0.1", "password": "", "keymap": "de"})
        proxy.set_console_keymap.assert_called_once_with("de")
        proxy.set_console_listener_addr.assert_called_once_with("127.0.0.1")
