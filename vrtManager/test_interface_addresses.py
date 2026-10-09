"""A running VM's addresses come from the guest agent when it answers and
from the host's ARP table, so a VM without an agent shows its IPv4 address."""

import unittest
from unittest.mock import MagicMock, patch

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from libvirt import VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT, VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_ARP
from vrtManager.instance import wvmInstance

MAC = "52:54:00:00:00:01"
ARP = {"vnet0": {"hwaddr": MAC, "addrs": [{"type": 0, "addr": "192.0.2.10", "prefix": 0}]}}
AGENT = {"eth0": {"hwaddr": MAC, "addrs": [{"type": 0, "addr": "192.0.2.20", "prefix": 24}]}}


def vm(agent):
    inst = wvmInstance.__new__(wvmInstance)
    inst._ip_cache = None
    inst.instance = MagicMock()
    inst.instance.interfaceAddresses.side_effect = lambda source: {
        VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT: AGENT,
        VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_ARP: ARP,
    }[source]
    patch.object(inst, "get_status", return_value=1).start()
    patch.object(inst, "is_agent_ready", return_value=agent).start()
    return inst


class InterfaceAddressesTestCase(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def test_a_vm_without_an_agent_shows_its_arp_address(self):
        inst = vm(agent=False)
        self.assertEqual(inst.get_interface_addresses(MAC), (["192.0.2.10"], []))
        self.assertEqual(
            [c.args for c in inst.instance.interfaceAddresses.call_args_list], [(VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_ARP,)]
        )

    def test_the_agents_address_comes_first(self):
        inst = vm(agent=True)
        self.assertEqual(inst.get_interface_addresses(MAC), (["192.0.2.20"], []))
        self.assertEqual(
            [c.args for c in inst.instance.interfaceAddresses.call_args_list],
            [(VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT,), (VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_ARP,)],
        )


if __name__ == "__main__":
    unittest.main()
