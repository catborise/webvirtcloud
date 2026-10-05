"""VM statistics sample CPU, disks and NICs over one shared second: the stats
view runs in a sync worker, polled every 10 s by each open VM page."""

import unittest
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from vrtManager.instance import wvmInstance

XML = """<domain><devices>
<disk type='file' device='disk'><source file='/var/lib/libvirt/images/a.qcow2'/><target dev='vda'/></disk>
<disk type='file' device='disk'><source file='/var/lib/libvirt/images/b.qcow2'/><target dev='vdb'/></disk>
<disk type='file' device='cdrom'><target dev='sda'/></disk>
<interface type='network'><target dev='vnet0'/></interface>
</devices></domain>"""


class FakeDomain:
    """Counters grow by a fixed amount per elapsed second (per sleep)."""

    def __init__(self, state):
        self.state = state
        self.second = 0

    def info(self):
        return [self.state, 0, 0, 2, self.second * 500_000_000]  # 0.5 s of CPU time per second

    def blockStats(self, dev):
        n = {"vda": 1, "vdb": 2}[dev]  # disks are read by target name
        return (0, self.second * n * 1000, 0, self.second * n * 3000, 0)

    def interfaceStats(self, dev):
        return (self.second * 100, 0, 0, 0, self.second * 50, 0, 0, 0)


class FakeConn:
    def getInfo(self):
        return ["x86_64", 0, 4]  # 4 host CPUs


def instance(state):
    proxy = wvmInstance.__new__(wvmInstance)  # no connection: only these calls
    proxy.instance = FakeDomain(state)
    proxy.wvm = FakeConn()
    proxy._XMLDesc = lambda flags: XML
    proxy.get_net_devices = lambda: [{"mac": "52:54:00:00:00:01"}]
    return proxy


class UsageTestCase(unittest.TestCase):
    def sample(self, state):
        proxy = instance(state)

        def sleep(seconds):
            proxy.instance.second += seconds

        with patch("vrtManager.instance.time.sleep", side_effect=sleep) as slept:
            result = proxy.usage()
        return result, slept

    def test_a_running_vm_is_sampled_over_one_second(self):
        (cpu, disks, nets), slept = self.sample(1)
        slept.assert_called_once_with(1)
        self.assertEqual(cpu, {"cpu": 12.5})  # 0.5 s of 4 host CPUs
        self.assertEqual(disks, [{"dev": "vda", "rd": 1000, "wr": 3000}, {"dev": "vdb", "rd": 2000, "wr": 6000}])
        self.assertEqual(nets, [{"dev": 0, "rx": 800, "tx": 400}])  # bits

    def test_a_vm_that_is_not_running_is_not_sampled(self):
        (cpu, disks, nets), slept = self.sample(5)
        slept.assert_not_called()
        self.assertEqual(cpu, {"cpu": 0})
        self.assertEqual(disks, [{"dev": "vda", "rd": 0, "wr": 0}, {"dev": "vdb", "rd": 0, "wr": 0}])
        self.assertEqual(nets, [{"dev": 0, "rx": 0, "tx": 0}])
