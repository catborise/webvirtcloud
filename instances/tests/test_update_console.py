from unittest.mock import patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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

    @override_settings(SERIAL_CONSOLE_ENABLED=True)
    def test_a_new_console_type_and_its_vnc_settings(self):
        data = {"listen_on": "0.0.0.0", "keymap": "de"}
        for old, new, vnc_settings in (("vnc", "pty", False), ("pty", "vnc", True)):
            with self.subTest(new), patch("instances.models.wvmInstance") as wvm:
                wvm.return_value.get_console_type.return_value = old
                self.client.post(reverse("instances:update_console", args=[self.instance.id]), dict(data, console_type=new))
                wvm.return_value.set_console_type.assert_called_once_with(new)
                self.assertEqual(wvm.return_value.set_console_listener_addr.called, vnc_settings)

    @override_settings(SERIAL_CONSOLE_ENABLED=False)
    def test_serial_is_not_offered_while_the_serial_console_is_off(self):
        proxy = self.post({"console_type": "pty", "listen_on": "127.0.0.1", "keymap": "de"})
        proxy.set_console_type.assert_not_called()
