"""The fixed-address dialogs take real MAC addresses and host names: the
browser refuses what their pattern attributes do not match."""

import re
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from lxml import html


def pattern(template, name):
    superuser = SimpleNamespace(user=SimpleNamespace(is_superuser=True))
    page = html.fromstring(render_to_string(template, {"request": superuser}))
    return re.compile(page.xpath(f"//input[@name='{name}']/@pattern")[0])


class FixedAddressFormTests(SimpleTestCase):
    def test_macs_and_host_names_are_accepted(self):
        mac = pattern("modify_ipv4_fixed_address.html", "mac")
        for value in ("52:54:00:ab:cd:ef", "52:54:00:AB:CD:EF"):
            with self.subTest(mac=value):
                self.assertTrue(mac.fullmatch(value))
        for template in ("modify_ipv4_fixed_address.html", "modify_ipv6_fixed_address.html"):
            name = pattern(template, "name")
            for value in ("web-01", "db.lan", "vm_1"):
                with self.subTest(template=template, name=value):
                    self.assertTrue(name.fullmatch(value))
            for value in ("a b", 'x"<'):
                with self.subTest(template=template, refused=value):
                    self.assertFalse(name.fullmatch(value))
