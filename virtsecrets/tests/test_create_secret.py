"""A secret's usage value goes into its XML as text, whatever it holds: a
volume path, an iSCSI IQN or a Ceph name, and characters XML gives a meaning."""

from unittest.mock import patch

from django.test import SimpleTestCase
from lxml import etree
from vrtManager.virtsecrets import wvmSecrets


class CreateSecretTests(SimpleTestCase):
    def defined_xml(self, usage_type, data):
        with patch("vrtManager.connection.connection_manager.get_connection") as get:
            wvmSecrets("host", "login", "pw", 1).create_secret("no", "yes", usage_type, data)
        return etree.fromstring(get.return_value.secretDefineXML.call_args.args[0])

    def test_the_usage_value_is_text_of_its_element(self):
        for usage_type, element, data in (
            ("volume", "volume", "/var/lib/libvirt/images/vm-1.qcow2"),
            ("iscsi", "target", "iqn.2026-10.test.wvc:lun"),
            ("ceph", "name", "client.libvirt secret"),
            ("volume", "volume", "/a'b<c>&d\""),
        ):
            with self.subTest(usage_type=usage_type, data=data):
                secret = self.defined_xml(usage_type, data)
                self.assertEqual((secret.get("ephemeral"), secret.get("private")), ("no", "yes"))
                self.assertEqual(secret.find("usage").get("type"), usage_type)
                self.assertEqual(secret.findtext(f"usage/{element}"), data)


class SecretValueTests(SimpleTestCase):
    def secrets(self):
        with patch("vrtManager.connection.connection_manager.get_connection") as get:
            conn = wvmSecrets("host", "login", "pw", 1)
        secret = get.return_value.secretLookupByUUIDString.return_value
        return conn, secret

    def test_a_base64_value_is_stored_as_its_bytes(self):
        conn, secret = self.secrets()
        conn.set_secret_value("uuid", "cHcA/w==")
        secret.setValue.assert_called_once_with(b"pw\x00\xff")

    def test_a_value_that_is_not_base64_is_refused(self):
        conn, secret = self.secrets()
        for value in ("b'cHc='", "not base64!"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                conn.set_secret_value("uuid", value)
        secret.setValue.assert_not_called()
