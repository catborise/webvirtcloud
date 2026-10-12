import threading
from types import SimpleNamespace

from django.test import SimpleTestCase

from computes.utils import libvirt_compute_lock, libvirt_instance_lock


class OperationLocksTestCase(SimpleTestCase):
    def setUp(self):
        self.compute = SimpleNamespace(pk=987654321)
        self.first = SimpleNamespace(pk=987654321, compute=self.compute)
        self.second = SimpleNamespace(pk=987654322, compute=self.compute)

    def test_sibling_vm_can_run_while_same_vm_and_reconcile_are_excluded(self):
        acquired = threading.Event()
        release = threading.Event()
        errors = []

        def hold_first_vm():
            try:
                with libvirt_instance_lock(self.first):
                    acquired.set()
                    if not release.wait(5):
                        raise TimeoutError("Test did not release the VM lock")
            except Exception as err:
                errors.append(err)

        worker = threading.Thread(target=hold_first_vm)
        worker.start()
        try:
            self.assertTrue(acquired.wait(2))
            with libvirt_instance_lock(self.second, timeout=0.1):
                pass
            with self.assertRaises(TimeoutError):
                with libvirt_instance_lock(self.first, timeout=0.05):
                    pass
            with self.assertRaises(TimeoutError):
                with libvirt_compute_lock(self.compute, timeout=0.05):
                    pass
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        with libvirt_compute_lock(self.compute, timeout=0.1):
            pass

    def test_instance_lock_is_reentrant_without_releasing_the_outer_lock(self):
        with libvirt_instance_lock(self.first):
            with libvirt_instance_lock(self.first):
                pass
            with self.assertRaisesRegex(RuntimeError, "Cannot upgrade"):
                with libvirt_compute_lock(self.compute, timeout=0.05):
                    pass

    def test_compute_exclusive_owner_can_take_an_instance_lock(self):
        with libvirt_compute_lock(self.compute):
            with libvirt_instance_lock(self.first):
                pass
