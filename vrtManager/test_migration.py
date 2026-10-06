"""Migration flags: the source definition never stays behind."""

import unittest

import libvirt
from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from vrtManager.instance import wvmInstances


class FakeDomain:
    def __init__(self):
        self.calls = []

    def migrate(self, dconn, flags, dname, uri, bandwidth):
        self.calls.append((flags, uri))


class FakeSource:
    def __init__(self, state):
        self.state = state
        self.instance = FakeDomain()

    def get_status(self):
        return self.state

    def get_arch(self):
        return "x86_64"

    def get_dom_emulator(self):
        return "/usr/bin/qemu-system-x86_64"


def destination():
    dest = wvmInstances.__new__(wvmInstances)
    dest.wvm = object()
    dest.get_emulator = lambda arch: "/usr/bin/qemu-system-x86_64"
    return dest


class MigrationFlagsTestCase(unittest.TestCase):
    def test_the_source_definition_is_always_removed(self):
        for state, live, offline in ((1, True, False), (5, False, True)):
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
