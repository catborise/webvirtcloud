"""Reachability probe and connections that do not block each other."""

import socket
import threading
import time
import unittest
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(MAC_OUI="52:54:10")

from vrtManager.connection import CONN_TCP, wvmConnectionManager


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


if __name__ == "__main__":
    unittest.main()
