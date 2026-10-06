import os
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import reverse
from django.test import RequestFactory, TestCase, TransactionTestCase

from vrtManager.connection import CONN_SOCKET, connection_manager
from .models import Compute

TEST_COMPUTE_HOST = os.environ.get("TEST_LIBVIRT_HOST", "localhost")
TEST_COMPUTE_LOGIN = os.environ.get("TEST_LIBVIRT_LOGIN", "")
TEST_COMPUTE_PASSWORD = os.environ.get("TEST_LIBVIRT_PASSWORD", "")
TEST_COMPUTE_TYPE = int(os.environ.get("TEST_LIBVIRT_TYPE", CONN_SOCKET))


class ComputesTestCase(TestCase):
    def setUp(self):
        self.client.login(request=RequestFactory().get("/"), username="admin", password="admin")
        self.compute, _ = Compute.objects.get_or_create(
            name="computes-test-compute",
            defaults={
                "hostname": TEST_COMPUTE_HOST,
                "login": TEST_COMPUTE_LOGIN,
                "password": TEST_COMPUTE_PASSWORD,
                "details": "test",
                "type": TEST_COMPUTE_TYPE,
            },
        )

    def tearDown(self):
        Compute.objects.filter(name="computes-test-compute").delete()
        super().tearDown()

    def _require_live_compute(self):
        try:
            conn = connection_manager.get_connection(
                self.compute.hostname,
                self.compute.login,
                self.compute.password,
                self.compute.type,
            )
            if not (conn and conn.isAlive()):
                self.skipTest("Live libvirt compute host not available")
        except Exception:
            self.skipTest("Live libvirt compute host not available")

    def test_index(self):
        response = self.client.get(reverse("computes"))
        self.assertEqual(response.status_code, 200)

    def test_create_update_delete(self):
        response = self.client.get(reverse("add_socket_host"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("add_socket_host"),
            {
                "name": "l1",
                "details": "Created",
                "hostname": "localhost",
                "type": 4,
            },
        )
        self.assertRedirects(response, reverse("computes"))

        compute = Compute.objects.get(name="l1")
        self.assertEqual(compute.name, "l1")
        self.assertEqual(compute.details, "Created")
        created_id = compute.id

        response = self.client.get(reverse("compute_update", args=[created_id]))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("compute_update", args=[created_id]),
            {
                "name": "l2",
                "details": "Updated",
                "hostname": "localhost",
                "type": 4,
            },
        )
        self.assertRedirects(response, reverse("computes"))

        compute = Compute.objects.get(id=created_id)
        self.assertEqual(compute.name, "l2")
        self.assertEqual(compute.details, "Updated")

        response = self.client.get(reverse("compute_delete", args=[created_id]))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("compute_delete", args=[created_id]))
        self.assertRedirects(response, reverse("computes"))

        with self.assertRaises(ObjectDoesNotExist):
            Compute.objects.get(id=created_id)

    def test_overview(self):
        self._require_live_compute()
        response = self.client.get(reverse("overview", args=[self.compute.id]))
        self.assertEqual(response.status_code, 200)

    def test_graph(self):
        self._require_live_compute()
        response = self.client.get(reverse("compute_graph", args=[self.compute.id]))
        self.assertEqual(response.status_code, 200)

    def test_instances(self):
        self._require_live_compute()
        response = self.client.get(reverse("instances", args=[self.compute.id]))
        self.assertEqual(response.status_code, 200)

    def test_storages(self):
        self._require_live_compute()
        response = self.client.get(reverse("storages", args=[self.compute.id]))
        self.assertEqual(response.status_code, 200)

    def test_default_storage_volumes(self):
        self._require_live_compute()
        response = self.client.get(
            reverse("volumes", kwargs={"compute_id": self.compute.id, "pool": "default"})
        )
        self.assertEqual(response.status_code, 200)

    def test_default_storage(self):
        self._require_live_compute()
        response = self.client.get(
            reverse("storage", kwargs={"compute_id": self.compute.id, "pool": "default"})
        )
        self.assertEqual(response.status_code, 200)

    def test_networks(self):
        self._require_live_compute()
        response = self.client.get(reverse("networks", args=[self.compute.id]))
        self.assertEqual(response.status_code, 200)

    def test_default_network(self):
        self._require_live_compute()
        response = self.client.get(
            reverse("network", kwargs={"compute_id": self.compute.id, "pool": "default"})
        )
        self.assertEqual(response.status_code, 200)

    def test_interfaces(self):
        self._require_live_compute()
        response = self.client.get(reverse("interfaces", args=[self.compute.id]))
        self.assertEqual(response.status_code, 200)

    def test_nwfilters(self):
        self._require_live_compute()
        response = self.client.get(reverse("nwfilters", args=[self.compute.id]))
        self.assertEqual(response.status_code, 200)

    def test_secrets(self):
        self._require_live_compute()
        response = self.client.get(reverse("virtsecrets", args=[self.compute.id]))
        self.assertEqual(response.status_code, 200)

    def test_machines(self):
        self._require_live_compute()
        response = self.client.get(
            reverse("machines", kwargs={"compute_id": self.compute.id, "arch": "x86_64"})
        )
        self.assertEqual(response.status_code, 200)

    def test_compute_disk_buses(self):
        self._require_live_compute()
        response = self.client.get(
            reverse(
                "buses",
                kwargs={
                    "compute_id": self.compute.id,
                    "arch": "x86_64",
                    "machine": "pc",
                    "disk": "disk",
                },
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_dom_capabilities(self):
        self._require_live_compute()
        response = self.client.get(
            reverse(
                "domcaps", kwargs={"compute_id": self.compute.id, "arch": "x86_64", "machine": "pc"}
            )
        )
        self.assertEqual(response.status_code, 200)


class ComputeConcurrencyTestCase(TransactionTestCase):
    def test_concurrent_refresh_instance_database(self):
        import threading
        from django.db import connection
        from unittest.mock import MagicMock
        from computes.utils import refresh_instance_database
        from instances.models import Instance

        compute = Compute.objects.create(
            name="concurrent-test",
            hostname="127.0.0.1",
            login="test",
            password="pwd",
            type=CONN_SOCKET,
        )

        mock_dom1 = MagicMock()
        mock_dom1.UUIDString.return_value = "11111111-2222-3333-4444-555555555555"
        mock_dom1.name.return_value = "vm-concurrent-1"

        mock_dom2 = MagicMock()
        mock_dom2.UUIDString.return_value = "22222222-3333-4444-5555-666666666666"
        mock_dom2.name.return_value = "vm-concurrent-2"

        mock_proxy = MagicMock()
        mock_proxy.wvm.listAllDomains.return_value = [mock_dom1, mock_dom2]
        compute.proxy = mock_proxy
        compute.status = True  # reachable

        exceptions = []

        def worker():
            try:
                refresh_instance_database(compute)
            except Exception as e:
                exceptions.append(e)
            finally:
                connection.close()

        connection.close()
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(exceptions, [])
        self.assertEqual(Instance.objects.filter(compute=compute).count(), 2)

    def test_refresh_fails_closed_on_lock_error(self):
        from unittest.mock import patch, MagicMock
        from computes.utils import refresh_instance_database
        from instances.models import Instance

        compute = Compute.objects.create(
            name="lock-fail-test",
            hostname="127.0.0.1",
            login="test",
            password="pwd",
            type=CONN_SOCKET,
        )
        Instance.objects.create(compute=compute, name="existing-vm", uuid="existing-uuid-1234")

        # Libvirt returns empty list - if it did not fail closed, existing-vm would be deleted!
        mock_proxy = MagicMock()
        mock_proxy.wvm.listAllDomains.return_value = []
        compute.proxy = mock_proxy
        compute.status = True  # reachable

        # 1. When lock directory cannot be acquired
        with patch("computes.utils._get_lock_directory", return_value=None):
            refresh_instance_database(compute)
            self.assertEqual(Instance.objects.filter(compute=compute).count(), 1)

        # 2. When lock file is a symlink
        with patch("os.path.islink", return_value=True):
            refresh_instance_database(compute)
            self.assertEqual(Instance.objects.filter(compute=compute).count(), 1)

        # 3. When lock file cannot be opened
        with patch("os.open", side_effect=OSError("Permission denied")):
            refresh_instance_database(compute)
            self.assertEqual(Instance.objects.filter(compute=compute).count(), 1)

        # 4. When flock times out
        curr_time = [0.0]
        def advance_time():
            curr_time[0] += 20.0
            return curr_time[0]

        with patch("fcntl.flock", side_effect=BlockingIOError("Resource temporarily unavailable")):
            with patch("time.monotonic", side_effect=advance_time):
                refresh_instance_database(compute)
                self.assertEqual(Instance.objects.filter(compute=compute).count(), 1)

    def test_libvirt_compute_lock_thread_timeout(self):
        from computes.utils import libvirt_compute_lock, _get_compute_thread_lock
        compute = Compute.objects.create(
            name="thread-lock-test",
            hostname="127.0.0.1",
            login="test",
            password="pwd",
            type=CONN_SOCKET,
        )
        tlock = _get_compute_thread_lock(compute.id)
        tlock.acquire()
        try:
            with self.assertRaises(TimeoutError):
                with libvirt_compute_lock(compute, timeout=0.05):
                    pass
        finally:
            tlock.release()

    def test_libvirt_compute_lock_reentrant(self):
        from computes.utils import libvirt_compute_lock
        compute = Compute.objects.create(
            name="reentrant-lock-test",
            hostname="127.0.0.1",
            login="test",
            password="pwd",
            type=CONN_SOCKET,
        )
        executed = []
        with libvirt_compute_lock(compute, timeout=1.0):
            executed.append("outer")
            with libvirt_compute_lock(compute, timeout=1.0):
                executed.append("inner")
                with libvirt_compute_lock(compute, timeout=1.0):
                    executed.append("deep")
        self.assertEqual(executed, ["outer", "inner", "deep"])


class ComputeInputValidationTestCase(TestCase):
    """hostname and login reach libvirt URIs and ssh unquoted; the form and
    the API reject values that would inject a URI parameter or an ssh option."""

    def test_form_rejects_injecting_hostname(self):
        from computes.forms import SshComputeForm

        form = SshComputeForm(
            data={"name": "c", "hostname": "192.0.2.1/system?command=/evil", "login": "root", "type": 2}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("hostname", form.errors)

    def test_api_rejects_injecting_hostname_and_login(self):
        from computes.api.serializers import ComputeSerializer

        for field, data in (
            ("hostname", {"name": "c", "hostname": "192.0.2.1/system?command=/evil&a=", "login": "root", "type": 2}),
            ("login", {"name": "c", "hostname": "192.0.2.1", "login": "-oProxyCommand=x", "type": 2}),
        ):
            with self.subTest(field=field):
                serializer = ComputeSerializer(data=data)
                self.assertFalse(serializer.is_valid())
                self.assertIn(field, serializer.errors)

    def test_api_accepts_valid_values(self):
        from computes.api.serializers import ComputeSerializer

        serializer = ComputeSerializer(
            data={"name": "c", "hostname": "host.example.com", "login": "root", "type": 2}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_api_accepts_empty_login_for_tcp(self):
        from computes.api.serializers import ComputeSerializer

        serializer = ComputeSerializer(data={"name": "c", "hostname": "192.0.2.1", "login": "", "type": 1})
        self.assertTrue(serializer.is_valid(), serializer.errors)
