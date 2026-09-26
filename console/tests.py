# pylint: disable=no-member
import importlib.machinery
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

from accounts.models import UserInstance
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from instances.models import Instance
from libvirt import libvirtError

# Dynamically load console/novncd script for testing
NOVNCD_PATH = str(Path(__file__).resolve().parent / "novncd")
_loader = importlib.machinery.SourceFileLoader("novncd_mod", NOVNCD_PATH)
_spec = importlib.util.spec_from_file_location(
    "novncd_mod", NOVNCD_PATH, loader=_loader
)
assert _spec is not None
assert _spec.loader is not None
novncd_mod = importlib.util.module_from_spec(_spec)
_loader.exec_module(novncd_mod)


class ConsoleViewsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_user = (
            User.objects.filter(is_superuser=True).first()
            or User.objects.filter(username="admin").first()
            or User.objects.create_superuser(
                username="admin_console_test",
                email="admin@example.com",
                password="adminpassword",
            )
        )
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="alicepassword"
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="bobpassword"
        )

        cls.compute = Compute.objects.create(
            name="test-compute",
            hostname="127.0.0.1",
            login="root",
            password="",
            type=1,
        )

        cls.instance = Instance.objects.create(
            compute=cls.compute,
            name="vm-alpha",
            uuid="11111111-2222-3333-4444-555555555555",
        )

        # Grant alice ownership of vm-alpha
        UserInstance.objects.create(
            user=cls.alice,
            instance=cls.instance,
        )

        cls.token = f"{cls.compute.id}-{cls.instance.uuid}"

    @patch("console.views.wvmInstance")
    def test_console_superuser_vnc_lite(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_conn.get_console_websocket_port.return_value = None
        mock_conn.get_console_passwd.return_value = "vncsecret"
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}&view=lite"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console-vnc-lite.html")
        self.assertEqual(response.cookies["token"].value, self.token)
        self.assertEqual(response.context["ws_path"], "novncd/")
        self.assertEqual(response.context["ws_port"], 6080)
        self.assertEqual(response.context["console_passwd"], "vncsecret")

    @patch("console.views.wvmInstance")
    def test_console_superuser_vnc_full(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_conn.get_console_websocket_port.return_value = 5900
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}&view=full"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console-vnc-full.html")
        self.assertEqual(response.context["ws_port"], 5900)

    @patch("console.views.wvmInstance")
    def test_console_superuser_spice_lite(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "spice"
        mock_conn.get_console_websocket_port.return_value = None
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}&view=lite"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console-spice-lite.html")

    @patch("console.views.wvmInstance")
    def test_console_superuser_spice_full(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "spice"
        mock_conn.get_console_websocket_port.return_value = None
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}&view=full"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console-spice-full.html")

    @patch("console.views.wvmInstance")
    def test_console_superuser_pty(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "pty"
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console-xterm.html")

    @patch("console.views.wvmInstance")
    def test_console_user_assigned_instance_access(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_conn.get_console_websocket_port.return_value = None
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.alice)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console-vnc-lite.html")

    def test_console_unauthorized_user_forbidden(self):
        self.client.force_login(self.bob)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url)

        # Non-permitted user attempting to view other user's instance
        self.assertEqual(response.status_code, 500)
        self.assertIn("permission", response.content.decode("utf-8").lower())

    @patch("console.views.wvmInstance")
    def test_console_user_with_view_instances_perm(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_conn.get_console_websocket_port.return_value = None
        mock_wvm.return_value = mock_conn

        perm = Permission.objects.get(codename="view_instances")
        self.bob.user_permissions.add(perm)

        self.client.force_login(self.bob)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

    @patch("console.views.wvmInstance")
    @override_settings(WS_PUBLIC_PATH="/novncd/")
    def test_console_ws_path_normalization_leading_slash(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ws_path"], "novncd/")

    @patch("console.views.wvmInstance")
    @override_settings(WS_PUBLIC_PATH="novncd/")
    def test_console_ws_path_normalization_clean(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ws_path"], "novncd/")

    @patch("console.views.wvmInstance")
    @override_settings(WS_PUBLIC_PATH="/")
    def test_console_ws_path_root_slash(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ws_path"], "")

    @patch("console.views.wvmInstance")
    def test_console_ws_host_strip_port(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url, HTTP_HOST="panel.example.com:8080")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ws_host"], "panel.example.com")

    @patch("console.views.wvmInstance")
    def test_console_xss_protection_query_parameters(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_conn.get_console_websocket_port.return_value = None
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        # Attempt XSS injection via query parameters
        url = (
            reverse("console")
            + f"?token={self.token}&view=lite&view_only=alert('xss')&scale=true&clip_viewport=1"
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["view_only"], False)
        self.assertEqual(response.context["scale"], True)
        self.assertEqual(response.context["clip_viewport"], True)

        content = response.content.decode("utf-8")
        self.assertNotIn("alert('xss')", content)
        self.assertIn("rfb.viewOnly = false;", content)
        self.assertIn("rfb.scaleViewport = true;", content)

    @patch("console.views.wvmInstance")
    def test_console_password_escapejs_escaping(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_conn.get_console_websocket_port.return_value = None
        mock_conn.get_console_passwd.return_value = "O'Brian<script>&123"
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)

        # Test Lite mode
        url_lite = reverse("console") + f"?token={self.token}&view=lite"
        resp_lite = self.client.get(url_lite)
        self.assertEqual(resp_lite.status_code, 200)
        content_lite = resp_lite.content.decode("utf-8")
        self.assertIn(r"O\u0027Brian", content_lite)
        self.assertNotIn("&#39;", content_lite)

        # Test Full mode
        url_full = reverse("console") + f"?token={self.token}&view=full"
        resp_full = self.client.get(url_full)
        self.assertEqual(resp_full.status_code, 200)
        content_full = resp_full.content.decode("utf-8")
        # In script tag, password must use JS unicode escape and not HTML entities
        main_script_block = [
            s.split("</script>")[0]
            for s in content_full.split("<script")
            if "defaults" in s
        ][0]
        self.assertIn(r"password: 'O\u0027Brian", main_script_block)
        self.assertNotIn("&#39;", main_script_block)
        self.assertNotIn("&#x27;", main_script_block)

    @patch("console.views.wvmInstance")
    def test_console_full_resize_syntax_validity(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_conn.get_console_websocket_port.return_value = None
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)

        # Default: scale=False, resize_session=False -> 'off'
        url = reverse("console") + f"?token={self.token}&view=full"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("resize: 'off',", content)
        self.assertNotIn("&#x27;off&#x27;", content)
        self.assertNotIn("&#39;off&#39;", content)

        # scale=True, resize_session=False -> 'scale'
        url_scale = reverse("console") + f"?token={self.token}&view=full&scale=true"
        resp_scale = self.client.get(url_scale)
        self.assertEqual(resp_scale.status_code, 200)
        content_scale = resp_scale.content.decode("utf-8")
        self.assertIn("resize: 'scale',", content_scale)

        # resize_session=True -> 'remote'
        url_remote = reverse("console") + f"?token={self.token}&view=full&resize_session=true"
        resp_remote = self.client.get(url_remote)
        self.assertEqual(resp_remote.status_code, 200)
        content_remote = resp_remote.content.decode("utf-8")
        self.assertIn("resize: 'remote',", content_remote)


    @patch("console.views.wvmInstance")
    def test_console_libvirt_error_fallback(self, mock_wvm):
        mock_wvm.side_effect = libvirtError("Connection to libvirtd failed")

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console-vnc-lite.html")
        self.assertIsNotNone(response.context["console_error"])

    def test_console_missing_token_safe_fallback(self):
        self.client.force_login(self.admin_user)
        url = reverse("console")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "console-vnc-lite.html")
        self.assertIsNotNone(response.context["console_error"])

    @patch("console.views.wvmInstance")
    def test_console_send_keys_menu_elements(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_console_type.return_value = "vnc"
        mock_wvm.return_value = mock_conn

        self.client.force_login(self.admin_user)
        url = reverse("console") + f"?token={self.token}&view=lite"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        expected_ids = [
            "ctrlaltdel",
            "ctrlaltbackspace",
            "alttab",
            "superkey",
            "sendesc",
            "sendtab",
            "ctrlc",
            "ctrlz",
            "ctrld",
            "ctrlbackslash",
        ] + [f"ctrlaltf{i}" for i in range(1, 13)]

        for key_id in expected_ids:
            self.assertIn(
                f'id="{key_id}"',
                content,
                f"Expected key element id '{key_id}' in console menu",
            )


class NovncdDaemonLogicTestCase(TestCase):
    def setUp(self):
        self.get_parser = novncd_mod.get_parser
        self.CompatibilityMixIn = novncd_mod.CompatibilityMixIn

    def test_novncd_parser_defaults(self):
        parser = self.get_parser()
        opts = parser.parse_args([])
        self.assertFalse(opts.verbose)
        self.assertFalse(opts.debug)
        self.assertEqual(opts.port, 6080)

    def test_novncd_parser_custom_arguments(self):
        parser = self.get_parser()
        opts = parser.parse_args(
            ["-v", "-d", "-H", "192.168.1.100", "-p", "7070", "-c", "/path/to/cert.pem"]
        )
        self.assertTrue(opts.verbose)
        self.assertTrue(opts.debug)
        self.assertEqual(opts.host, "192.168.1.100")
        self.assertEqual(opts.port, 7070)
        self.assertEqual(opts.cert, "/path/to/cert.pem")

    @patch.object(novncd_mod, "get_connection_infos")
    def test_compatibility_mixin_token_from_cookie(self, mock_get_info):
        mock_get_info.return_value = (
            "host1",
            22,
            "root",
            1,
            "192.168.1.50",
            5900,
            None,
        )

        handler = self.CompatibilityMixIn()
        handler.headers = {"cookie": "other=123; token=1-uuid-from-cookie; test=456"}
        handler.path = "/novncd/"
        handler.msg = MagicMock()
        handler.do_proxy = MagicMock()
        handler.verbose = False

        socket_factory = MagicMock()
        handler._new_client(daemon=False, socket_factory=socket_factory)

        mock_get_info.assert_called_once_with("1-uuid-from-cookie")
        socket_factory.assert_called_once_with("host1", 5900, connect=True)

    @patch.object(novncd_mod, "get_connection_infos")
    def test_compatibility_mixin_token_from_query_params(self, mock_get_info):
        mock_get_info.return_value = (
            "host2",
            22,
            "root",
            1,
            "192.168.1.50",
            5901,
            None,
        )

        handler = self.CompatibilityMixIn()
        handler.headers = {}
        handler.path = "/novncd/?token=1-uuid-from-query"
        handler.msg = MagicMock()
        handler.do_proxy = MagicMock()
        handler.verbose = False

        socket_factory = MagicMock()
        handler._new_client(daemon=False, socket_factory=socket_factory)

        mock_get_info.assert_called_once_with("1-uuid-from-query")
        socket_factory.assert_called_once_with("host2", 5901, connect=True)

    @patch.object(novncd_mod, "get_connection_infos")
    def test_compatibility_mixin_missing_token_safe_exit(self, mock_get_info):
        handler = self.CompatibilityMixIn()
        handler.headers = {}
        handler.path = "/novncd/"
        handler.msg = MagicMock()

        socket_factory = MagicMock()
        # Should cleanly exit and log message without raising UnboundLocalError
        handler._new_client(daemon=False, socket_factory=socket_factory)

        mock_get_info.assert_not_called()
        socket_factory.assert_not_called()
        handler.msg.assert_called_with(
            "No console token provided in cookie or query parameters"
        )
