"""Host interface form: every value is validated before it goes into the interface XML."""

from unittest.mock import MagicMock, patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from lxml import etree
from vrtManager.interface import wvmInterfaces

from interfaces.forms import AddInterface

BASE = {
    "name": "br1", "itype": "bridge", "start_mode": "onboot", "netdev": "eth1",
    "ipv4_type": "static", "ipv4_addr": "192.0.2.10/24", "ipv4_gw": "192.0.2.1",
    "ipv6_type": "static", "stp": "on", "delay": 0,
}


# the host's network devices, as interfaces/views.py passes them
NETDEVS = ["eth0", "eth1", "enp129s0f0np0", "eth0.100", "bond-lan"]


def form(**values):
    return AddInterface(data={**BASE, **values}, netdevs=NETDEVS)


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


class Ipv4FormTestCase(SimpleTestCase):
    def test_dhcp_and_none_need_no_address(self):
        for ipv4_type in ("dhcp", "none"):
            with self.subTest(ipv4_type=ipv4_type):
                f = form(ipv4_type=ipv4_type, ipv4_addr="", ipv4_gw="", ipv6_type="none")
                self.assertTrue(f.is_valid(), f.errors)

    def test_valid_address_and_gateway(self):
        f = form(ipv4_addr="192.0.2.10/24", ipv4_gw="192.0.2.1", ipv6_type="none")
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["ipv4_addr"], "192.0.2.10/24")

    def test_address_without_prefix_gets_one(self):
        f = form(ipv4_addr="192.0.2.10", ipv4_gw="", ipv6_type="none")
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["ipv4_addr"], "192.0.2.10/32")

    def test_invalid_values_are_rejected(self):
        for field, value in (
            ("ipv4_addr", "192.0.2.300/24"),
            ("ipv4_addr", "192.0.2.10/24/1"),
            ("ipv4_addr", "192.0.2.10/33"),
            ("ipv4_gw", "192.0.2.300"),
            ("ipv4_gw", "192.0.2.1/24"),
        ):
            with self.subTest(field=field, value=value):
                f = form(**{"ipv6_type": "none", field: value})
                self.assertFalse(f.is_valid())
                self.assertIn(field, f.errors)

    def test_static_needs_an_address(self):
        f = form(ipv4_addr="", ipv4_gw="", ipv6_type="none")
        self.assertFalse(f.is_valid())
        self.assertIn("ipv4_addr", f.errors)


class InterfaceNameTestCase(SimpleTestCase):
    def test_linux_interface_names_are_accepted(self):
        for name in ("br0", "bond-lan", "br_mgmt", "vlan.100", "BR0", "a" * 15):
            with self.subTest(name=name):
                f = form(name=name, ipv6_type="none")
                self.assertTrue(f.is_valid(), f.errors)
        for netdev in ("enp129s0f0np0", "eth0.100", "bond-lan"):
            with self.subTest(netdev=netdev):
                f = form(netdev=netdev, ipv6_type="none")
                self.assertTrue(f.is_valid(), f.errors)

    def test_other_names_are_rejected(self):
        for field in ("name", "netdev"):
            for value in ("br 1", "br/1", "br'1", "br1\nx", "a" * 16, "", ".", ".."):
                with self.subTest(field=field, value=value):
                    f = form(**{"ipv6_type": "none", field: value})
                    self.assertFalse(f.is_valid())
                    self.assertIn(field, f.errors)

    def test_surrounding_whitespace_is_stripped(self):
        f = form(name="br1\n", ipv6_type="none")
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["name"], "br1")

    def test_a_new_interface_name_has_no_alias_colon(self):
        f = form(name="br0:1", ipv6_type="none")
        self.assertFalse(f.is_valid())
        self.assertIn("name", f.errors)


class Ipv4XmlTestCase(SimpleTestCase):
    def xml(self, addr, gw):
        conn = wvmInterfaces.__new__(wvmInterfaces)
        conn.define_iface = MagicMock()
        conn.get_iface = MagicMock()
        conn.create_iface("br1", "bridge", "onboot", "eth1", "static", addr, gw, "none", "", "", "on", 0)
        return etree.fromstring(conn.define_iface.call_args[0][0])

    def test_static_address_with_gateway(self):
        proto = self.xml("192.0.2.10/24", "192.0.2.1").find("protocol[@family='ipv4']")
        self.assertEqual(proto.find("ip").attrib, {"address": "192.0.2.10", "prefix": "24"})
        self.assertEqual(proto.find("route").get("gateway"), "192.0.2.1")

    def test_no_empty_route_without_gateway(self):
        self.assertIsNone(self.xml("192.0.2.10/24", "").find("protocol[@family='ipv4']/route"))


