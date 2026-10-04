"""Live tests for cases that lost disks or changed the wrong VM.

Each test asserts the safe behavior. Known unfixed cases are marked
expectedFailure; a fix turns them into unexpected successes, which fail
the run until the marker is removed. Runs only with
TEST_LIBVIRT_HOST set (see instances/livetest.py).
"""

import os
import unittest

import libvirt
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from vrtManager.connection import connection_manager

from . import livetest
from .models import Instance
from .utils import refr

P = livetest.PREFIX


class LiveDataLossTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        if not livetest.enabled():
            raise unittest.SkipTest("Set TEST_LIBVIRT_HOST to run the live libvirt tests")
        cls.compute_args = dict(
            hostname=os.environ["TEST_LIBVIRT_HOST"],
            login=os.environ.get("TEST_LIBVIRT_LOGIN", ""),
            password=os.environ.get("TEST_LIBVIRT_PASSWORD", ""),
            type=int(os.environ.get("TEST_LIBVIRT_TYPE", 4)),
        )
        cls.conn = connection_manager.get_connection(*cls.compute_args.values())
        livetest.cleanup(cls.conn)
        livetest.ensure_pool(cls.conn)
        cls.inventory = livetest.inventory(cls.conn)
        # Last: a failure above must not leave the class transaction open,
        # which would lock the shared test database for later test classes.
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
        self.compute = Compute.objects.create(name="live-dataloss", details="test", **self.compute_args)
        admin = get_user_model().objects.create_superuser("live_admin", "l@example.com", "pw")
        self.client.force_login(admin)
        self.client.raise_request_exception = False

    def tearDown(self):
        livetest.cleanup(self.conn)

    # helpers

    def vm(self, name, disks, uefi=False, extra_devices=""):
        dom = livetest.define_vm(self.conn, P + name, disks, uefi=uefi, extra_devices=extra_devices)
        refr(self.compute)
        return dom, Instance.objects.get(compute=self.compute, uuid=dom.UUIDString())

    def post(self, view, instance, data=None):
        return self.client.post(
            reverse(f"instances:{view}", args=[instance.id]),
            data or {},
            HTTP_REFERER=reverse("instances:instance", args=[instance.id]),
        )

    def disk_sources(self, dom, flags=0):
        from lxml import etree

        return {
            d.find("target").get("dev"): d.find("source").get("file")
            for d in etree.fromstring(dom.XMLDesc(flags)).findall("devices/disk")
        }

    def domain_exists(self, uuid):
        try:
            self.conn.lookupByUUIDString(uuid)
            return True
        except libvirt.libvirtError:
            return False

    # External snapshots

    @unittest.expectedFailure
    def test_revert_external_snapshot_keeps_disk_added_later(self):
        base = livetest.create_volume(self.conn, P + "r03-a")
        dom, inst = self.vm("r03-a", [base])
        self.post("create_external_snapshot", inst, {"name": "snap"})

        later = livetest.create_volume(self.conn, P + "r03-later")
        dom.attachDeviceFlags(
            f"<disk type='file' device='disk'><driver name='qemu' type='qcow2'/>"
            f"<source file='{later}'/><target dev='vdb' bus='virtio'/></disk>",
            libvirt.VIR_DOMAIN_AFFECT_CONFIG,
        )
        self.post("revert_external_snapshot", inst, {"name": "s1.snap", "date": "", "desc": ""})

        self.assertTrue(livetest.volume_exists(self.conn, later), "revert deleted a disk the snapshot never had")

    @unittest.expectedFailure
    def test_delete_older_external_snapshot_keeps_newer_snapshot_files(self):
        base = livetest.create_volume(self.conn, P + "r03-b")
        dom, inst = self.vm("r03-b", [base])
        self.post("create_external_snapshot", inst, {"name": "older"})
        self.post("create_external_snapshot", inst, {"name": "newer"})
        sources = set(self.disk_sources(dom).values())

        self.post("delete_external_snapshot", inst, {"name": "s1.older"})

        for path in sources:
            self.assertTrue(livetest.volume_exists(self.conn, path), f"{path} was deleted")
        self.assertIn("s1.newer", dom.snapshotListNames(0))

    # Internal snapshots of UEFI VMs

    def _uefi_internal_snapshot(self, running):
        base = livetest.create_volume(self.conn, P + "r13")
        dom, inst = self.vm("r13", [base], uefi=True)
        if running:
            dom.create()
        os_before = self.os_xml(dom)

        response = self.post("snapshot", inst, {"name": "snap"})

        # Taken where libvirt supports it, otherwise refused with a message;
        # never a server error and never a rewritten loader or NVRAM.
        self.assertEqual(response.status_code, 302)
        if dom.snapshotNum(0) == 0:
            self.assertTrue(list(get_messages(response.wsgi_request)))
        self.assertEqual(self.os_xml(dom), os_before)

    def os_xml(self, dom):
        from lxml import etree

        return etree.tostring(etree.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)).find("os"))

    def test_uefi_internal_snapshot_of_a_stopped_vm(self):
        self._uefi_internal_snapshot(running=False)

    def test_uefi_internal_snapshot_of_a_running_vm(self):
        self._uefi_internal_snapshot(running=True)

    # Destroy

    def test_destroy_keeps_a_volume_another_vm_uses(self):
        shared = livetest.create_volume(self.conn, P + "r14-shared")
        _, inst = self.vm("r14-a", [shared])
        self.vm("r14-b", [shared])

        response = self.post("destroy", inst, {"delete_disk": "1"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(livetest.volume_exists(self.conn, shared), "destroy deleted another VM's disk")

    def _destroy_base_of_another_vms_overlay(self, running):
        base = livetest.create_volume(self.conn, P + "r14-base")
        pool = self.conn.storagePoolLookupByName(livetest.POOL)
        overlay = pool.createXML(
            f"<volume><name>{P}r14-overlay.qcow2</name><capacity unit='MiB'>64</capacity>"
            f"<target><format type='qcow2'/></target><backingStore><path>{base}</path>"
            "<format type='qcow2'/></backingStore></volume>",
            0,
        ).path()
        _, inst = self.vm("r14-base", [base])
        dom_b, _ = self.vm("r14-overlay", [overlay])
        if running:
            dom_b.create()

        response = self.post("destroy", inst, {"delete_disk": "1"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(livetest.volume_exists(self.conn, base), "deleted the backing file of another VM's disk")

    def test_destroy_keeps_the_backing_file_of_a_stopped_vm(self):
        self._destroy_base_of_another_vms_overlay(running=False)

    def test_destroy_keeps_the_backing_file_of_a_running_vm(self):
        self._destroy_base_of_another_vms_overlay(running=True)

    def test_destroy_with_managed_save_removes_the_vm(self):
        base = livetest.create_volume(self.conn, P + "r14-save")
        dom, inst = self.vm("r14-save", [base])
        dom.create()
        dom.managedSave(0)

        self.post("destroy", inst, {"delete_disk": "1"})

        uuid = dom.UUIDString()
        self.assertFalse(self.domain_exists(uuid), "VM is still defined")
        self.assertFalse(Instance.objects.filter(uuid=uuid).exists())

    def test_destroy_of_a_paused_vm_removes_it(self):
        base = livetest.create_volume(self.conn, P + "r14-paused")
        dom, inst = self.vm("r14-paused", [base])
        dom.create()
        dom.suspend()

        self.post("destroy", inst)

        self.assertFalse(self.domain_exists(dom.UUIDString()), "paused VM is still running")

    # Delete a volume only after it is detached

    def _delete_attached_volume(self, pause):
        base = livetest.create_volume(self.conn, P + "s02")
        data = livetest.create_volume(self.conn, P + "s02-data")
        dom, inst = self.vm("s02", [base, data])
        dom.create()
        if pause:
            dom.suspend()

        response = self.post("delete_vol", inst, {"dev": "vdb"})

        self.assertEqual(response.status_code, 302)

        # The guest has no OS, so it never releases the disk: the live
        # definition keeps vdb, and the volume must be kept with it.
        self.assertIn("vdb", self.disk_sources(dom))
        self.assertTrue(livetest.volume_exists(self.conn, data), "volume deleted while the VM still uses it")

    def test_delete_volume_of_a_paused_vm(self):
        self._delete_attached_volume(pause=True)

    def test_delete_volume_of_a_running_vm_without_guest_ack(self):
        self._delete_attached_volume(pause=False)

    def test_delete_volume_another_vm_uses_is_refused(self):
        base = livetest.create_volume(self.conn, P + "s02-own")
        shared = livetest.create_volume(self.conn, P + "s02-shared")
        _, inst = self.vm("s02-a", [base, shared])
        self.vm("s02-b", [shared])

        response = self.post("delete_vol", inst, {"dev": "vdb"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(livetest.volume_exists(self.conn, shared), "deleted another VM's disk")

    # Operations follow the VM's UUID, not its name

    def test_mutation_follows_uuid_after_names_are_swapped(self):
        dom_a, inst_a = self.vm("r06-a", [])
        dom_b, _ = self.vm("r06-b", [])
        dom_a.rename(P + "r06-tmp", 0)
        dom_b.rename(P + "r06-a", 0)
        dom_a.rename(P + "r06-b", 0)

        self.post("change_options", inst_a, {"title": "for-a", "description": ""})

        self.assertIn("<title>for-a</title>", dom_a.XMLDesc(0))
        self.assertNotIn("<title>for-a</title>", dom_b.XMLDesc(0))

    def test_vm_replaced_under_the_same_name_is_not_touched(self):
        dom_old, inst = self.vm("r06-c", [])
        dom_old.undefine()
        dom_new = livetest.define_vm(self.conn, P + "r06-c", [])

        self.post("change_options", inst, {"title": "for-old", "description": ""})

        self.assertNotIn("<title>for-old</title>", dom_new.XMLDesc(0))

    # NIC edits of a running VM are pending until restart; the form must show them

    def test_pending_nic_change_is_shown_and_can_be_edited_again(self):
        nic = "<interface type='network'><mac address='52:54:00:aa:05:01'/><source network='default'/></interface>"
        dom, inst = self.vm("nic-pending", [], extra_devices=nic)
        dom.create()

        def change(old, new):
            return self.post("change_network", inst, {
                "net-old-mac-0": old, "net-mac-0": new, "net-source-0": "net:default", "net-model-0": "virtio",
            })

        change("52:54:00:aa:05:01", "52:54:00:aa:05:02")
        page = self.client.get(reverse("instances:instance", args=[inst.id])).content.decode()
        self.assertIn('name="net-old-mac-0" value="52:54:00:aa:05:02"', page)

        change("52:54:00:aa:05:02", "52:54:00:aa:05:03")
        self.assertIn("52:54:00:aa:05:03", dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))

