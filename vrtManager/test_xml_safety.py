import unittest
from unittest.mock import MagicMock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from vrtManager.instance import wvmInstance
from vrtManager.network import wvmNetworks, wvmNetwork


class TestXmlSafety(unittest.TestCase):
    def test_create_network_escaping(self):
        wn = wvmNetworks.__new__(wvmNetworks)
        wn.wvm = MagicMock()
        mock_net = MagicMock()
        wn.get_network = MagicMock(return_value=mock_net)

        malicious_name = "net<script>alert(1)</script>&name"
        malicious_bridge = "br0' onfocus='alert(2)"

        wn.create_network(
            name=malicious_name,
            forward="bridge",
            ipv4=False,
            gateway=None,
            mask=None,
            dhcp4=None,
            ipv6=False,
            gateway6=None,
            prefix6=None,
            dhcp6=None,
            bridge=malicious_bridge,
            openvswitch=False,
        )

        wn.wvm.networkDefineXML.assert_called_once()
        defined_xml = wn.wvm.networkDefineXML.call_args[0][0]
        self.assertNotIn("<script>", defined_xml)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;&amp;name", defined_xml)
        self.assertIn("name='br0&apos; onfocus=&apos;alert(2)'", defined_xml)

    def test_network_set_qos_validation(self):
        wn = wvmNetwork.__new__(wvmNetwork)
        wn._XMLDesc = MagicMock(return_value="<network><name>testnet</name></network>")
        wn.wvm = MagicMock()

        # Invalid direction
        with self.assertRaises(ValueError):
            wn.set_qos("lateral", 100, 200, 300)

        # Invalid numeric strings
        with self.assertRaises(ValueError):
            wn.set_qos("inbound", "not_a_number", 200, 300)

    def test_snapshot_escaping_and_recovery_on_failure(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst.get_status = MagicMock(return_value=5)  # shutoff
        inst.change_snapshot_xml = MagicMock()
        inst.recover_snapshot_xml = MagicMock()
        inst._XMLDesc = MagicMock(return_value="<domain></domain>")
        inst._snapshotCreateXML = MagicMock(side_effect=RuntimeError("libvirt exploded"))

        bad_name = "snap<inject>&quot;"
        bad_desc = "desc<tag>&"

        with self.assertRaises(RuntimeError):
            inst.create_snapshot(bad_name, bad_desc)

        inst.change_snapshot_xml.assert_called_once()
        # Verify recover_snapshot_xml was called in finally despite error
        inst.recover_snapshot_xml.assert_called_once()

    def test_external_snapshot_recovery(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst.get_status = MagicMock(return_value=1)
        inst.change_snapshot_xml = MagicMock()
        inst.recover_snapshot_xml = MagicMock()
        inst._XMLDesc = MagicMock(return_value="<domain></domain>")
        inst._snapshotCreateXML = MagicMock(return_value=None)
        inst.refresh_instance_pools = MagicMock()

        inst.create_external_snapshot("ext_snap_1", desc="normal snapshot")

        inst.change_snapshot_xml.assert_called_once()
        inst.recover_snapshot_xml.assert_called_once()
        inst.refresh_instance_pools.assert_called_once()


if __name__ == "__main__":
    unittest.main()