class HostDeviceTestCase(SimpleTestCase):
    def test_a_bridge_member_must_be_a_host_device(self):
        for netdev in ("eth9", "eth0:1"):
            with self.subTest(netdev=netdev):
                f = form(netdev=netdev, ipv6_type="none")
                self.assertFalse(f.is_valid())
                self.assertIn("netdev", f.errors)

    def test_a_bridge_cannot_contain_itself(self):
        f = form(name="eth1", netdev="eth1", ipv6_type="none")
        self.assertFalse(f.is_valid())
        self.assertIn("netdev", f.errors)

    def test_an_ethernet_interface_configures_a_host_device(self):
        self.assertTrue(form(itype="ethernet", name="eth0", ipv6_type="none").is_valid())
        f = form(itype="ethernet", name="eth9", ipv6_type="none")
        self.assertFalse(f.is_valid())
        self.assertIn("name", f.errors)


class BridgeSettingsTestCase(SimpleTestCase):
    def test_a_bridge_needs_stp_and_a_delay(self):
        for field in ("stp", "delay"):
            with self.subTest(field=field):
                f = form(**{"ipv6_type": "none", field: ""})
                self.assertFalse(f.is_valid())
                self.assertIn(field, f.errors)

    def test_the_delay_is_not_negative(self):
        f = form(delay=-1, ipv6_type="none")
        self.assertFalse(f.is_valid())
        self.assertIn("delay", f.errors)


class AddressNotationTestCase(SimpleTestCase):
    def test_ipv4_netmask_notation_becomes_a_prefix(self):
        f = form(ipv4_addr="192.0.2.10/255.255.255.0", ipv6_type="none")
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["ipv4_addr"], "192.0.2.10/24")

    def test_ipv6_scope_ids_are_rejected(self):
        for field, value in (("ipv6_addr", "fe80::1%eth0/64"), ("ipv6_gw", "fe80::1%eth0")):
            with self.subTest(field=field):
                f = form(**{"ipv6_addr": "2001:db8::10/64", "ipv6_gw": "", field: value})
                self.assertFalse(f.is_valid())
                self.assertIn(field, f.errors)


class CreateViewTestCase(TestCase):
    """The page validates the device against the host's devices before it
    defines an interface."""

    def test_only_a_host_device_becomes_a_bridge_member(self):
        admin = get_user_model().objects.create_superuser("iface_admin", "a@example.com", "x")
        compute = Compute.objects.create(name="iface-c", hostname="127.0.0.1:1", login="root", password="", type=1)
        self.client.force_login(admin)
        for netdev, created in (("eth9", False), ("eth0", True)):
            with self.subTest(netdev=netdev), patch("interfaces.views.wvmInterfaces") as conn_cls:
                conn = conn_cls.return_value
                conn.get_ifaces.return_value = []
                conn.get_net_devices.return_value = ["eth0", "eth1"]
                self.client.post(
                    reverse("interfaces", args=[compute.id]),
                    {**BASE, "netdev": netdev, "ipv6_type": "none", "create": ""},
                )
                self.assertEqual(conn.create_iface.called, created)

    def test_unreadable_host_devices_are_reported_not_blamed_on_the_input(self):
        admin = get_user_model().objects.create_superuser("iface_admin2", "b@example.com", "x")
        compute = Compute.objects.create(name="iface-c2", hostname="127.0.0.1:1", login="root", password="", type=1)
        self.client.force_login(admin)
        with patch("interfaces.views.wvmInterfaces") as conn_cls:
            conn = conn_cls.return_value
            conn.get_ifaces.return_value = []
            conn.get_net_devices.side_effect = RuntimeError("nodedev driver not running")
            response = self.client.post(
                reverse("interfaces", args=[compute.id]),
                {**BASE, "netdev": "eth0", "ipv6_type": "none", "create": ""},
            )
        self.assertFalse(conn.create_iface.called)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("nodedev driver not running" in m for m in messages), messages)
        self.assertFalse(any("not on this host" in m for m in messages), messages)


class EthernetDeviceTestCase(SimpleTestCase):
    def test_an_ethernet_interface_needs_no_bridge_member(self):
        self.assertTrue(form(itype="ethernet", name="eth0", netdev="", ipv6_type="none").is_valid())

    def test_a_bridge_needs_a_member(self):
        f = form(netdev="", ipv6_type="none")
        self.assertFalse(f.is_valid())
        self.assertIn("netdev", f.errors)
