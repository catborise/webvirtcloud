import threading
from types import SimpleNamespace

from django.db import connection
from django.test import TransactionTestCase

from computes.utils import libvirt_compute_lock, libvirt_instance_lock
from operations import claims


class OperationLocksTestCase(TransactionTestCase):
    def setUp(self):
        self.compute = SimpleNamespace(pk=987654321)
        self.first = SimpleNamespace(pk=987654321, uuid="wvc-test-uuid-1", compute=self.compute)
        self.second = SimpleNamespace(pk=987654322, uuid="wvc-test-uuid-2", compute=self.compute)

    def test_sibling_vm_can_run_while_same_vm_and_vm_set_changes_are_refused(self):
        acquired = threading.Event()
        release = threading.Event()
        errors = []

        def hold_first_vm():
            try:
                with libvirt_instance_lock(self.first):
                    acquired.set()
                    if not release.wait(10):
                        raise AssertionError("Test did not release the VM lock")
            except Exception as err:
                errors.append(err)
            finally:
                connection.close()

        worker = threading.Thread(target=hold_first_vm)
        worker.start()
        try:
            self.assertTrue(acquired.wait(10))
            with libvirt_instance_lock(self.second):
                pass
            with self.assertRaises(claims.Busy):
                with libvirt_instance_lock(self.first):
                    pass
            with self.assertRaises(claims.Busy):
                with libvirt_compute_lock(self.compute):
                    pass
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        with libvirt_compute_lock(self.compute):
            pass

    def test_instance_lock_nests_and_cannot_be_upgraded(self):
        with libvirt_instance_lock(self.first):
            with libvirt_instance_lock(self.first):
                pass
            with self.assertRaisesRegex(RuntimeError, "upgrade"):
                with libvirt_compute_lock(self.compute):
                    pass

    def test_compute_exclusive_owner_can_take_an_instance_lock(self):
        with libvirt_compute_lock(self.compute):
            with libvirt_instance_lock(self.first):
                pass
