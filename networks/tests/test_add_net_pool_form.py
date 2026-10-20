"""The create-network form takes real subnets of the right IP version; one
it refuses is a field error, not a server error."""

from django.test import SimpleTestCase
from networks.forms import AddNetPool


def form(**fields):
    return AddNetPool({"name": "wvc-test-net", "forward": "nat", **fields})


class SubnetTests(SimpleTestCase):
    def test_subnets_are_accepted(self):
        for subnet in ("192.0.2.0/24", "192.0.2.0/30", "255.255.255.252/30"):
            with self.subTest(subnet=subnet):
                self.assertTrue(form(subnet=subnet, subnet6="2001:db8::/64").is_valid())
        self.assertTrue(form().is_valid())  # both optional

    def test_a_subnet_that_is_not_one_is_refused(self):
        for field, value in (
            ("subnet", "192.0.2.0/33"),
            ("subnet", "999.0.2.0/24"),
            ("subnet", "2001:db8::/64"),
            ("subnet6", "2001:db8::/129"),
            ("subnet6", "192.0.2.0/24"),
            # no room for a gateway, a DHCP range and a broadcast address
            ("subnet", "192.0.2.0"),
            ("subnet", "192.0.2.0/31"),
            ("subnet", "0.0.0.0/32"),
            ("subnet", "255.255.255.255/32"),
            # libvirt takes only /64 for IPv6
            ("subnet6", "2001:db8::"),
            ("subnet6", "2001:db8::/120"),
            ("subnet6", "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff/128"),
        ):
            with self.subTest(field=field, value=value):
                self.assertIn(field, form(**{field: value}).errors)
