"""Deleting a host interface: netcf removes a bridge's members with the
bridge, so they are defined again on their own (as virsh iface-unbridge)."""

from unittest.mock import MagicMock

from django.test import SimpleTestCase
from lxml import etree
from vrtManager.interface import wvmInterface

ADDRESS = "<protocol family='ipv4'><ip address='192.0.2.10' prefix='24'/></protocol>"
BOND = "<interface type='bond' name='bond0'><bond mode='active-backup'><interface type='ethernet' name='eth1'/></bond></interface>"


def delete(xml):
    conn = wvmInterface.__new__(wvmInterface)
    conn.iface = MagicMock()
    conn.iface.XMLDesc.return_value = xml
    conn.wvm = MagicMock()
    conn.delete_iface()
    conn.iface.undefine.assert_called_once_with()
    return [etree.fromstring(call.args[0]) for call in conn.wvm.interfaceDefineXML.call_args_list]


class DeleteInterfaceTestCase(SimpleTestCase):
    def test_the_only_member_takes_the_bridge_start_mode_and_addresses(self):
        (bond,) = delete(f"<interface type='bridge' name='br1'><start mode='onboot'/>{ADDRESS}<bridge>{BOND}</bridge></interface>")
        self.assertEqual(bond.get("name"), "bond0")
        self.assertEqual(bond.xpath("bond/@mode"), ["active-backup"])
        self.assertEqual(bond.xpath("bond/interface/@name"), ["eth1"])
        self.assertEqual(bond.xpath("start/@mode"), ["onboot"])
        self.assertEqual(bond.xpath("protocol/ip/@address"), ["192.0.2.10"])

    def test_several_members_take_the_start_mode_but_no_address(self):
        members = delete(
            f"<interface type='bridge' name='br1'><start mode='onboot'/>{ADDRESS}<bridge>"
            "<interface type='ethernet' name='eth1'/><interface type='ethernet' name='eth2'/></bridge></interface>"
        )
        self.assertEqual([m.get("name") for m in members], ["eth1", "eth2"])
        for member in members:
            self.assertEqual(member.xpath("start/@mode"), ["onboot"])
            self.assertEqual(member.xpath("protocol"), [])

    def test_an_interface_that_is_not_a_bridge_is_only_undefined(self):
        self.assertEqual(delete(f"<interface type='ethernet' name='eth1'><start mode='onboot'/>{ADDRESS}</interface>"), [])
