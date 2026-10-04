from unittest.mock import patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from instances.models import Instance


class ChangeXmlTestCase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("xml_admin", "x@example.com", "pw"))
        compute = Compute.objects.create(name="xml-compute", hostname="127.0.0.1", login="", password="", type=4)
        self.instance = Instance.objects.create(compute=compute, name="xml-vm", uuid="11111111-2222-3333-4444-888888888888")

    def post(self, status):
        with patch("instances.models.wvmInstance") as wvm:
            wvm.return_value.get_status.return_value = status
            response = self.client.post(reverse("instances:change_xml", args=[self.instance.id]), {"inst_xml": "<domain/>"})
        return response, wvm.return_value._defineXML

    def test_shut_off_vm_gets_the_new_definition(self):
        _, define = self.post(status=5)
        define.assert_called_once_with("<domain/>")

    def test_running_or_paused_vm_is_refused(self):
        for status in (1, 3):
            with self.subTest(status=status):
                response, define = self.post(status)
                define.assert_not_called()
                self.assertIn("Power off", " ".join(str(m) for m in get_messages(response.wsgi_request)))
