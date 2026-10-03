import unittest
from unittest.mock import MagicMock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from lxml import etree
from vrtManager.instance import PERSISTENT_XML, wvmInstance

LIVE = "<domain><devices><graphics type='vnc' passwd='live'><listen type='address'/></graphics></devices></domain>"
PERSISTENT = "<domain><devices><graphics type='spice' passwd='old'><listen type='address'/></graphics></devices></domain>"


def running_vm_with_pending_graphics_type():
    vm = wvmInstance.__new__(wvmInstance)
    vm.wvm = MagicMock()
    vm._XMLDesc = MagicMock(side_effect=lambda flags: PERSISTENT if flags == PERSISTENT_XML else LIVE)
    return vm


class ConsoleSettersTestCase(unittest.TestCase):
    """The graphics device to edit comes from the persistent definition."""

    def defined_graphics(self, vm):
        return etree.fromstring(vm.wvm.defineXML.call_args[0][0]).find("devices/graphics")

    def test_setters_edit_the_persistent_graphics_device(self):
        edits = {
            "set_console_passwd": (lambda vm: vm.set_console_passwd("new"), "passwd", "new"),
            "set_console_keymap": (lambda vm: vm.set_console_keymap("de"), "keymap", "de"),
            "set_console_listener_addr": (lambda vm: vm.set_console_listener_addr("127.0.0.1"), "listen", "127.0.0.1"),
            "set_console_type": (lambda vm: vm.set_console_type("vnc"), "type", "vnc"),
        }
        for name, (edit, attr, value) in edits.items():
            with self.subTest(name):
                vm = running_vm_with_pending_graphics_type()
                edit(vm)
                self.assertEqual(self.defined_graphics(vm).get(attr), value)


if __name__ == "__main__":
    unittest.main()
