import unittest
from unittest.mock import MagicMock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from vrtManager.instance import PERSISTENT_XML, wvmInstance

DOMAIN = """<domain><name>vm</name><memory>131072</memory><currentMemory>131072</currentMemory>
<vcpu>1</vcpu><os><type>hvm</type></os><devices>
<interface type='network'><mac address='52:54:00:00:00:01'/><source network='default'/></interface>
<graphics type='vnc' passwd='secret'><listen type='address' address='0.0.0.0'/></graphics>
<video><model type='vga' primary='yes'/></video></devices></domain>"""


class PersistentEditsTestCase(unittest.TestCase):
    """R-01/R-02: edits read the persistent definition with secrets."""

    def test_edits_read_the_persistent_secure_definition(self):
        edits = {
            "set_bootmenu": lambda vm: vm.set_bootmenu(1),
            "set_console_keymap": lambda vm: vm.set_console_keymap("de"),
            "set_video_model": lambda vm: vm.set_video_model("virtio"),
            "resize_cpu": lambda vm: vm.resize_cpu("1", "2"),
            "set_options": lambda vm: vm.set_options({"title": "t"}),
            "set_qos": lambda vm: vm.set_qos("52:54:00:00:00:01", "inbound", 1, 2, 3),
        }
        for name, edit in edits.items():
            with self.subTest(name):
                vm = wvmInstance.__new__(wvmInstance)
                vm.instance = MagicMock()
                vm.wvm = MagicMock()
                vm._XMLDesc = MagicMock(return_value=DOMAIN)
                vm.get_vcpus = MagicMock(return_value=None)
                vm.get_status = MagicMock(return_value=5)
                edit(vm)
                flags = {call.args[0] for call in vm._XMLDesc.call_args_list}
                self.assertIn(PERSISTENT_XML, flags)
                defined = (vm.wvm.defineXML.call_args or vm._XMLDesc.call_args)[0][0]
                self.assertIn("secret", str(defined))


if __name__ == "__main__":
    unittest.main()
