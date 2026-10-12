"""External snapshots go through libvirt's snapshot API; WebVirtCloud only
checks preconditions and picks the flags. libvirt is mocked here; the live
cases are in instances/tests/test_live_snapshots.py."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

import libvirt
from libvirt import libvirtError
from vrtManager import util
from vrtManager.instance import PERSISTENT_XML, wvmInstance

INTERNAL = libvirt.VIR_DOMAIN_SNAPSHOT_LIST_INTERNAL
EXTERNAL = libvirt.VIR_DOMAIN_SNAPSHOT_LIST_EXTERNAL

DOMAIN = """<domain>
  <devices>
    <disk type='file' device='disk'><source file='/pool/a.qcow2'/><target dev='vda' bus='virtio'/></disk>
    <disk type='file' device='disk'><source file='/pool/later.qcow2'/><target dev='vdb' bus='virtio'/></disk>
    <disk type='file' device='cdrom'><target dev='sda' bus='sata'/></disk>
  </devices>
</domain>"""


def snapshot_xml(name, memory=False, disks=("vda",)):
    devices = "".join(
        f"<disk type='file' device='disk'><source file='/pool/{d}.qcow2'/><target dev='{d}'/></disk>" for d in disks
    )
    mem = "<memory snapshot='external' file='/pool/x.mem'/>" if memory else "<memory snapshot='no'/>"
    return (
        f"<domainsnapshot><name>{name}</name><description>d &amp; e</description>"
        f"<creationTime>1700000000</creationTime>{mem}"
        f"<domain><devices>{devices}</devices></domain></domainsnapshot>"
    )


def fake_snap(name, memory=False, disks=("vda",), current=False, children=0):
    snap = MagicMock()
    snap.getName.return_value = name
    snap.getXMLDesc.return_value = snapshot_xml(name, memory, disks)
    snap.isCurrent.return_value = current
    snap.numChildren.return_value = children
    return snap


def proxy(status=1, internal=(), external=(), xml=DOMAIN, jobs=None, managed_save=False):
    """A wvmInstance without a connection. internal/external are fake snapshots."""
    p = wvmInstance.__new__(wvmInstance)
    p._read_cache = None
    p._XMLDesc = lambda flags: xml
    p.get_status = lambda: status
    p.get_uuid = lambda: "11111111-2222-3333-4444-555555555555"
    p.get_cur_memory = lambda: 1024
    p.refresh_instance_pools = MagicMock()
    p._snapshotCreateXML = MagicMock()
    p.wvm = MagicMock()
    dom = MagicMock()
    snaps = {s.getName(): s for s in (*internal, *external)}

    def list_names(flag):
        return [s.getName() for s in {INTERNAL: internal, EXTERNAL: external}.get(flag, (*internal, *external))]

    def list_all(flag):
        return list({INTERNAL: internal, EXTERNAL: external}[flag])

    dom.snapshotListNames.side_effect = list_names
    dom.listAllSnapshots.side_effect = list_all
    dom.snapshotLookupByName.side_effect = lambda name, flags: snaps[name]
    current = [s for s in snaps.values() if s.isCurrent()]
    dom.hasCurrentSnapshot.return_value = bool(current)
    dom.snapshotCurrent.return_value = current[0] if current else None
    dom.hasManagedSaveImage.return_value = managed_save
    dom.isActive.return_value = status in (1, 3)
    dom.blockJobInfo.side_effect = lambda dev, flags: (jobs or {}).get(dev, {})
    p.instance = dom
    return p


class SnapshotListTestCase(unittest.TestCase):
    def test_entries_carry_memory_current_and_description(self):
        p = proxy(external=[fake_snap("one", memory=True, current=True)])
        [entry] = p.get_snapshot(EXTERNAL)
        self.assertEqual(entry["name"], "one")
        self.assertEqual(entry["description"], "d & e")
        self.assertEqual(entry["date"], datetime.fromtimestamp(1700000000))
        self.assertTrue(entry["memory"])
        self.assertTrue(entry["current"])

    def test_detaches_lists_disks_missing_from_the_snapshot(self):
        p = proxy(external=[fake_snap("one", disks=("vda",))])
        [entry] = p.get_snapshot(EXTERNAL)
        self.assertEqual(entry["detaches"], [{"dev": "vdb", "path": "/pool/later.qcow2"}])

    def test_detaches_comes_from_the_persistent_definition(self):
        # vdc was hot-plugged into the running VM only; vdb waits for the next start
        live = DOMAIN.replace("/pool/later.qcow2", "/pool/live-only.qcow2").replace("dev='vdb'", "dev='vdc'")
        p = proxy(external=[fake_snap("one")])
        p._XMLDesc = lambda flags: DOMAIN if flags == PERSISTENT_XML else live
        [entry] = p.get_snapshot(EXTERNAL)
        self.assertEqual(entry["detaches"], [{"dev": "vdb", "path": "/pool/later.qcow2"}])

    def test_lists_are_by_kind(self):
        p = proxy(internal=[fake_snap("i")], external=[fake_snap("e")])
        self.assertEqual([s["name"] for s in p.get_snapshot(INTERNAL)], ["i"])
        self.assertEqual([s["name"] for s in p.get_snapshot(EXTERNAL)], ["e"])


class SnapshotJobsTestCase(unittest.TestCase):
    def test_running_jobs_are_reported_per_disk(self):
        p = proxy(jobs={"vda": {"cur": 5, "end": 10, "type": 3, "bandwidth": 0}})
        self.assertEqual(p.get_snapshot_jobs(), [{"dev": "vda", "cur": 5, "end": 10}])

    def test_a_stopped_vm_has_no_jobs(self):
        p = proxy(status=5)
        self.assertEqual(p.get_snapshot_jobs(), [])
        p.instance.blockJobInfo.assert_not_called()


class SnapshotLookupTestCase(unittest.TestCase):
    def test_a_name_of_the_other_kind_is_refused(self):
        p = proxy(internal=[fake_snap("i")], external=[fake_snap("e")])
        with self.assertRaises(util.OperationError):
            p._snapshot("i", external=True)
        with self.assertRaises(util.OperationError):
            p._snapshot("e", external=False)
        self.assertEqual(p._snapshot("e", external=True).getName(), "e")


class SnapshotStateTestCase(unittest.TestCase):
    def test_managed_save_is_refused(self):
        with self.assertRaisesRegex(util.OperationError, "saved state"):
            proxy(managed_save=True)._check_snapshot_state()

    def test_running_job_is_refused(self):
        with self.assertRaisesRegex(util.OperationError, "still running"):
            proxy(jobs={"vda": {"cur": 1, "end": 2}})._check_snapshot_state()

    def test_a_mixed_vm_passes_so_it_can_be_cleaned_up(self):
        proxy(internal=[fake_snap("i")], external=[fake_snap("e")])._check_snapshot_state()

    def test_a_clean_vm_passes(self):
        proxy()._check_snapshot_state()


DISK_ONLY_ATOMIC = libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY | libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_ATOMIC


def created(p):
    xml, flags = p._snapshotCreateXML.call_args[0]
    return xml, flags


class CreateTestCase(unittest.TestCase):
    def test_disk_only_snapshot(self):
        p = proxy()
        p.create_external_snapshot("snap-1", "desc")
        xml, flags = created(p)
        self.assertEqual(flags, DISK_ONLY_ATOMIC)
        self.assertIn("<name>snap-1</name>", xml)
        self.assertNotIn("<memory", xml)
        p.refresh_instance_pools.assert_called_once()

    def test_description_is_escaped(self):
        p = proxy()
        p.create_external_snapshot("s", "<x> & 'y'")
        xml, _ = created(p)
        self.assertIn("&lt;x&gt; &amp;", xml)
        self.assertNotIn("<x>", xml)

    def test_memory_snapshot_goes_into_the_first_disks_pool(self):
        p = proxy()
        p.get_volume_by_path = MagicMock()
        pool = p.get_volume_by_path.return_value.storagePoolLookupByVolume.return_value
        pool.info.return_value = [2, 0, 0, 2 * 1024**3]
        p.create_external_snapshot("m", memory=True)
        xml, flags = created(p)
        p.get_volume_by_path.assert_called_once_with("/pool/a.qcow2")
        self.assertEqual(flags, libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_ATOMIC)
        self.assertIn(
            "<memory snapshot='external' file='/pool/11111111-2222-3333-4444-555555555555.m.mem'/>", xml
        )

    def refused(self, p, pattern, **kwargs):
        with self.assertRaisesRegex(util.OperationError, pattern):
            p.create_external_snapshot(kwargs.pop("name", "s"), **kwargs)
        p._snapshotCreateXML.assert_not_called()

    def test_invalid_names_are_refused(self):
        for name in ("", ".hidden", "a/b", "a b", "x" * 65, "<x>"):
            with self.subTest(name=name):
                self.refused(proxy(), "Snapshot names", name=name)

    def test_names_are_checked_only_on_create(self):
        # snapshots made by the old code are named s1.<name> and stay usable
        p = proxy(external=[fake_snap("s1.old", current=True)])
        p.instance.revertToSnapshot = MagicMock()
        p.revert_external_snapshot("s1.old")
        p.instance.revertToSnapshot.assert_called_once()

    def test_a_taken_name_is_refused(self):
        self.refused(proxy(external=[fake_snap("s", current=True)]), "already exists")

    def test_shared_state_checks_apply(self):
        self.refused(proxy(managed_save=True), "saved state")

    def test_internal_snapshots_are_refused(self):
        self.refused(proxy(internal=[fake_snap("i")]), "internal snapshots")

    def test_newer_snapshots_after_a_revert_are_refused(self):
        self.refused(proxy(external=[fake_snap("old", current=True, children=1)]), "Newer snapshots")

    def test_snapshots_without_a_current_one_are_refused(self):
        # a create would start a second root
        self.refused(proxy(external=[fake_snap("old")]), "revert to a snapshot first")

    def test_a_disk_that_is_not_a_file_is_refused(self):
        xml = "<domain><devices><disk type='block' device='disk'><source dev='/dev/vg/x'/><target dev='vda'/></disk></devices></domain>"
        self.refused(proxy(xml=xml), "vda is not a file")

    def test_cdrom_with_media_is_refused(self):
        xml = DOMAIN.replace("<target dev='sda'", "<source file='/iso/x.iso'/><target dev='sda'")
        self.refused(proxy(xml=xml), "Eject")

    def test_memory_needs_a_running_vm(self):
        self.refused(proxy(status=3), "running VM", memory=True)
        self.refused(proxy(status=5), "running VM", memory=True)

    def test_memory_needs_a_pool(self):
        p = proxy()
        p.get_volume_by_path = MagicMock(side_effect=libvirtError("no volume"))
        self.refused(p, "not in a storage pool", memory=True)

    def test_memory_needs_room_in_the_pool(self):
        p = proxy()
        p.get_volume_by_path = MagicMock()
        pool = p.get_volume_by_path.return_value.storagePoolLookupByVolume.return_value
        pool.info.return_value = [2, 0, 0, 512 * 1024**2]
        self.refused(p, "less free space", memory=True)


REVERT_RUNNING = libvirt.VIR_DOMAIN_SNAPSHOT_REVERT_RUNNING
REVERT_PAUSED = libvirt.VIR_DOMAIN_SNAPSHOT_REVERT_PAUSED


class RevertTestCase(unittest.TestCase):
    def reverted_with(self, status, memory=False):
        snap = fake_snap("s", memory=memory, current=True)
        p = proxy(status=status, external=[snap])
        p.revert_external_snapshot("s")
        p.instance.revertToSnapshot.assert_called_once_with(snap, unittest.mock.ANY)
        p.refresh_instance_pools.assert_called_once()
        return p.instance.revertToSnapshot.call_args[0][1]

    def test_disk_only_flags_keep_the_vm_state(self):
        self.assertEqual(self.reverted_with(1), REVERT_RUNNING)
        self.assertEqual(self.reverted_with(3), REVERT_PAUSED)
        self.assertEqual(self.reverted_with(5), 0)

    def test_memory_snapshot_resumes_as_saved(self):
        self.assertEqual(self.reverted_with(1, memory=True), 0)
        self.assertEqual(self.reverted_with(5, memory=True), 0)

    def test_shared_state_checks_apply(self):
        p = proxy(external=[fake_snap("s")], managed_save=True)
        with self.assertRaisesRegex(util.OperationError, "saved state"):
            p.revert_external_snapshot("s")
        p.instance.revertToSnapshot.assert_not_called()

    def collision(self):
        err = libvirtError("external snapshot file for disk vda already exists and is not a block device")
        err.get_error_code = lambda: libvirt.VIR_ERR_CONFIG_UNSUPPORTED
        return err

    def test_same_second_revert_collision_is_retried_once(self):
        # libvirt has already stopped a running VM when the overlay names
        # collide; the retry a second later finishes the revert.
        snap = fake_snap("s")
        p = proxy(external=[snap])
        p.instance.revertToSnapshot.side_effect = [self.collision(), None]
        with unittest.mock.patch("vrtManager.instance.time.sleep") as sleep:
            p.revert_external_snapshot("s")
        sleep.assert_called_once_with(1)
        self.assertEqual(
            p.instance.revertToSnapshot.call_args_list,
            [unittest.mock.call(snap, REVERT_RUNNING)] * 2,
        )

    def test_a_second_collision_is_reported_with_the_vm_state(self):
        p = proxy(external=[fake_snap("s")])
        p.instance.revertToSnapshot.side_effect = [self.collision(), self.collision()]
        p.instance.isActive.return_value = False
        with unittest.mock.patch("vrtManager.instance.time.sleep"):
            with self.assertRaisesRegex(util.OperationError, "try again in a moment.*shut off"):
                p.revert_external_snapshot("s")

    def test_other_libvirt_errors_pass_through(self):
        p = proxy(external=[fake_snap("s")])
        p.instance.revertToSnapshot.side_effect = libvirtError("boom")
        with self.assertRaises(libvirtError) as ctx:
            p.revert_external_snapshot("s")
        self.assertNotIsInstance(ctx.exception, util.OperationError)


    def test_an_old_uefi_snapshot_saved_with_a_rom_loader_is_refused(self):
        # The old code switched pflash to rom while taking the snapshot, so
        # the saved definition has rom; reverting would leave the VM unbootable.
        uefi = DOMAIN.replace("<devices>", "<os><loader readonly='yes' type='pflash'>/ovmf/CODE.fd</loader></os><devices>")
        snap = fake_snap("s1.old", current=True)
        snap.getXMLDesc.return_value = snapshot_xml("s1.old").replace(
            "<domain>", "<domain><os><loader readonly='yes' type='rom'>/ovmf/CODE.fd</loader></os>"
        )
        p = proxy(external=[snap], xml=uefi)
        with self.assertRaisesRegex(util.OperationError, "older version"):
            p.revert_external_snapshot("s1.old")
        p.instance.revertToSnapshot.assert_not_called()


    def test_an_old_running_uefi_snapshot_is_judged_by_its_inactive_definition(self):
        # a running VM's snapshot keeps the live definition (pflash) and the
        # persistent one (rom); the revert restores the persistent one
        uefi = DOMAIN.replace("<devices>", "<os><loader readonly='yes' type='pflash'>/ovmf/CODE.fd</loader></os><devices>")
        snap = fake_snap("s1.old", current=True)
        snap.getXMLDesc.return_value = snapshot_xml("s1.old").replace(
            "<domain>", "<domain><os><loader readonly='yes' type='pflash'>/ovmf/CODE.fd</loader></os>"
        ).replace(
            "</domainsnapshot>",
            "<inactiveDomain><os><loader readonly='yes' type='rom'>/ovmf/CODE.fd</loader></os>"
            "<devices/></inactiveDomain></domainsnapshot>",
        )
        p = proxy(external=[snap], xml=uefi)
        with self.assertRaisesRegex(util.OperationError, "older version"):
            p.revert_external_snapshot("s1.old")
        p.instance.revertToSnapshot.assert_not_called()

    def test_a_failed_retry_still_reports_the_vm_state(self):
        # the collision already stopped the VM; another error on the retry
        # must not hide that
        p = proxy(external=[fake_snap("s")])
        p.instance.revertToSnapshot.side_effect = [self.collision(), libvirtError("disk busy")]
        p.instance.isActive.return_value = False
        with unittest.mock.patch("vrtManager.instance.time.sleep"):
            with self.assertRaisesRegex(util.OperationError, "disk busy.*The VM is shut off"):
                p.revert_external_snapshot("s")


class DeleteTestCase(unittest.TestCase):
    def test_delete_is_one_libvirt_call(self):
        snap = fake_snap("s", current=True)
        p = proxy(external=[snap])
        p.delete_external_snapshot("s")
        snap.delete.assert_called_once_with(0)
        p.refresh_instance_pools.assert_called_once()

    def test_running_job_is_refused(self):
        snap = fake_snap("s")
        p = proxy(external=[snap], jobs={"vda": {"cur": 1, "end": 2}})
        with self.assertRaisesRegex(util.OperationError, "still running"):
            p.delete_external_snapshot("s")
        snap.delete.assert_not_called()


class KindTestCase(unittest.TestCase):
    def test_internal_endpoints_refuse_external_names(self):
        snap = fake_snap("e")
        p = proxy(external=[snap])
        for call in (p.snapshot_revert, p.snapshot_delete):
            with self.subTest(call.__name__), self.assertRaises(util.OperationError):
                call("e")
        snap.delete.assert_not_called()
        p.instance.revertToSnapshot.assert_not_called()

    def test_external_endpoints_refuse_internal_names(self):
        snap = fake_snap("i")
        p = proxy(internal=[snap])
        for call in (p.revert_external_snapshot, p.delete_external_snapshot):
            with self.subTest(call.__name__), self.assertRaises(util.OperationError):
                call("i")
        snap.delete.assert_not_called()

    def test_internal_create_refuses_a_vm_with_external_snapshots(self):
        p = proxy(external=[fake_snap("e")])
        with self.assertRaisesRegex(util.OperationError, "external snapshots"):
            p.create_snapshot("i")
        p._snapshotCreateXML.assert_not_called()


if __name__ == "__main__":
    unittest.main()
