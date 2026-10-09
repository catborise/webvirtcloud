"""Device edits of a VM: each definition is changed as it describes the
device, every NIC type can be a boot device, posted values stay values, and
definition changes go through _defineXML."""

import unittest
from unittest.mock import MagicMock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from lxml import etree

from vrtManager import util
from vrtManager.instance import PERSISTENT_XML, wvmInstance
from libvirt import VIR_DOMAIN_AFFECT_CONFIG, VIR_DOMAIN_AFFECT_LIVE

PERSISTENT = """<domain><os><type>hvm</type></os><devices>
<disk type='file' device='disk'><source file='/pool/a.qcow2'/><target dev='vda' bus='virtio'/></disk>
<disk type='file' device='disk'><source file='/pool/b.qcow2'/><target dev='vdb' bus='virtio'/></disk>
<interface type='bridge'><mac address='52:54:10:00:00:02'/><source bridge='br0'/></interface>
<interface type='network'><mac address='52:54:10:00:00:01'/><source network='default'/>
<bandwidth><inbound average='1' peak='2' burst='3'/></bandwidth></interface>
<video><model type='vga'/></video>
</devices></domain>"""
LIVE = """<domain><devices>
<disk type='file' device='disk'><source file='/pool/a.qcow2'/><target dev='vda' bus='virtio'/><alias name='virtio-disk0'/></disk>
</devices></domain>"""


def proxy(active=True):
    p = wvmInstance.__new__(wvmInstance)  # no connection: only these calls
    p.instance = MagicMock(**{"isActive.return_value": active})
    p._XMLDesc = lambda flags: PERSISTENT if flags == PERSISTENT_XML else LIVE
    p._defineXML = MagicMock()
    p.wvm = MagicMock()
    return p


def defined(p):
    (xml,), _ = p._defineXML.call_args
    return etree.fromstring(xml)


class DetachDiskTestCase(unittest.TestCase):
    def test_each_definition_is_detached_as_it_describes_the_disk(self):
        p = proxy()
        p.detach_disk("vda")
        (live, live_flags), (config, config_flags) = (c.args for c in p.instance.detachDeviceFlags.call_args_list)
        self.assertEqual((live_flags, config_flags), (VIR_DOMAIN_AFFECT_LIVE, VIR_DOMAIN_AFFECT_CONFIG))
        self.assertIn("virtio-disk0", live)
        self.assertNotIn("alias", config)

    def test_a_disk_only_in_the_persistent_definition_is_detached_there(self):
        p = proxy()
        p.detach_disk("vdb")
        self.assertEqual([c.args[1] for c in p.instance.detachDeviceFlags.call_args_list], [VIR_DOMAIN_AFFECT_CONFIG])

    def test_an_unknown_disk_is_refused(self):
        p = proxy()
        with self.assertRaises(util.OperationError):
            p.detach_disk("vdz")
        p.instance.detachDeviceFlags.assert_not_called()


class DeviceEditsTestCase(unittest.TestCase):
    def test_a_block_volume_is_attached_by_its_device_path(self):
        p = proxy(active=False)
        p._definitions = lambda: [(PERSISTENT_XML, VIR_DOMAIN_AFFECT_CONFIG)]
        p.attach_disk("vdc", "/dev/vg/c", disk_type="block", target_bus="virtio")
        xml, _ = p.instance.attachDeviceFlags.call_args.args
        self.assertEqual(etree.fromstring(xml).find("source").attrib, {"dev": "/dev/vg/c"})

    def test_a_bridge_nic_can_be_a_boot_device(self):
        p = proxy()
        p.set_bootorder({0: {"type": "network", "dev": "52:54:10:00:00:02"}, 1: {"type": "disk", "dev": "vdb"}})
        tree = defined(p)
        self.assertEqual(tree.xpath("devices/interface[@type='bridge']/boot/@order"), ["1"])
        self.assertEqual(tree.xpath("devices/disk[target/@dev='vdb']/boot/@order"), ["2"])

    def test_the_video_model_is_a_value(self):
        p = proxy()
        p.set_video_model("vga' heads='9")
        model = defined(p).find("devices/video/model")
        self.assertEqual(model.attrib, {"type": "vga' heads='9"})
        p._defineXML.assert_called_once()

    def test_qos_changes_go_through_define_xml(self):
        for change in (
            lambda p: p.set_qos("52:54:10:00:00:01", "outbound", 1, 2, 3),
            lambda p: p.unset_qos("52:54:10:00:00:01", "inbound"),
        ):
            p = proxy()
            change(p)
            p._defineXML.assert_called_once()
            p.wvm.defineXML.assert_not_called()

    def test_the_default_nic_model_leaves_the_choice_to_libvirt(self):
        p = proxy()
        p.change_network("52:54:10:00:00:01", "52:54:10:00:00:01", "default", "net", "default", "")
        self.assertIsNone(defined(p).find("devices/interface[@type='network']/model"))
        p = proxy(active=False)
        p.add_network("52:54:10:00:00:09", "default", model="default")
        xml, _ = p.instance.attachDeviceFlags.call_args.args
        self.assertIsNone(etree.fromstring(xml).find("model"))


if __name__ == "__main__":
    unittest.main()
