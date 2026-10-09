"""A wvmConnect reads the host's info and capabilities once, and its domain
capabilities once per emulator, arch, machine and domain type: the create
page's getters parse the capabilities some 60 times, the overview reads the
info four times. An object lives for one request. The interfaces page lists
only the host's network devices, not all of them."""

import unittest
from unittest.mock import MagicMock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from libvirt import VIR_CONNECT_LIST_NODE_DEVICES_CAP_NET
from vrtManager.connection import wvmConnect
from vrtManager.hostdetails import wvmHostDetails

CAPABILITIES = """<capabilities><host><cpu><arch>x86_64</arch></cpu></host>
<guest><os_type>hvm</os_type><arch name='x86_64'><emulator>/usr/bin/qemu-kvm</emulator>
<machine>pc-q35-9.0</machine><machine canonical='pc-q35-9.0'>q35</machine><machine canonical='pc-i440fx-9.0'>pc</machine>
<domain type='qemu'/><domain type='kvm'/></arch></guest></capabilities>"""


class FakeConn:
    def __init__(self):
        self.reads = []

    def getCapabilities(self):
        self.reads.append("capabilities")
        return CAPABILITIES

    def getInfo(self):
        self.reads.append("info")
        return ["x86_64", 2048, 4, 0, 1, 2, 2, 1]

    def getMemoryStats(self, cell, flags):
        self.reads.append("memory")
        return {"buffers": 0, "free": 1048576, "cached": 0}

    def getHostname(self):
        return "host"

    def getSysinfo(self, flags):
        return "<sysinfo/>"

    def getURI(self):
        return "qemu:///system"

    def getDomainCapabilities(self, emulator, arch, machine, virttype):
        self.reads.append((emulator, arch, machine, virttype))
        return "<domainCapabilities/>"


def connection(cls=wvmConnect):
    conn = cls.__new__(cls)  # no libvirt connection: only these calls
    conn.wvm = FakeConn()
    return conn


class CapabilitiesOnceTestCase(unittest.TestCase):
    def test_the_capabilities_are_read_once_for_all_getters(self):
        conn = connection()
        for _ in range(2):
            conn.get_emulator("x86_64")
            conn.get_machine_types("x86_64")
            conn.get_hypervisors_domain_types()
            conn.get_cap_xml()
        self.assertEqual(conn.wvm.reads, ["capabilities"])

    def test_domain_capabilities_are_read_once_per_machine(self):
        conn = connection()
        for _ in range(2):
            conn.get_dom_cap_xml("x86_64", "q35")
            conn.get_dom_cap_xml("x86_64", "pc")
            conn.get_dom_cap_xml("x86_64", "unknown")  # falls back to pc
        self.assertEqual(
            conn.wvm.reads,
            ["capabilities", ("/usr/bin/qemu-kvm", "x86_64", "q35", "kvm"), ("/usr/bin/qemu-kvm", "x86_64", "pc", "kvm")],
        )

    def test_the_overview_reads_the_info_once_and_the_free_memory_each_time(self):
        conn = connection(wvmHostDetails)
        conn.get_node_info()
        conn.get_memory_usage()
        self.assertEqual(conn.get_memory_usage()["total"], 2048 * 1048576)
        self.assertEqual(conn.wvm.reads, ["info", "memory", "memory"])

    def test_two_objects_do_not_share_their_reads(self):
        a, b = connection(), connection()
        a.get_cap_xml()
        b.get_cap_xml()
        self.assertEqual((a.wvm.reads, b.wvm.reads), (["capabilities"], ["capabilities"]))

    def test_a_failed_read_is_not_kept(self):
        conn = connection()
        conn.wvm.getCapabilities = lambda: (_ for _ in ()).throw(RuntimeError("libvirt error"))
        with self.assertRaises(RuntimeError):
            conn.get_cap_xml()
        conn.wvm = FakeConn()
        conn.get_cap_xml()
        self.assertEqual(conn.wvm.reads, ["capabilities"])


class NetDevicesTestCase(unittest.TestCase):
    def test_only_the_network_devices_are_listed_and_read(self):
        conn = connection()
        devices = {
            VIR_CONNECT_LIST_NODE_DEVICES_CAP_NET: [
                MagicMock(**{"XMLDesc.return_value": f"<device><capability type='net'><interface>{name}</interface>"
                                                     "<capability type='80203'/></capability></device>"})
                for name in ("eth0", "br0")
            ]
        }
        conn.wvm.listAllDevices = lambda flags: devices.get(flags, [])
        self.assertEqual(conn.get_net_devices(), ["eth0", "br0"])
