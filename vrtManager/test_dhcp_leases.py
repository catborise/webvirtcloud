"""refresh_dhcp_leases must raise a real exception (handled as a libvirt error),
not a bare string (which Python turns into TypeError, masking the cause)."""

import unittest
from unittest.mock import Mock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from vrtManager import util
from vrtManager.network import wvmNetwork


def network_with_leases(side_effect):
    net = wvmNetwork.__new__(wvmNetwork)
    net.leases = None
    net.net = Mock()
    net.net.DHCPLeases.side_effect = side_effect
    return net


class RefreshDhcpLeasesTestCase(unittest.TestCase):
    def test_a_lease_error_raises_an_operation_error(self):
        net = network_with_leases(Exception("no DHCP on this network"))
        with self.assertRaises(util.OperationError):
            net.refresh_dhcp_leases()

    def test_the_error_keeps_the_cause_and_clears_leases(self):
        cause = Exception("boom")
        net = network_with_leases(cause)
        with self.assertRaises(util.OperationError) as caught:
            net.refresh_dhcp_leases()
        self.assertIs(caught.exception.__cause__, cause)
        self.assertEqual(net.leases, [])
