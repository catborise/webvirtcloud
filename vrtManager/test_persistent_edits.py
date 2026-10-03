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


class XmlFidelityTestCase(unittest.TestCase):
    def test_edits_keep_metadata_namespace_prefixes(self):
        xml = (
            "<domain><metadata><app:info xmlns:app='http://example.com/x'>v</app:info></metadata>"
            "<os><type>hvm</type></os><devices><graphics type='vnc'><listen type='address'/></graphics>"
            "<disk type='file' device='disk'><target dev='vda'/></disk></devices></domain>"
        )
        edits = {
            "set_bootmenu": lambda vm: vm.set_bootmenu(1),
            "set_bootorder": lambda vm: vm.set_bootorder({0: {"type": "disk", "dev": "vda"}}),
            "set_console_keymap": lambda vm: vm.set_console_keymap("de"),
            "set_console_passwd": lambda vm: vm.set_console_passwd("x"),
            "set_console_listener_addr": lambda vm: vm.set_console_listener_addr("127.0.0.1"),
        }
        for name, edit in edits.items():
            with self.subTest(name):
                vm = wvmInstance.__new__(wvmInstance)
                vm.wvm = MagicMock()
                vm._XMLDesc = MagicMock(return_value=xml)
                edit(vm)
                self.assertIn("<app:info", vm.wvm.defineXML.call_args[0][0])

    def test_paused_vm_gets_nic_changes_live_and_persistent(self):
        from libvirt import VIR_DOMAIN_AFFECT_CONFIG, VIR_DOMAIN_AFFECT_LIVE

        vm = wvmInstance.__new__(wvmInstance)
        vm.instance = MagicMock()
        vm.instance.isActive.return_value = True
        vm.get_status = MagicMock(return_value=3)  # paused
        vm.add_network("52:54:00:00:00:09", "default", "net", "virtio")
        self.assertEqual(
            [c.args[1] for c in vm.instance.attachDeviceFlags.call_args_list],
            [VIR_DOMAIN_AFFECT_LIVE, VIR_DOMAIN_AFFECT_CONFIG],
        )


if __name__ == "__main__":
    unittest.main()
