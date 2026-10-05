"""The VM page reads each kind of domain XML and the host capabilities once,
through the real getters, on libvirt's test driver."""

from unittest.mock import patch

import libvirt
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.models import Instance

# a VM with a VNC console, on libvirt's test driver
DOMAIN = """<domain type='test'><name>read-cache-vm</name><memory unit='MiB'>128</memory><vcpu>1</vcpu>
<os><type arch='i686'>hvm</type></os>
<devices><graphics type='vnc' port='-1' listen='127.0.0.1'><listen type='address' address='127.0.0.1'/></graphics></devices>
</domain>"""


class DetailReadsTests(TestCase):
    def setUp(self):
        self.conn = libvirt.open("test:///default")  # each open is a fresh, private test host
        self.addCleanup(self.conn.close)
        domain = self.conn.defineXML(DOMAIN)
        self.compute = Compute.objects.create(name="testdrv", hostname="localhost", login="", password="", type=4)
        self.vm = Instance.objects.create(compute=self.compute, name=domain.name(), uuid=domain.UUIDString())
        # what the test driver lacks: machine types, network filters
        for target, value in (("get_machine_types", ["pc"]), ("get_nwfilters", [])):
            patcher = patch(f"vrtManager.connection.wvmConnect.{target}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.admin = get_user_model().objects.create_superuser("dr-admin", "dr@example.com", "pw")

    def render(self):
        xml_reads, cap_reads = [], []
        real_xml, real_caps = libvirt.virDomain.XMLDesc, libvirt.virConnect.getCapabilities

        def xml(dom, flags=0):
            xml_reads.append(flags)
            return real_xml(dom, flags)

        def caps(conn):
            cap_reads.append(1)
            return real_caps(conn)

        self.client.force_login(self.admin)
        with patch("vrtManager.connection.connection_manager.get_connection", return_value=self.conn), patch.object(
            libvirt.virDomain, "XMLDesc", xml
        ), patch.object(libvirt.virConnect, "getCapabilities", caps):
            response = self.client.get(reverse("instances:instance", args=[self.vm.id]))
        self.assertEqual(response.status_code, 200)
        return response, xml_reads, cap_reads

    def test_each_xml_and_the_capabilities_are_read_once(self):
        _, xml_reads, cap_reads = self.render()
        self.assertEqual(sorted(xml_reads), sorted(set(xml_reads)), "an XML read twice")
        self.assertEqual(len(cap_reads), 1)

    def test_a_change_after_the_page_reads_fresh_xml(self):
        # outside the page the getters read libvirt each time, as changes need
        with patch("vrtManager.connection.connection_manager.get_connection", return_value=self.conn):
            proxy = self.vm.proxy
            proxy.set_console_passwd("pass1")
            proxy.set_console_keymap("de")
            self.assertEqual(proxy.get_console_passwd(), "pass1")
            self.assertEqual(proxy.get_console_keymap(), "de")
