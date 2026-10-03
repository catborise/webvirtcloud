import socket

from accounts.models import UserAttributes, UserInstance
from appsettings.settings import get_settings
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from instances.models import Instance
from instances.utils import check_user_quota


class QuotaWithDownHostTestCase(TestCase):
    def test_vm_on_an_unreachable_host_does_not_break_the_quota_check(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        user = get_user_model().objects.create_user("quota_down", password="pw")
        UserAttributes.objects.create(user=user, max_instances=5, max_cpus=8, max_memory=8192, max_disk_size=100)
        compute = Compute.objects.create(name="down", hostname=f"127.0.0.1:{port}", login="", password="", type=1)
        vm = Instance.objects.create(compute=compute, name="vm", uuid="55555555-5555-5555-5555-555555555555")
        UserInstance.objects.create(user=user, instance=vm)
        get_settings()  # app settings are normally loaded by middleware

        self.assertEqual(check_user_quota(user, 1, 1, 128, 1), "")
