import json
import socket
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase
from django.urls import reverse

from computes.models import Compute
from instances.models import Instance


class SetRootPassTestCase(TestCase):
    def setUp(self):
        self.client.login(request=RequestFactory().get("/"), username="admin", password="admin")
        self.compute = Compute.objects.create(
            name="test_compute_root_pass",
            hostname="localhost",
            type=1,
        )
        self.instance = Instance.objects.create(
            compute=self.compute,
            name="root-pass-vm",
            uuid="87654321-4321-4321-4321-210987654321",
        )

    @patch("socket.socket")
    @patch.object(Instance, "proxy")
    def test_set_root_pass_post(self, mock_proxy, mock_socket_cls):
        # Renamed on the host: gstfsd must get the name of this UUID's domain
        mock_proxy.instance.name.return_value = "renamed-on-host"
        mock_proxy.get_status.return_value = 5  # shut off
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        replies = iter([json.dumps({"return": "success"}).encode(), b""] * 2)
        # one reply, then gstfsd closes the connection
        mock_sock.recv.side_effect = lambda size: next(replies)

        response = self.client.post(
            reverse("instances:rootpasswd", args=[self.instance.id]),
            {"passwd": "newSecretPassword123"}
        )
        # Redirects back to referer or instance detail
        self.assertEqual(response.status_code, 302)

        # Check socket sent the command with hashed password
        self.assertTrue(mock_sock.sendall.called)
        sent_bytes = mock_sock.sendall.call_args[0][0]
        data = json.loads(sent_bytes.decode())
        self.assertEqual(data["action"], "password")
        self.assertEqual(data["vname"], "renamed-on-host")
        # SHA-512 crypt with a random salt, not the old fixed one
        self.assertTrue(data["passwd"].startswith("$6$"))
        self.assertFalse(data["passwd"].startswith("$6$kgPoiREy$"))

    def test_set_root_pass_get_not_allowed(self):
        response = self.client.get(reverse("instances:rootpasswd", args=[self.instance.id]))
        self.assertEqual(response.status_code, 405)

    def test_set_root_pass_not_found(self):
        response = self.client.post(reverse("instances:rootpasswd", args=[99999]), {"passwd": "secret"})
        self.assertEqual(response.status_code, 404)

    @patch("socket.socket")
    @patch.object(Instance, "proxy")
    def test_same_password_gets_a_different_salt_each_time(self, mock_proxy, mock_socket_cls):
        mock_proxy.instance.name.return_value = "root-pass-vm"
        mock_proxy.get_status.return_value = 5
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        replies = iter([json.dumps({"return": "success"}).encode(), b""] * 2)
        # one reply, then gstfsd closes the connection
        mock_sock.recv.side_effect = lambda size: next(replies)
        url = reverse("instances:rootpasswd", args=[self.instance.id])

        self.client.post(url, {"passwd": "samePassword1"})
        self.client.post(url, {"passwd": "samePassword1"})

        hashes = [json.loads(c[0][0].decode())["passwd"] for c in mock_sock.sendall.call_args_list]
        self.assertEqual(len(hashes), 2)
        self.assertNotEqual(hashes[0], hashes[1])

    @patch("socket.socket")
    @patch.object(Instance, "proxy")
    def test_unreachable_gstfsd_is_an_error_message_not_a_crash(self, mock_proxy, mock_socket_cls):
        mock_proxy.instance.name.return_value = "root-pass-vm"
        mock_proxy.get_status.return_value = 5
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.connect.side_effect = socket.timeout("timed out")

        response = self.client.post(
            reverse("instances:rootpasswd", args=[self.instance.id]), {"passwd": "x"}
        )

        self.assertEqual(response.status_code, 302)
        mock_sock.settimeout.assert_called()

    @patch("socket.socket")
    @patch.object(Instance, "proxy")
    def test_add_public_key_unreachable_gstfsd_is_an_error_message(self, mock_proxy, mock_socket_cls):
        from accounts.models import UserSSHKey
        from django.contrib.auth import get_user_model

        admin = get_user_model().objects.get(username="admin")
        key = UserSSHKey.objects.create(user=admin, keyname="k", keypublic="ssh-ed25519 AAAA k")
        mock_proxy.instance.name.return_value = "root-pass-vm"
        mock_proxy.get_status.return_value = 5
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.connect.side_effect = ConnectionRefusedError()

        response = self.client.post(
            reverse("instances:add_public_key", args=[self.instance.id]), {"sshkeyid": key.id}
        )

        self.assertEqual(response.status_code, 302)
        mock_sock.settimeout.assert_called()
