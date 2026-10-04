"""Host interface form: IPv6 values are validated before they go into the interface XML."""

from unittest.mock import MagicMock

from django.test import SimpleTestCase
from lxml import etree
from vrtManager.interface import wvmInterfaces

from interfaces.forms import AddInterface

BASE = {
    "name": "br1", "itype": "bridge", "start_mode": "onboot", "netdev": "eth1",
    "ipv4_type": "static", "ipv4_addr": "192.0.2.10/24", "ipv4_gw": "192.0.2.1",
    "ipv6_type": "static", "stp": "on", "delay": 0,
}


def form(**ipv6):
    return AddInterface(data={**BASE, **ipv6})


class Ipv6FormTestCase(SimpleTestCase):
    def test_valid_addresses_are_normalized(self):
        f = form(ipv6_addr="2001:DB8::10/64", ipv6_gw="2001:DB8::1")
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["ipv6_addr"], "2001:db8::10/64")
        self.assertEqual(f.cleaned_data["ipv6_gw"], "2001:db8::1")

    def test_address_without_prefix_gets_one(self):
        f = form(ipv6_addr="2001:db8::10", ipv6_gw="")
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["ipv6_addr"], "2001:db8::10/128")

    def test_values_that_only_start_like_an_address_are_rejected(self):
        for field, value in (
            ("ipv6_addr", "2001:db8::10/64'/><evil/>"),
            ("ipv6_gw", "2001:db8::1'/><evil/>"),
            ("ipv6_addr", "2001:db8::zz/64"),
            ("ipv6_addr", "192.0.2.10/24"),
        ):
            with self.subTest(field=field, value=value):
                f = form(**{"ipv6_addr": "2001:db8::10/64", "ipv6_gw": "", field: value})
                self.assertFalse(f.is_valid())
                self.assertIn(field, f.errors)

    def test_static_needs_an_address(self):
        f = form(ipv6_addr="", ipv6_gw="")
        self.assertFalse(f.is_valid())
        self.assertIn("ipv6_addr", f.errors)

    def test_dhcp_needs_no_address(self):
        self.assertTrue(form(ipv6_type="dhcp", ipv6_addr="", ipv6_gw="").is_valid())


class Ipv6XmlTestCase(SimpleTestCase):
    def xml(self, gw):
        conn = wvmInterfaces.__new__(wvmInterfaces)
        conn.define_iface = MagicMock()
        conn.get_iface = MagicMock()
        conn.create_iface("br1", "bridge", "onboot", "eth1", "none", "", "", "static", "2001:db8::10/64", gw, "on", 0)
        return etree.fromstring(conn.define_iface.call_args[0][0])

    def test_static_address_with_gateway(self):
        proto = self.xml("2001:db8::1").find("protocol[@family='ipv6']")
        self.assertEqual(proto.find("ip").attrib, {"address": "2001:db8::10", "prefix": "64"})
        self.assertEqual(proto.find("route").get("gateway"), "2001:db8::1")

    def test_no_empty_route_without_gateway(self):
        self.assertIsNone(self.xml("").find("protocol[@family='ipv6']/route"))
