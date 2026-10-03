import unittest
from unittest.mock import MagicMock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from libvirt import VIR_DOMAIN_AFFECT_CONFIG, libvirtError
from vrtManager.instance import wvmInstance

DOMAIN_XML = """<domain><devices>
<interface type='network'><mac address='52:54:00:00:00:01'/><source network='default'/><model type='virtio'/></interface>
<interface type='network'><mac address='52:54:00:00:00:02'/><source network='default'/><model type='virtio'/></interface>
</devices></domain>"""


def stopped_instance():
    inst = wvmInstance.__new__(wvmInstance)
    inst.instance = MagicMock()
    inst._XMLDesc = MagicMock(return_value=DOMAIN_XML)
    inst.get_status = MagicMock(return_value=5)
    return inst


class TestChangeNetwork(unittest.TestCase):
    def test_replaces_the_nic_with_the_old_mac(self):
        inst = stopped_instance()
        inst.change_network("52:54:00:00:00:02", "52:54:00:00:00:03", "default", "net", "e1000", "")

        detached = inst.instance.detachDeviceFlags.call_args[0][0]
        self.assertIn("52:54:00:00:00:02", detached)
        attached = inst.instance.attachDeviceFlags.call_args[0][0]
        self.assertIn("52:54:00:00:00:03", attached)
        self.assertIn("e1000", attached)

    def test_unknown_old_mac_adds_nothing(self):
        inst = stopped_instance()
        with self.assertRaises(libvirtError):
            inst.change_network("52:54:00:00:00:09", "52:54:00:00:00:03", "default", "net", "virtio", "")
        inst.instance.detachDeviceFlags.assert_not_called()
        inst.instance.attachDeviceFlags.assert_not_called()

    def test_failed_add_restores_the_old_nic(self):
        inst = stopped_instance()
        inst.instance.attachDeviceFlags.side_effect = [libvirtError("bad source"), 0]
        with self.assertRaises(libvirtError):
            inst.change_network("52:54:00:00:00:01", "52:54:00:00:00:03", "missing", "net", "virtio", "")

        restored, flags = inst.instance.attachDeviceFlags.call_args[0]
        self.assertIn("52:54:00:00:00:01", restored)
        self.assertEqual(flags, VIR_DOMAIN_AFFECT_CONFIG)


class TestAddNetworkXml(unittest.TestCase):
    def attached(self, **kwargs):
        from lxml import etree

        inst = stopped_instance()
        inst.add_network(**kwargs)
        return etree.fromstring(inst.instance.attachDeviceFlags.call_args[0][0])

    def test_posted_values_cannot_inject_elements(self):
        iface = self.attached(
            mac_address="52:54:00:00:00:05",
            source="default",
            model="virtio'/><filesystem type='mount'/><model type='x",
            nwfilter="f'/><evil/>",
        )
        self.assertEqual([el.tag for el in iface], ["mac", "source", "model", "filterref"])
        self.assertEqual(iface.find("model").get("type"), "virtio'/><filesystem type='mount'/><model type='x")

    def test_missing_mac_is_left_to_libvirt(self):
        iface = self.attached(mac_address="", source="default")
        self.assertIsNone(iface.find("mac"))


if __name__ == "__main__":
    unittest.main()
