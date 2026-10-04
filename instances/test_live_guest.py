"""Live tests that need a running guest OS with qemu-guest-agent.

Set TEST_LIBVIRT_GUEST_IMAGE to the path of an installed Linux image on the
test host: a BIOS-bootable qcow2 volume in a storage pool other than wvc-test,
with qemu-guest-agent enabled.
The tests boot wvc-test VMs from qcow2 overlays in the test pool, so the
image itself is only read. They are skipped while a running VM uses the
image, since its content would change under the overlays. Runs only with TEST_LIBVIRT_HOST set as well (see
instances/livetest.py).
"""

import json
import os
import time
import unittest

import libvirt
import libvirt_qemu
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from lxml import etree
from vrtManager.connection import connection_manager

from . import livetest
from .models import Instance
from .utils import refr

P = livetest.PREFIX
BOOT_TIMEOUT = 300


def wait_for(check, timeout, interval=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
    return check()


class LiveGuestTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = os.environ.get("TEST_LIBVIRT_GUEST_IMAGE")
        if not livetest.enabled() or not cls.image:
            raise unittest.SkipTest("Set TEST_LIBVIRT_HOST and TEST_LIBVIRT_GUEST_IMAGE to run the guest tests")
        cls.compute_args = dict(
            hostname=os.environ["TEST_LIBVIRT_HOST"],
            login=os.environ.get("TEST_LIBVIRT_LOGIN", ""),
            password=os.environ.get("TEST_LIBVIRT_PASSWORD", ""),
            type=int(os.environ.get("TEST_LIBVIRT_TYPE", 4)),
        )
        cls.conn = connection_manager.get_connection(*cls.compute_args.values())
        try:
            image = cls.conn.storageVolLookupByPath(cls.image)
        except libvirt.libvirtError as err:
            raise RuntimeError(f"TEST_LIBVIRT_GUEST_IMAGE must be a volume in a storage pool: {err}") from err
        if image.storagePoolLookupByVolume().name() == livetest.POOL:
            raise RuntimeError(f"TEST_LIBVIRT_GUEST_IMAGE must not be in the {livetest.POOL} pool, which the tests empty")
        cls.image_bytes = image.info()[1]  # overlays get the image's exact virtual size
        for dom in cls.conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE):
            if cls.image in dom.XMLDesc(0):
                raise unittest.SkipTest(f"{dom.name()} is running from {cls.image}")
        livetest.cleanup(cls.conn)
        livetest.ensure_pool(cls.conn)
        cls.inventory = livetest.inventory(cls.conn)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            livetest.remove_pool(cls.conn)
        finally:
            super().tearDownClass()
        if livetest.inventory(cls.conn) != cls.inventory:
            raise AssertionError("Live tests changed objects outside the test namespace")

    def setUp(self):
        self.compute = Compute.objects.create(name="live-guest", details="test", **self.compute_args)
        admin = get_user_model().objects.create_superuser("guest_admin", "g@example.com", "pw")
        self.client.force_login(admin)
        self.client.raise_request_exception = False

    def tearDown(self):
        livetest.cleanup(self.conn)

    def boot(self, name, data_disk=False):
        """A running copy of the guest image; returns the domain and its Instance."""
        pool = self.conn.storagePoolLookupByName(livetest.POOL)
        root = pool.createXML(
            f"<volume><name>{P}{name}.qcow2</name><capacity unit='bytes'>{self.image_bytes}</capacity>"
            "<target><format type='qcow2'/></target>"
            f"<backingStore><path>{self.image}</path><format type='qcow2'/></backingStore></volume>",
            0,
        ).path()
        data = livetest.create_volume(self.conn, P + name + "-data") if data_disk else None
        disks = f"<disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='{root}'/>" \
                "<target dev='vda' bus='virtio'/></disk>"
        if data:
            disks += f"<disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='{data}'/>" \
                     "<target dev='vdb' bus='virtio'/></disk>"
        # No PCI controllers: libvirt then adds the root ports the devices need
        # plus one free port, which the NIC hotplug test uses. With explicit
        # controllers it adds no free port.
        dom = self.conn.defineXML(f"""
<domain type='kvm'>
  <name>{P}{name}</name>
  <memory unit='MiB'>2048</memory>
  <vcpu placement='static' current='1'>2</vcpu>
  <vcpus>
    <vcpu id='0' enabled='yes' hotpluggable='no' order='1'/>
    <vcpu id='1' enabled='no' hotpluggable='yes'/>
  </vcpus>
  <os><type arch='x86_64' machine='q35'>hvm</type><boot dev='hd'/></os>
  <features><acpi/><apic/></features>
  <cpu mode='host-model'/>
  <devices>
    {disks}
    <interface type='network'><source network='default'/><model type='virtio'/></interface>
    <channel type='unix'><target type='virtio' name='org.qemu.guest_agent.0'/></channel>
    <serial type='pty'/>
  </devices>
</domain>""")
        dom.create()
        self.assertTrue(wait_for(lambda: self.agent(dom, "guest-ping") is not None, BOOT_TIMEOUT), "guest agent never answered")
        refr(self.compute)
        return dom, Instance.objects.get(compute=self.compute, uuid=dom.UUIDString())

    def agent(self, dom, command):
        try:
            return json.loads(libvirt_qemu.qemuAgentCommand(dom, json.dumps({"execute": command}), 5, 0))["return"]
        except libvirt.libvirtError:
            return None

    def post(self, view, instance, data=None):
        return self.client.post(
            reverse(f"instances:{view}", args=[instance.id]),
            data or {},
            HTTP_REFERER=reverse("instances:instance", args=[instance.id]),
        )

    def live(self, dom):
        return etree.fromstring(dom.XMLDesc(0))

    def test_deleting_a_disk_the_guest_releases_deletes_the_volume(self):
        dom, inst = self.boot("gdel", data_disk=True)
        data = self.live(dom).xpath("devices/disk[target/@dev='vdb']/source/@file")[0]

        response = self.post("delete_vol", inst, {"dev": "vdb"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.live(dom).xpath("devices/disk[target/@dev='vdb']"), "still attached")
        self.assertFalse(
            etree.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)).xpath("devices/disk[target/@dev='vdb']")
        )
        self.assertFalse(livetest.volume_exists(self.conn, data), "volume kept although the guest released it")

    def test_detaching_a_disk_removes_it_from_the_running_guest(self):
        dom, inst = self.boot("gdet", data_disk=True)
        data = self.live(dom).xpath("devices/disk[target/@dev='vdb']/source/@file")[0]

        response = self.post("detach_vol", inst, {"dev": "vdb"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(wait_for(lambda: not self.live(dom).xpath("devices/disk[target/@dev='vdb']"), 30))
        self.assertTrue(livetest.volume_exists(self.conn, data), "detach must keep the volume")

    def test_nic_hotplug_and_unplug(self):
        dom, inst = self.boot("gnic")
        mac = "52:54:00:aa:03:01"

        self.post("add_network", inst, {"add-net-mac": mac, "add-net-network": "net:default", "add-net-model": "virtio"})
        self.assertTrue(self.live(dom).xpath("devices/interface[mac/@address=$m]", m=mac), "NIC not attached")
        self.assertTrue(
            wait_for(lambda: any(i["hardware-address"] == mac for i in self.agent(dom, "guest-network-get-interfaces") or []), 60),
            "guest does not see the new NIC",
        )

        self.post("delete_network", inst, {"delete_network": mac})
        self.assertTrue(wait_for(lambda: not self.live(dom).xpath("devices/interface[mac/@address=$m]", m=mac), 30))
        self.assertFalse(
            etree.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)).xpath("devices/interface[mac/@address=$m]", m=mac)
        )

    def test_vcpu_hotplug_reaches_the_guest(self):
        dom, inst = self.boot("gcpu")

        def online():
            return sum(1 for cpu in self.agent(dom, "guest-get-vcpus") or [] if cpu["online"])

        self.assertEqual(online(), 1)
        self.post("set_vcpu", inst, {"id": "1", "set_vcpu": "True"})
        self.assertTrue(wait_for(lambda: online() == 2, 60), "guest did not bring the new vCPU online")

    def test_agent_reports_the_guest_os_and_nic(self):
        dom, inst = self.boot("gagent")

        response = self.client.get(reverse("instances:osinfo", args=[inst.id]))
        self.assertEqual(response.status_code, 200)
        # One field from each agent call osinfo() merges: osinfo, timezone, host name
        self.assertLessEqual({"kernel-release", "zone", "host-name"}, set(response.json()))

        # The NIC is matched by MAC in the agent's report. Whether the guest has
        # an address depends on its network configuration, not on WebVirtCloud.
        mac = self.live(dom).xpath("devices/interface/mac/@address")[0]
        proxy = inst.proxy
        proxy.refresh_interface_addresses()
        self.assertIn(mac, [nic["hwaddr"] for nic in proxy._ip_cache["qemuga"].values()])

    def test_poweroff_shuts_the_guest_down(self):
        dom, inst = self.boot("goff")

        # A guest that is still booting ignores or delays the request (seen:
        # 20 s to over 3 min right after the agent came up). Let the boot
        # finish, then press power off again if needed, as a user would.
        time.sleep(60)
        for _ in range(6):
            self.assertEqual(self.post("poweroff", inst).status_code, 302)
            if wait_for(lambda: not dom.isActive(), 30):
                break
        self.assertFalse(dom.isActive(), "guest did not shut down on ACPI")
