import unittest
from unittest.mock import MagicMock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from libvirt import VIR_DOMAIN_AFFECT_CONFIG, VIR_DOMAIN_AFFECT_LIVE, libvirtError
from lxml import etree
from vrtManager.instance import wvmInstance

DOMAIN = """<domain><devices>
<disk type='file' device='cdrom'><driver name='qemu' type='raw'/><target dev='sda' bus='sata'/></disk>
<disk type='file' device='cdrom'><driver name='qemu' type='raw'/><target dev='sdb' bus='sata'/></disk>
</devices></domain>"""


def vm(active):
    inst = wvmInstance.__new__(wvmInstance)
    inst.instance = MagicMock()
    inst.instance.isActive.return_value = active
    inst._XMLDesc = MagicMock(return_value=DOMAIN)
    return inst


class IsoMediaTestCase(unittest.TestCase):
    """Media change goes through updateDeviceFlags for the right CD-ROM."""

    def updates(self, inst):
        return [
            (etree.fromstring(c.args[0]).find("target").get("dev"), etree.fromstring(c.args[0]).xpath("string(source/@file)"), c.args[1])
            for c in inst.instance.updateDeviceFlags.call_args_list
        ]

    def test_stopped_vm_changes_only_the_persistent_definition(self):
        inst = vm(active=False)
        inst._set_cdrom_media("sdb", "/iso/a.iso")
        self.assertEqual(self.updates(inst), [("sdb", "/iso/a.iso", VIR_DOMAIN_AFFECT_CONFIG)])

    def test_running_vm_changes_live_then_persistent(self):
        inst = vm(active=True)
        inst._set_cdrom_media("sdb", None)
        self.assertEqual(
            self.updates(inst), [("sdb", "", VIR_DOMAIN_AFFECT_LIVE), ("sdb", "", VIR_DOMAIN_AFFECT_CONFIG)]
        )

    def test_unknown_cdrom_changes_nothing(self):
        inst = vm(active=True)
        with self.assertRaises(libvirtError):
            inst._set_cdrom_media("sdz", "/iso/a.iso")
        inst.instance.updateDeviceFlags.assert_not_called()


if __name__ == "__main__":
    unittest.main()
