"""Reachability probe and connections that do not block each other."""

import os
import socket
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

import libvirt
from vrtManager import connection
from vrtManager.connection import CONN_SSH, CONN_TCP, SSH_COMMAND, wvmConnectionManager


def closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class HostIsUpTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = wvmConnectionManager.__new__(wvmConnectionManager)

    def test_reachable_host_is_true(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        self.addCleanup(server.close)
        self.assertIs(self.manager.host_is_up(CONN_TCP, f"127.0.0.1:{server.getsockname()[1]}"), True)

    def test_unreachable_host_is_false_not_an_exception(self):
        self.assertIs(self.manager.host_is_up(CONN_TCP, f"127.0.0.1:{closed_port()}"), False)


class ConnectionManagerTestCase(unittest.TestCase):
    def setUp(self):
        patcher = patch("vrtManager.connection.wvmEventLoop")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = wvmConnectionManager()

    def test_a_slow_host_does_not_block_other_hosts(self):
        def open_auth(uri, auth, flags):
            if "slow" in uri:
                time.sleep(1.5)
            raise __import__("libvirt").libvirtError("refused")

        with patch("vrtManager.connection.libvirt.openAuth", side_effect=open_auth):
            slow = threading.Thread(target=lambda: self.assertRaises(Exception, self.manager.get_connection, "slow", "u", "p", CONN_TCP))
            slow.start()
            time.sleep(0.2)
            started = time.monotonic()
            with self.assertRaises(Exception):
                self.manager.get_connection("fast", "u", "p", CONN_TCP)
            self.assertLess(time.monotonic() - started, 1.0)
            slow.join()



def alive_connection():
    conn = MagicMock()
    conn.isAlive.return_value = 1
    return conn


class BoundedOpenTestCase(unittest.TestCase):
    """Opening waits at most CONNECT_TIMEOUT; a failed host is not retried
    for RETRY_AFTER; one open per connection at a time."""

    def setUp(self):
        for name, value in (("wvmEventLoop", MagicMock()), ("CONNECT_TIMEOUT", 0.3), ("RETRY_AFTER", 1.0)):
            patcher = patch(f"vrtManager.connection.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = wvmConnectionManager()
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.calls = 0

    def open_auth(self, result):
        def open_auth(uri, auth, flags):
            self.calls += 1
            self.release.wait(5)
            if isinstance(result, Exception):
                raise result
            return result
        return patch("vrtManager.connection.libvirt.openAuth", side_effect=open_auth)

    def get(self, host="h"):
        started = time.monotonic()
        try:
            return self.manager.get_connection(host, "u", "p", CONN_TCP), time.monotonic() - started
        except libvirt.libvirtError as err:
            return err, time.monotonic() - started

    def test_a_hanging_open_is_given_up_after_the_timeout(self):
        with self.open_auth(alive_connection()):
            result, took = self.get()
        self.assertIsInstance(result, libvirt.libvirtError)
        self.assertIn("did not answer within", str(result))
        self.assertLess(took, 1.0)

    def test_within_retry_after_the_last_error_comes_at_once(self):
        with self.open_auth(alive_connection()):
            self.get()
            result, took = self.get()
        self.assertIn("did not answer within", str(result))
        self.assertLess(took, 0.1)
        self.assertEqual(self.calls, 1)

    def test_a_late_success_is_used(self):
        conn = alive_connection()
        with self.open_auth(conn):
            self.get()
            self.release.set()
            time.sleep(0.1)
            result, took = self.get()
        self.assertIs(result, conn)
        self.assertEqual(self.calls, 1)

    def test_a_failed_host_is_tried_again_after_retry_after(self):
        self.release.set()
        with self.open_auth(libvirt.libvirtError("refused")):
            self.get()
            self.get()
            self.assertEqual(self.calls, 1)
            time.sleep(1.1)
            self.get()
        self.assertEqual(self.calls, 2)

    def test_concurrent_requests_share_one_open(self):
        with self.open_auth(alive_connection()):
            threads = [threading.Thread(target=self.get) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(self.calls, 1)

    def test_a_stuck_host_does_not_keep_other_hosts_from_connecting(self):
        healthy = alive_connection()

        def open_auth(uri, auth, flags):
            if "stuck" in uri:
                self.release.wait(5)  # an open that never returns
            return healthy

        with patch("vrtManager.connection.libvirt.openAuth", side_effect=open_auth):
            for i in range(12):
                self.get(f"stuck{i}")
            result, took = self.get("healthy")
        self.assertIs(result, healthy)

    def test_an_abandoned_open_is_not_waited_for_again_nor_duplicated(self):
        conn = alive_connection()
        with self.open_auth(conn):
            self.get()
            time.sleep(1.1)  # past RETRY_AFTER, the open still runs
            result, took = self.get()
            self.assertIsInstance(result, libvirt.libvirtError)
            self.assertLess(took, 0.1)
            self.release.set()  # the host answers at last
            time.sleep(0.1)
            result, _ = self.get()
        self.assertIs(result, conn)
        self.assertEqual(self.calls, 1)

    def test_a_late_close_callback_does_not_drop_the_new_connection(self):
        self.release.set()
        old, new = alive_connection(), alive_connection()
        with self.open_auth(old):
            self.get()
        entry = self.manager._search_connection("h", "u", "p", CONN_TCP)
        old.isAlive.return_value = 0  # seen dead, reconnected
        with self.open_auth(new):
            self.assertIs(self.get()[0], new)
        entry._wvmConnection__connection_close_callback(old, 1)
        self.assertIs(self.get()[0], new)
        entry._wvmConnection__connection_close_callback(new, 2)
        self.assertIsNone(entry.connection)
        self.assertEqual(entry.last_error, "Connection closed (keepalive timeout)")

    def test_a_connection_closed_before_it_is_published_is_an_error(self):
        self.release.set()
        dead = alive_connection()
        dead.isAlive.return_value = 0
        with self.open_auth(dead):
            result, _ = self.get()
        self.assertIn("closed right after opening", str(result))

    def test_ssh_runs_ssh_with_a_connect_timeout(self):
        self.release.set()
        with patch("vrtManager.connection.libvirt.open", return_value=alive_connection()) as libvirt_open:
            self.manager.get_connection("host", "root", "", CONN_SSH)
        uri = libvirt_open.call_args[0][0]
        self.assertTrue(uri.startswith("qemu+ssh://root@host/system?command="))
        self.assertTrue(os.access(SSH_COMMAND, os.X_OK))
        with open(SSH_COMMAND) as script:
            self.assertIn("ConnectTimeout", script.read())


class EventLoopTestCase(unittest.TestCase):
    """The libvirt event loop thread starts with the first connection of a
    process, not at import: novncd forks a child per console after importing
    this module, and libvirt is not safe to use after a fork of a process
    with running libvirt threads. A child starts its own loop, so its
    keepalive works."""

    def setUp(self):
        for name in ("wvmEventLoop", "libvirt.virEventRegisterDefaultImpl", "libvirt.openAuth"):
            patcher = patch(f"vrtManager.connection.{name}")
            setattr(self, name.split(".")[-1], patcher.start())
            self.addCleanup(patcher.stop)
        self.openAuth.side_effect = lambda *args: alive_connection()
        state = patch.dict(connection._event_loop, {"lock": threading.Lock(), "running": False, "registered": False})
        state.start()
        self.addCleanup(state.stop)

    def test_no_thread_until_the_first_connection(self):
        manager = wvmConnectionManager()
        self.wvmEventLoop.assert_not_called()
        self.virEventRegisterDefaultImpl.assert_not_called()
        manager.get_connection("h", "u", "p", CONN_TCP)
        wvmConnectionManager().get_connection("h2", "u", "p", CONN_TCP)  # one loop per process
        self.wvmEventLoop.return_value.start.assert_called_once()
        self.virEventRegisterDefaultImpl.assert_called_once()

    def test_a_forked_child_starts_its_own_loop_with_its_own_connections(self):
        manager = connection.connection_manager
        parent_conn = manager.get_connection("fork-host", "u", "p", CONN_TCP)
        read_end, write_end = os.pipe()
        pid = os.fork()
        if pid == 0:  # child: report and leave without running the test runner
            try:
                child_conn = manager.get_connection("fork-host", "u", "p", CONN_TCP)
                kept = any(
                    entry.connection is parent_conn
                    for connections in manager._inherited
                    for entries in connections.values()
                    for entry in entries
                )
                report = (
                    f"{self.wvmEventLoop.return_value.start.call_count} "
                    f"{self.virEventRegisterDefaultImpl.call_count} {child_conn is parent_conn} {kept}"
                )
            except BaseException as e:
                report = repr(e)
            os.write(write_end, report.encode())
            os._exit(0)
        os.close(write_end)
        os.waitpid(pid, 0)
        report = os.read(read_end, 200).decode()
        os.close(read_end)
        # a second loop started in the child, on the inherited registration;
        # the child opened its own connection instead of the parent's socket,
        # and kept the parent's (releasing it would run libvirt destructors)
        self.assertEqual(report, "2 1 False True")


if __name__ == "__main__":
    unittest.main()
