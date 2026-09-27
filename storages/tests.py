import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from computes.models import Compute
from storages.views import handle_uploaded_file
from vrtManager.connection import CONN_SOCKET, CONN_SSH

User = get_user_model()


class StorageUploadSecurityTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.mock_socket_conn = MagicMock()
        self.mock_socket_conn.conn = CONN_SOCKET

        self.mock_ssh_conn = MagicMock()
        self.mock_ssh_conn.conn = CONN_SSH
        self.mock_ssh_conn.host = "127.0.0.1"
        self.mock_ssh_conn.login = "test"
        self.mock_ssh_conn.passwd = "test"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_handle_uploaded_file_invalid_names(self):
        dummy_chunk = SimpleUploadedFile("test.iso", b"data")
        invalid_names = ["", "   ", ".", "..", "test\x00evil.iso", "../evil.iso", "../../etc/shadow"]

        for bad_name in invalid_names:
            clean_base = os.path.basename(bad_name).strip()
            # bad_name either gets rejected as empty/dot/null or has basename extracted
            if not clean_base or clean_base in (".", "..") or "\x00" in clean_base:
                with self.assertRaises(ValueError):
                    handle_uploaded_file(self.mock_socket_conn, self.temp_dir, bad_name, dummy_chunk, is_last_chunk=True)

    def test_handle_uploaded_file_successful_socket_upload(self):
        dummy_chunk = SimpleUploadedFile("install.iso", b"ISODATA_BLOCK_123")
        file_name = "install.iso"

        # Chunk 1 (is_last_chunk=False)
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, dummy_chunk, is_last_chunk=False)
        part_path = os.path.join(self.temp_dir, f"{file_name}.part")
        final_path = os.path.join(self.temp_dir, file_name)
        self.assertTrue(os.path.exists(part_path))
        self.assertFalse(os.path.exists(final_path))

        # Chunk 2 (is_last_chunk=True)
        dummy_chunk2 = SimpleUploadedFile("install.iso", b"_PART2")
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, dummy_chunk2, is_last_chunk=True)
        self.assertFalse(os.path.exists(part_path))
        self.assertTrue(os.path.exists(final_path))

        with open(final_path, "rb") as f:
            self.assertEqual(f.read(), b"ISODATA_BLOCK_123_PART2")

    def test_handle_uploaded_file_jail_containment(self):
        dummy_chunk = SimpleUploadedFile("test.iso", b"data")
        # Attempt traversal outside sandbox
        with patch("os.path.basename", side_effect=lambda x: x):  # simulate raw traversal name
            with self.assertRaises(PermissionError):
                handle_uploaded_file(self.mock_socket_conn, self.temp_dir, "../../../evil.iso", dummy_chunk, is_last_chunk=True)

    def test_handle_uploaded_file_ssh_traversal_check(self):
        dummy_chunk = SimpleUploadedFile("test.iso", b"data")
        with patch("os.path.basename", side_effect=lambda x: x):
            with self.assertRaises(PermissionError):
                handle_uploaded_file(self.mock_ssh_conn, "/var/lib/libvirt/images", "../../../evil.iso", dummy_chunk, is_last_chunk=True)


class StorageUploadViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="storage_admin", password="password", email="storage_admin@example.com")
        self.client.login(username="storage_admin", password="password")
        self.compute = Compute.objects.create(name="LocalCompute", hostname="localhost", type=1, login="u", password="p")

    @patch("storages.views.wvmStorage")
    def test_iso_upload_missing_chunk(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_status.return_value = 1
        mock_conn.get_target_path.return_value = "/tmp"
        mock_conn.get_type.return_value = "dir"
        mock_conn.get_autostart.return_value = 1
        mock_conn.get_size.return_value = [1000, 500]
        mock_conn.update_volumes.return_value = []
        mock_wvm.return_value = mock_conn

        url = reverse("storage", args=[self.compute.id, "default"])
        res = self.client.post(url, {"iso_upload": "1", "file_name": "test.iso"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("No file chunk was submitted", res.json().get("error", ""))

    @patch("storages.views.wvmStorage")
    def test_iso_upload_invalid_filename(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.get_status.return_value = 1
        mock_conn.get_target_path.return_value = "/tmp"
        mock_conn.get_type.return_value = "dir"
        mock_conn.get_autostart.return_value = 1
        mock_conn.get_size.return_value = [1000, 500]
        mock_conn.update_volumes.return_value = []
        mock_wvm.return_value = mock_conn

        url = reverse("storage", args=[self.compute.id, "default"])
        chunk = SimpleUploadedFile("test.iso", b"chunkdata")
        res = self.client.post(url, {
            "iso_upload": "1",
            "file": chunk,
            "file_name": "..\x00..",
            "chunk_index": "0",
            "total_chunks": "1",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid file name", res.json().get("error", ""))
