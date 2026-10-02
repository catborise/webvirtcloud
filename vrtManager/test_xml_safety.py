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
        inst.recover_snapshot_xml.assert_called_once()

    def test_snapshot_xml_payload_is_escaped(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst.get_status = MagicMock(return_value=5)  # shutoff
        inst.change_snapshot_xml = MagicMock()
        inst.recover_snapshot_xml = MagicMock()
        inst._XMLDesc = MagicMock(return_value="<domain></domain>")
        inst._snapshotCreateXML = MagicMock(return_value=None)

        bad_name = "snap<inject>&quotes"
        bad_desc = "desc<tag>&and"

        inst.create_snapshot(bad_name, bad_desc)

        inst._snapshotCreateXML.assert_called_once()
        created_xml = inst._snapshotCreateXML.call_args[0][0]
        self.assertNotIn("<inject>", created_xml)
        self.assertNotIn("<tag>", created_xml)
        self.assertIn("&lt;inject&gt;&amp;quotes", created_xml)
        self.assertIn("&lt;tag&gt;&amp;and", created_xml)

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

    def test_secure_boot_loader_change_and_recovery(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst.wvm = MagicMock()
        uefi_xml = "<domain><os><loader readonly='yes' secure='yes' type='pflash'>/usr/share/OVMF/OVMF.fd</loader></os></domain>"
        inst._XMLDesc = MagicMock(return_value=uefi_xml)

        inst.change_snapshot_xml()
        inst.wvm.defineXML.assert_called_once()
        changed_xml = inst.wvm.defineXML.call_args[0][0]
        self.assertIn("type=\"rom\"", changed_xml)
        self.assertIn("secure=\"yes\"", changed_xml)

        # Test recovery from rom back to pflash
        inst._XMLDesc = MagicMock(return_value=changed_xml)
        inst.wvm.defineXML.reset_mock()
        inst.recover_snapshot_xml()
        inst.wvm.defineXML.assert_called_once()
        recovered_xml = inst.wvm.defineXML.call_args[0][0]
        self.assertIn("type=\"pflash\"", recovered_xml)
        self.assertIn("secure=\"yes\"", recovered_xml)

    def test_original_rom_loader_preserved(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst._XMLDesc = MagicMock(return_value="<domain><os><loader type='rom'>/usr/share/rom.bin</loader></os></domain>")
        inst._defineXML = MagicMock()
        inst._snapshotCreateXML = MagicMock(return_value=None)
        inst.get_status = MagicMock(return_value=5)

        # change_snapshot_xml should return False and not define XML
        self.assertFalse(inst.change_snapshot_xml())
        inst._defineXML.assert_not_called()

        # Create snapshot should not invoke recover_snapshot_xml if not changed
        inst.change_snapshot_xml = MagicMock(return_value=False)
        inst.recover_snapshot_xml = MagicMock()
        inst.create_snapshot("snap_rom", "testing rom preservation")
        inst.change_snapshot_xml.assert_called_once()
        inst.recover_snapshot_xml.assert_not_called()

    def test_malformed_xml_raises_in_change(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst._XMLDesc = MagicMock(return_value="<malformed << xml")
        with self.assertRaises(Exception):
            inst.change_snapshot_xml()

    def test_recovery_failure_raises(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst._XMLDesc = MagicMock(return_value="<domain><os><loader type='rom'>/path</loader></os></domain>")
        inst._defineXML = MagicMock(side_effect=RuntimeError("libvirt defineXML failed"))
        with self.assertRaises(RuntimeError):
            inst.recover_snapshot_xml()


    def test_snapshot_revert_rom_loader_preserved(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst.instance = MagicMock()
        mock_snap = MagicMock()
        inst.instance.snapshotLookupByName = MagicMock(return_value=mock_snap)
        inst.instance.revertToSnapshot = MagicMock()
        inst.change_snapshot_xml = MagicMock(return_value=False)
        inst.recover_snapshot_xml = MagicMock()

        inst.snapshot_revert("snap_rom")

        inst.change_snapshot_xml.assert_called_once()
        inst.instance.snapshotLookupByName.assert_called_once_with("snap_rom", 0)
        inst.instance.revertToSnapshot.assert_called_once_with(mock_snap, 0)
        inst.recover_snapshot_xml.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestDiskXmlEscaping(unittest.TestCase):
    def _instance(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst.get_status = MagicMock(return_value=5)  # shutoff
        inst.instance = MagicMock()
        return inst

    def test_attach_disk_escapes_source_and_serial(self):
        inst = self._instance()
        inst.attach_disk(
            "vdb",
            "/images/a'/><disk type='file",
            target_bus="virtio",
            disk_type="file",
            serial="S1</serial><evil/>",
        )
        xml = inst.instance.attachDeviceFlags.call_args[0][0]
        self.assertIn("file='/images/a&apos;/&gt;&lt;disk type=&apos;file'", xml)
        self.assertIn("<serial>S1&lt;/serial&gt;&lt;evil/&gt;</serial>", xml)
        self.assertNotIn("<evil/>", xml)

    def test_edit_disk_escapes_source_and_serial(self):
        inst = self._instance()
        inst._XMLDesc = MagicMock(
            return_value=(
                "<domain><devices><disk type='file' device='disk'>"
                "<driver name='qemu' type='qcow2'/>"
                "<source file='/images/vm.qcow2'/>"
                "<target dev='vda' bus='virtio'/>"
                "</disk></devices></domain>"
            )
        )
        inst.edit_disk(
            "vda",
            "/images/b'/><x y='",
            False,
            False,
            "virtio",
            "S2<evil/>",
            "qcow2",
            "default",
            "default",
            "default",
            "default",
        )
        xml = inst.instance.updateDeviceFlags.call_args[0][0]
        self.assertIn("file='/images/b&apos;/&gt;&lt;x y=&apos;'", xml)
        self.assertNotIn("<evil/>", xml)

    def test_detach_disk_does_not_evaluate_dev_as_xpath(self):
        inst = self._instance()
        inst._XMLDesc = MagicMock(
            return_value=(
                "<domain><devices><disk type='file' device='disk'>"
                "<source file='/images/vm.qcow2'/>"
                "<target dev='vda' bus='virtio'/>"
                "</disk></devices></domain>"
            )
        )
        with self.assertRaises(IndexError):
            inst.detach_disk("x' or '1'='1")
        inst.instance.detachDeviceFlags.assert_not_called()


class TestChangeDiskBus(unittest.TestCase):
    RBD_DOMAIN = (
        "<domain><devices>"
        "<disk type='network' device='disk'>"
        "<driver name='qemu' type='raw'/>"
        "<auth username='libvirt'><secret type='ceph' uuid='aaaa'/></auth>"
        "<source protocol='rbd' name='pool/vm-disk'><host name='mon1' port='6789'/></source>"
        "<target dev='vda' bus='virtio'/>"
        "<address type='pci' domain='0x0000' bus='0x00' slot='0x05' function='0x0'/>"
        "</disk></devices></domain>"
    )

    def _instance(self):
        inst = wvmInstance.__new__(wvmInstance)
        inst.instance = MagicMock()
        inst._XMLDesc = MagicMock(return_value=self.RBD_DOMAIN)
        return inst

    def test_keeps_disk_source_and_auth_and_only_changes_target(self):
        from lxml import etree

        inst = self._instance()
        inst.change_disk_bus("vda", "sda", "sata")

        new_xml = inst.instance.attachDeviceFlags.call_args[0][0]
        disk = etree.fromstring(new_xml)
        self.assertEqual(disk.get("type"), "network")
        self.assertEqual(disk.find("source").get("name"), "pool/vm-disk")
        self.assertEqual(disk.find("auth/secret").get("uuid"), "aaaa")
        self.assertEqual(disk.find("target").get("dev"), "sda")
        self.assertEqual(disk.find("target").get("bus"), "sata")
        self.assertIsNone(disk.find("address"))

    def test_reattaches_the_old_disk_when_attach_fails(self):
        from libvirt import libvirtError

        inst = self._instance()
        inst.instance.attachDeviceFlags.side_effect = [libvirtError("boom"), None]

        with self.assertRaises(libvirtError):
            inst.change_disk_bus("vda", "sda", "sata")

        detached_xml = inst.instance.detachDeviceFlags.call_args[0][0]
        restored_xml = inst.instance.attachDeviceFlags.call_args_list[1][0][0]
        self.assertEqual(restored_xml, detached_xml)

    def test_edit_disk_keeps_source_and_auth_of_a_network_disk(self):
        from lxml import etree

        inst = self._instance()
        inst.edit_disk(
            "vda", "ignored-path", False, False, "virtio", "", "raw",
            "default", "default", "default", "default",
        )

        disk = etree.fromstring(inst.instance.updateDeviceFlags.call_args[0][0])
        self.assertEqual(disk.get("type"), "network")
        self.assertEqual(disk.find("source").get("name"), "pool/vm-disk")
        self.assertEqual(disk.find("source/host").get("name"), "mon1")
        self.assertEqual(disk.find("auth/secret").get("uuid"), "aaaa")

    def test_edit_disk_without_format_omits_the_driver_type(self):
        from lxml import etree

        inst = wvmInstance.__new__(wvmInstance)
        inst.instance = MagicMock()
        inst._XMLDesc = MagicMock(
            return_value=(
                "<domain><devices><disk type='file' device='disk'>"
                "<driver name='qemu'/><source file='/images/vm.img'/>"
                "<target dev='vda' bus='virtio'/></disk></devices></domain>"
            )
        )
        inst.edit_disk(
            "vda", "/images/vm.img", False, False, "virtio", "", "",
            "default", "default", "default", "default",
        )
        disk = etree.fromstring(inst.instance.updateDeviceFlags.call_args[0][0])
        self.assertIsNone(disk.find("driver").get("type"))
