"""wvmInstance.cached_reads(): inside the block the domain XML (per flags) and
the host capabilities are read from libvirt once; outside it every read goes
to libvirt, as code that changes the VM needs."""

import unittest

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from vrtManager.instance import wvmInstance


class FakeDomain:
    def __init__(self):
        self.reads = []
        self.fail = False

    def XMLDesc(self, flags):
        if self.fail:
            raise RuntimeError("libvirt error")
        self.reads.append(flags)
        return f"<domain flags='{flags}' read='{len(self.reads)}'/>"


class FakeConn:
    def __init__(self):
        self.caps = 0
        self.defined = []

    def getCapabilities(self):
        self.caps += 1
        return "<capabilities/>"

    def defineXML(self, xml):
        self.defined.append(xml)


def proxy():
    p = wvmInstance.__new__(wvmInstance)  # no connection: only these calls
    p.instance = FakeDomain()
    p.wvm = FakeConn()
    return p


class CachedReadsTestCase(unittest.TestCase):
    def test_outside_the_block_every_read_goes_to_libvirt(self):
        p = proxy()
        p._XMLDesc(0)
        p._XMLDesc(0)
        p.get_cap_xml()
        p.get_cap_xml()
        self.assertEqual((p.instance.reads, p.wvm.caps), ([0, 0], 2))

    def test_inside_the_block_each_flags_value_is_read_once(self):
        p = proxy()
        with p.cached_reads():
            first = [p._XMLDesc(0), p._XMLDesc(1), p._XMLDesc(3)]
            again = [p._XMLDesc(0), p._XMLDesc(1), p._XMLDesc(3)]
            p.get_cap_xml()
            p.get_cap_xml()
        self.assertEqual(first, again)
        self.assertEqual(p.instance.reads, [0, 1, 3])
        self.assertEqual(p.wvm.caps, 1)

    def test_the_block_ends_with_its_cache(self):
        p = proxy()
        with p.cached_reads():
            p._XMLDesc(0)
        with p.cached_reads():
            p._XMLDesc(0)
        p._XMLDesc(0)
        self.assertEqual(p.instance.reads, [0, 0, 0])

    def test_an_error_inside_the_block_still_ends_it(self):
        p = proxy()
        with self.assertRaises(ValueError), p.cached_reads():
            p._XMLDesc(0)
            raise ValueError
        p._XMLDesc(0)
        self.assertEqual(p.instance.reads, [0, 0])

    def test_a_failed_read_is_not_cached(self):
        p = proxy()
        with p.cached_reads():
            p.instance.fail = True
            with self.assertRaises(RuntimeError):
                p._XMLDesc(0)
            p.instance.fail = False
            p._XMLDesc(0)
        self.assertEqual(p.instance.reads, [0])

    def test_a_definition_change_drops_the_cache(self):
        p = proxy()
        with p.cached_reads():
            p._XMLDesc(3)
            p._defineXML("<domain/>")
            p._XMLDesc(3)
        self.assertEqual(p.instance.reads, [3, 3])

    def test_blocks_do_not_nest(self):
        p = proxy()
        with p.cached_reads(), self.assertRaises(RuntimeError), p.cached_reads():
            pass

    def test_two_proxies_do_not_share_a_cache(self):
        a, b = proxy(), proxy()
        with a.cached_reads():
            a._XMLDesc(0)
            b._XMLDesc(0)
            b._XMLDesc(0)
        self.assertEqual((a.instance.reads, b.instance.reads), ([0], [0, 0]))
