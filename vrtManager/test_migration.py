"""Migration: the source definition never stays behind; a shut-off VM's
definition is moved by the app, not by libvirt's offline migration."""

import unittest

import libvirt
from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from vrtManager import util
from vrtManager.instance import wvmInstances


def no_domain():
    error = libvirt.libvirtError("Domain not found")
    error.err = (libvirt.VIR_ERR_NO_DOMAIN,)
    return error


class FakeDomain:
    def __init__(self, xml="<domain/>", snapshots=0, saved=False, undefine_fails=False, undefine_lost=False,
                 started=False):
        self.calls = []
        self.xml = xml
        self.snapshots = snapshots
        self.saved = saved
        self.undefine_fails = undefine_fails
        self.undefine_lost = undefine_lost  # undefined, but the reply is lost
        self.started = started  # started by another client during the move
        self.undefined = None
        self.redefined = []
        self.restored_autostart = None

    def migrate(self, dconn, flags, dname, uri, bandwidth):
        self.calls.append((flags, uri))

    def name(self):
        return "vm"

    def UUIDString(self):
        return "u-1"

    def XMLDesc(self, flags):
        return self.xml

    def snapshotNum(self, flags=0):
        return self.snapshots

    def hasManagedSaveImage(self, flags=0):
        return self.saved

    def undefineFlags(self, flags):
        if self.undefine_fails:
            raise libvirt.libvirtError("undefine failed")
        self.undefined = flags
        if self.undefine_lost:
            raise libvirt.libvirtError("Cannot recv data")

    def isPersistent(self):
        return self.undefined is None or bool(self.redefined)

    def isActive(self):
        if not self.started and self.undefined is not None:
            raise no_domain()  # libvirt: an undefined shut-off VM is gone
        return self.started

    def autostart(self):
        return 1

    def setAutostart(self, value):
        self.restored_autostart = value

    def connect(self):
        return self

    def defineXML(self, xml):
        self.redefined.append(xml)
        return self


class FakeHost:
    """The destination's libvirt connection."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.defined = []
        self.new = []

    def lookup(self, key):
        if key in self.existing:
            return FakeDomain()
        raise no_domain()

    lookupByName = lookupByUUIDString = lookup

    def defineXML(self, xml):
        self.defined.append(xml)
        self.new.append(FakeDomain())
        return self.new[-1]


class FakeSource:
    def __init__(self, state, **domain):
        self.state = state
        self.instance = FakeDomain(**domain)

    def get_status(self):
        return self.state

    def get_arch(self):
        return "x86_64"

    def get_dom_emulator(self):
        return "/usr/bin/qemu-system-x86_64"


def destination(host=None):
    dest = wvmInstances.__new__(wvmInstances)
    dest.wvm = host or FakeHost()
    dest.get_emulator = lambda arch: "/usr/bin/qemu-system-x86_64"
    return dest


class MigrationFlagsTestCase(unittest.TestCase):
    def test_the_source_definition_is_always_removed(self):
        for state, live, offline in ((1, True, False), (3, False, False)):
            with self.subTest(state=state):
                source = FakeSource(state)
                destination().moveto(source, "vm", live=live, unsafe=False, offline=offline)
                flags = source.instance.calls[0][0]
                self.assertTrue(flags & libvirt.VIR_MIGRATE_UNDEFINE_SOURCE)
                self.assertTrue(flags & libvirt.VIR_MIGRATE_PERSIST_DEST)

    def test_the_migration_uri_is_passed_on(self):
        source = FakeSource(1)
        destination().moveto(source, "vm", live=True, unsafe=False, offline=False, uri="tcp://192.0.2.12")
        self.assertEqual(source.instance.calls[0][1], "tcp://192.0.2.12")
        source = FakeSource(1)
        destination().moveto(source, "vm", live=True, unsafe=False, offline=False)
        self.assertIsNone(source.instance.calls[0][1])


class ShutOffMigrationTestCase(unittest.TestCase):
    """libvirt's offline migration fails on some builds (RHEL-156800); a
    shut-off VM is only its definition, so the app moves that itself."""

    def test_the_definition_moves_to_the_destination(self):
        host = FakeHost()
        source = FakeSource(5, xml="<domain><name>vm</name></domain>")
        destination(host).moveto(source, "vm", live=False, unsafe=False, offline=True)
        self.assertEqual(host.defined, ["<domain><name>vm</name></domain>"])
        self.assertEqual(source.instance.calls, [])
        self.assertEqual(source.instance.undefined, libvirt.VIR_DOMAIN_UNDEFINE_KEEP_NVRAM)

    def test_tpm_state_stays_on_the_source(self):
        source = FakeSource(5, xml="<domain><devices><tpm model='tpm-crb'/></devices></domain>")
        destination().moveto(source, "vm", live=False, unsafe=False, offline=True)
        self.assertEqual(
            source.instance.undefined,
            libvirt.VIR_DOMAIN_UNDEFINE_KEEP_NVRAM | libvirt.VIR_DOMAIN_UNDEFINE_KEEP_TPM,
        )

    def test_refused_before_anything_changes(self):
        cases = {
            "snapshots": (FakeHost(), {"snapshots": 2}),
            "saved state": (FakeHost(), {"saved": True}),
            "same uuid on the destination": (FakeHost(existing={"u-1"}), {}),
            "same name on the destination": (FakeHost(existing={"vm"}), {}),
        }
        for case, (host, domain) in cases.items():
            with self.subTest(case=case):
                source = FakeSource(5, **domain)
                with self.assertRaises(util.OperationError):
                    destination(host).moveto(source, "vm", live=False, unsafe=False, offline=True)
                self.assertEqual(host.defined, [])
                self.assertIsNone(source.instance.undefined)

    def test_a_failed_source_undefine_removes_the_new_definition(self):
        host = FakeHost()
        source = FakeSource(5, undefine_fails=True)
        with self.assertRaises(libvirt.libvirtError):
            destination(host).moveto(source, "vm", live=False, unsafe=False, offline=True)
        self.assertIsNotNone(host.new[0].undefined)

    def test_the_destination_definition_stays_when_the_source_has_none(self):
        host = FakeHost()
        source = FakeSource(5, undefine_lost=True)
        with self.assertRaisesRegex(util.OperationError, "only on the destination"):
            destination(host).moveto(source, "vm", live=False, unsafe=False, offline=True)
        self.assertIsNone(host.new[0].undefined)

    def test_a_failed_rollback_is_reported(self):
        host = FakeHost()
        source = FakeSource(5, undefine_fails=True)
        host.defineXML = lambda xml: host.new.append(FakeDomain(undefine_fails=True)) or host.new[-1]
        with self.assertRaisesRegex(util.OperationError, "undefine failed; the VM is now defined on both hosts"):
            destination(host).moveto(source, "vm", live=False, unsafe=False, offline=True)

    def test_a_vm_started_meanwhile_stays_on_the_source(self):
        host = FakeHost()
        source = FakeSource(5, xml="<domain><name>vm</name></domain>", started=True)
        with self.assertRaisesRegex(util.OperationError, "started on the source"):
            destination(host).moveto(source, "vm", live=False, unsafe=False, offline=True)
        self.assertEqual(source.instance.redefined, ["<domain><name>vm</name></domain>"])
        self.assertEqual(source.instance.restored_autostart, 1)
        self.assertIsNotNone(host.new[0].undefined)
