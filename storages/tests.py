import os
import shutil
import tempfile
import time
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from computes.models import Compute
from storages.upload import (
    handle_uploaded_file,
    _acquire_upload_lock,
    _atomic_finalize_ssh,
    _cleanup_stale_local_uploads,
    _get_upload_lock_directory,
    _is_session_completed,
    _sftp_replace_file,
)
from vrtManager.connection import CONN_SOCKET, CONN_SSH, CONN_TCP, CONN_TLS

User = get_user_model()


class LibvirtUploadTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.lock_patch = patch("storages.upload._get_upload_lock_directory", return_value=self.temp_dir)
        self.lock_patch.start()
        self.conn = MagicMock()
        self.conn.conn = CONN_TCP
        self.volumes = {}
        self.conn.get_volumes.side_effect = lambda: list(self.volumes)
        self.conn.get_volume.side_effect = lambda name: self.volumes[name]

        def create_volume(xml, flags):
            from xml.etree import ElementTree
            root = ElementTree.fromstring(xml)
            name = root.findtext("name")
            self.assertNotIn(name, self.volumes)
            self.assertEqual(root.find("capacity").get("unit"), "bytes")
            self.assertEqual(root.find("target/format").get("type"), "raw")
            self.volumes[name] = MagicMock()

        self.conn.pool.createXML.side_effect = create_volume
        self.conn.wvm.newStream.return_value.send.side_effect = lambda data: len(data)

    def tearDown(self):
        self.lock_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def upload(self, data, index=0, total=1, upload_id="tcp-session", file_size=None):
        return handle_uploaded_file(
            self.conn, "/var/lib/libvirt/images", "ubuntu.iso",
            SimpleUploadedFile("ubuntu.iso", data), index == total - 1,
            chunk_index=index, total_chunks=total, upload_id=upload_id,
            user_id=1, compute_id=2, pool="default", file_size=file_size,
        )

    def test_tcp_upload_retries_and_stream_offsets(self):
        self.upload(b"abc", index=0, total=2, file_size=5)
        self.upload(b"abc", index=0, total=2, file_size=5)
        self.upload(b"de", index=1, total=2, file_size=5)
        self.upload(b"de", index=1, total=2, file_size=5)
        self.conn.pool.createXML.assert_called_once()
        self.assertEqual(self.volumes["ubuntu.iso"].upload.call_count, 2)
        self.assertEqual(self.volumes["ubuntu.iso"].upload.call_args.args[1:], (3, 2, 0))
        self.assertEqual(self.conn.wvm.newStream.return_value.finish.call_count, 2)

    def test_tls_uses_libvirt_stream(self):
        self.conn.conn = CONN_TLS
        self.upload(b"iso", file_size=3)
        self.volumes["ubuntu.iso"].upload.assert_called_once()

    def test_existing_volume_is_preserved(self):
        self.volumes["ubuntu.iso"] = MagicMock()
        with self.assertRaises(FileExistsError):
            self.upload(b"iso", file_size=3)
        self.conn.pool.createXML.assert_not_called()

    def test_size_and_sequence_are_validated(self):
        with self.assertRaises(ValueError):
            self.upload(b"iso", file_size=None)
        with self.assertRaises(ValueError):
            self.upload(b"iso", file_size=4)
        self.conn.pool.createXML.assert_not_called()
        self.upload(b"ab", index=0, total=2, file_size=4)
        with self.assertRaises(ValueError):
            self.upload(b"cd", index=1, total=2, file_size=5)
        with self.assertRaises(ValueError):
            self.upload(b"cd", index=1, total=3, file_size=4)

    def test_stream_failure_aborts_and_can_retry(self):
        stream = self.conn.wvm.newStream.return_value
        stream.send.side_effect = [1, OSError("connection lost")]
        with self.assertRaises(OSError):
            self.upload(b"iso", file_size=3)
        stream.abort.assert_called_once()
        self.assertEqual(stream.finish.call_count, 0)
        stream.send.side_effect = lambda data: len(data)
        self.upload(b"iso", file_size=3)
        self.conn.pool.createXML.assert_called_once()




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

    def test_handle_uploaded_file_missing_or_invalid_upload_id(self):
        dummy_chunk = SimpleUploadedFile("test.iso", b"data")
        # Missing upload_id
        with self.assertRaises(ValueError) as ctx:
            handle_uploaded_file(self.mock_socket_conn, self.temp_dir, "test.iso", dummy_chunk, is_last_chunk=True)
        self.assertIn("Missing required upload_id", str(ctx.exception))

        # Invalid upload_id format
        for bad_id in ["", "   ", "sess/../id", "id with spaces", "a" * 65]:
            with self.assertRaises(ValueError):
                handle_uploaded_file(
                    self.mock_socket_conn,
                    self.temp_dir,
                    "test.iso",
                    dummy_chunk,
                    is_last_chunk=True,
                    upload_id=bad_id,
                )

    def test_handle_uploaded_file_invalid_names(self):
        dummy_chunk = SimpleUploadedFile("test.iso", b"data")
        invalid_names = ["", "   ", ".", "..", "test\x00evil.iso", "../evil.iso", "../../etc/shadow"]

        for bad_name in invalid_names:
            clean_base = os.path.basename(bad_name).strip()
            if not clean_base or clean_base in (".", "..") or "\x00" in clean_base:
                with self.assertRaises(ValueError):
                    handle_uploaded_file(
                        self.mock_socket_conn,
                        self.temp_dir,
                        bad_name,
                        dummy_chunk,
                        is_last_chunk=True,
                        upload_id="sess-valid-1",
                    )

    def test_only_iso_extension_is_accepted(self):
        chunk = SimpleUploadedFile("image.img", b"not checked")
        for conn in (self.mock_socket_conn, self.mock_ssh_conn):
            with self.assertRaisesRegex(ValueError, r"Only \.iso files"):
                handle_uploaded_file(
                    conn, self.temp_dir, "image.img", chunk,
                    is_last_chunk=True, upload_id="iso-extension-test",
                )
        handle_uploaded_file(
            self.mock_socket_conn, self.temp_dir, "image.ISO",
            SimpleUploadedFile("image.ISO", b"contents are not inspected"),
            is_last_chunk=True, upload_id="uppercase-iso-test",
        )
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "image.ISO")))

    def test_handle_uploaded_file_successful_socket_upload(self):
        dummy_chunk = SimpleUploadedFile("install.iso", b"ISODATA_BLOCK_123")
        file_name = "install.iso"
        upload_id = "sess-install-1"

        # Chunk 1 (is_last_chunk=False, chunk_index=0, total_chunks=2)
        handle_uploaded_file(
            self.mock_socket_conn,
            self.temp_dir,
            file_name,
            dummy_chunk,
            is_last_chunk=False,
            chunk_index=0,
            total_chunks=2,
            upload_id=upload_id,
        )
        part_path = os.path.join(self.temp_dir, f"{file_name}.wvc_upload_{upload_id}.part")
        final_path = os.path.join(self.temp_dir, file_name)
        self.assertTrue(os.path.exists(part_path))
        self.assertFalse(os.path.exists(final_path))

        # Chunk 2 (is_last_chunk=True, chunk_index=1, total_chunks=2)
        dummy_chunk2 = SimpleUploadedFile("install.iso", b"_PART2")
        handle_uploaded_file(
            self.mock_socket_conn,
            self.temp_dir,
            file_name,
            dummy_chunk2,
            is_last_chunk=True,
            chunk_index=1,
            total_chunks=2,
            upload_id=upload_id,
        )
        self.assertFalse(os.path.exists(part_path))
        self.assertTrue(os.path.exists(final_path))

        with open(final_path, "rb") as f:
            self.assertEqual(f.read(), b"ISODATA_BLOCK_123_PART2")

    def test_handle_uploaded_file_idempotent_retry(self):
        file_name = "retry_test.iso"
        upload_id = "sess-retry-123"
        c0 = SimpleUploadedFile(file_name, b"CHUNK0_")
        c1 = SimpleUploadedFile(file_name, b"CHUNK1_")
        c2 = SimpleUploadedFile(file_name, b"CHUNK2")

        # Chunk 0 initial
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c0, is_last_chunk=False, chunk_index=0, total_chunks=3, upload_id=upload_id)
        # Chunk 0 retry (idempotent no-op!)
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c0, is_last_chunk=False, chunk_index=0, total_chunks=3, upload_id=upload_id)

        # Chunk 1 initial
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c1, is_last_chunk=False, chunk_index=1, total_chunks=3, upload_id=upload_id)
        # Chunk 1 retry (idempotent no-op!)
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c1, is_last_chunk=False, chunk_index=1, total_chunks=3, upload_id=upload_id)

        # Chunk 2 initial (final chunk)
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c2, is_last_chunk=True, chunk_index=2, total_chunks=3, upload_id=upload_id)

        # Chunk 2 retry (idempotent no-op, file already finalized!)
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c2, is_last_chunk=True, chunk_index=2, total_chunks=3, upload_id=upload_id)

        final_file = os.path.join(self.temp_dir, file_name)
        self.assertTrue(os.path.exists(final_file))
        with open(final_file, "rb") as f:
            self.assertEqual(f.read(), b"CHUNK0_CHUNK1_CHUNK2")

    def test_handle_uploaded_file_interrupted_finalization(self):
        file_name = "interrupted.iso"
        upload_id = "sess-interrupted-1"
        c0 = SimpleUploadedFile(file_name, b"PART1")
        c1 = SimpleUploadedFile(file_name, b"PART2")

        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c0, is_last_chunk=False, chunk_index=0, total_chunks=2, upload_id=upload_id)

        # Simulate state where chunk 1 was recorded in .meta and .part, but finalization was interrupted
        meta_file = os.path.join(self.temp_dir, f"{file_name}.wvc_upload_{upload_id}.meta")
        part_file = os.path.join(self.temp_dir, f"{file_name}.wvc_upload_{upload_id}.part")
        with open(part_file, "ab") as f:
            f.write(b"PART2")
        import json
        with open(meta_file, "w") as f:
            json.dump({
                "file_name": file_name,
                "total_chunks": 2,
                "expected_chunk": 2,
                "current_size": 10,
                "created_at": time.time(),
                "updated_at": time.time()
            }, f)

        # Client retries final chunk (chunk 1)
        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c1, is_last_chunk=True, chunk_index=1, total_chunks=2, upload_id=upload_id)

        final_file = os.path.join(self.temp_dir, file_name)
        self.assertTrue(os.path.exists(final_file))
        with open(final_file, "rb") as f:
            self.assertEqual(f.read(), b"PART1PART2")


    def test_handle_uploaded_file_inconsistent_total_chunks(self):
        file_name = "inconsistent.iso"
        upload_id = "sess-inc-1"
        c0 = SimpleUploadedFile(file_name, b"part0")
        c1 = SimpleUploadedFile(file_name, b"part1")

        handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c0, is_last_chunk=False, chunk_index=0, total_chunks=3, upload_id=upload_id)

        with self.assertRaises(ValueError) as ctx:
            handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, c1, is_last_chunk=False, chunk_index=1, total_chunks=4, upload_id=upload_id)
        self.assertIn("Inconsistent total_chunks", str(ctx.exception))

    def test_handle_uploaded_file_missing_previous_chunk(self):
        dummy_chunk = SimpleUploadedFile("broken.iso", b"data")
        with self.assertRaises(ValueError):
            handle_uploaded_file(self.mock_socket_conn, self.temp_dir, "broken.iso", dummy_chunk, is_last_chunk=False, chunk_index=1, total_chunks=2, upload_id="sess-broken-1")

    def test_handle_uploaded_file_destination_exists_raises(self):
        file_name = "existing.iso"
        final_path = os.path.join(self.temp_dir, file_name)
        with open(final_path, "wb") as f:
            f.write(b"already exists")

        dummy_chunk = SimpleUploadedFile(file_name, b"new data")
        with self.assertRaises(Exception):
            handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, dummy_chunk, is_last_chunk=False, chunk_index=0, total_chunks=2, upload_id="sess-exist-1")

    def test_handle_uploaded_file_symlink_defense(self):
        file_name = "symlink_test.iso"
        upload_id = "sess-symlink-1"
        target_file = os.path.join(self.temp_dir, "real.iso")
        with open(target_file, "wb") as f:
            f.write(b"real content")

        symlink_part = os.path.join(self.temp_dir, f"{file_name}.wvc_upload_{upload_id}.part")
        os.symlink(target_file, symlink_part)

        dummy_chunk = SimpleUploadedFile(file_name, b"malicious chunk")
        with self.assertRaises(PermissionError):
            handle_uploaded_file(self.mock_socket_conn, self.temp_dir, file_name, dummy_chunk, is_last_chunk=False, chunk_index=0, total_chunks=2, upload_id=upload_id)

    def test_handle_uploaded_file_jail_containment(self):
        dummy_chunk = SimpleUploadedFile("test.iso", b"data")
        with patch("os.path.basename", side_effect=lambda x: x):
            with self.assertRaises(PermissionError):
                handle_uploaded_file(self.mock_socket_conn, self.temp_dir, "../../../evil.iso", dummy_chunk, is_last_chunk=True, upload_id="sess-jail-1")

    def test_cleanup_stale_local_uploads_strict_regex(self):
        unrelated_files = [
            "unrelated.part",
            "important.lock",
            "data.meta",
            "short.123.part",
            "backup.abcdefgh.meta",
            "disk.12345678.part",
        ]
        for fname in unrelated_files:
            p = os.path.join(self.temp_dir, fname)
            with open(p, "wb") as f:
                f.write(b"keep me")
            old_time = time.time() - 172800
            os.utime(p, (old_time, old_time))

        stale_part = os.path.join(self.temp_dir, "ubuntu.iso.wvc_upload_sess-12345678.part")
        stale_meta = os.path.join(self.temp_dir, "ubuntu.iso.wvc_upload_sess-12345678.meta")
        fresh_part = os.path.join(self.temp_dir, "ubuntu.iso.wvc_upload_sess-87654321.part")

        with open(stale_part, "wb") as f:
            f.write(b"stale part")
        import json
        with open(stale_meta, "w") as f:
            json.dump({"upload_id": "sess-12345678", "expected_chunk": 1}, f)
        with open(fresh_part, "wb") as f:
            f.write(b"fresh part")

        old_time = time.time() - 172800
        os.utime(stale_part, (old_time, old_time))
        os.utime(stale_meta, (old_time, old_time))

        _cleanup_stale_local_uploads(self.temp_dir, max_age_seconds=86400)

        for fname in unrelated_files:
            self.assertTrue(os.path.exists(os.path.join(self.temp_dir, fname)), f"{fname} was unexpectedly deleted")
        self.assertTrue(os.path.exists(fresh_part))

        self.assertFalse(os.path.exists(stale_part))
        self.assertFalse(os.path.exists(stale_meta))

    def test_sftp_replace_file_requires_atomic_replace(self):
        mock_sftp = MagicMock()
        mock_sftp.posix_rename.return_value = None
        _sftp_replace_file(mock_sftp, "/tmp/src", "/tmp/dst")
        mock_sftp.posix_rename.assert_called_once_with("/tmp/src", "/tmp/dst")
        mock_sftp.rename.assert_not_called()

        mock_sftp.reset_mock()
        mock_sftp.posix_rename.side_effect = IOError("posix_rename unsupported")
        with self.assertRaises(OSError):
            _sftp_replace_file(mock_sftp, "/tmp/src", "/tmp/dst")
        mock_sftp.posix_rename.assert_called_once_with("/tmp/src", "/tmp/dst")
        mock_sftp.remove.assert_not_called()
        mock_sftp.rename.assert_not_called()

        # posix_rename with other error must NOT fallback to remove
        mock_sftp.reset_mock()
        mock_sftp.posix_rename.side_effect = IOError("permission denied")
        with self.assertRaises(IOError):
            _sftp_replace_file(mock_sftp, "/tmp/src", "/tmp/dst")
        mock_sftp.remove.assert_not_called()

        mock_sftp = MagicMock(spec=["remove", "rename"])
        with self.assertRaises(OSError):
            _sftp_replace_file(mock_sftp, "/tmp/src", "/tmp/dst")
        mock_sftp.remove.assert_not_called()
        mock_sftp.rename.assert_not_called()

        mock_ssh = MagicMock()
        stdout = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        mock_ssh.exec_command.return_value = (MagicMock(), stdout, MagicMock())
        _sftp_replace_file(mock_sftp, "/tmp/src", "/tmp/dst", ssh=mock_ssh)
        mock_sftp.remove.assert_not_called()

    @patch("storages.upload.time.sleep")
    @patch("storages.upload.fcntl.flock", side_effect=BlockingIOError)
    @patch("storages.upload.LOCK_TIMEOUT_SECONDS", 0)
    def test_upload_lock_times_out(self, _flock, _sleep):
        with self.assertRaises(TimeoutError):
            _acquire_upload_lock(123)

    def test_atomic_finalize_ssh_success_and_overwrite_prevention(self):
        mock_sftp = MagicMock()

        _atomic_finalize_ssh(mock_sftp, "/tmp/temp.part", "/tmp/final.iso")
        mock_sftp.rename.assert_called_once_with("/tmp/temp.part", "/tmp/final.iso")

        # A destination created concurrently remains untouched.
        mock_sftp.rename.side_effect = IOError("File exists")
        mock_sftp.reset_mock()
        with self.assertRaises(FileExistsError):
            _atomic_finalize_ssh(mock_sftp, "/tmp/temp.part", "/tmp/final.iso")
        mock_sftp.remove.assert_not_called()

    def test_is_session_completed_crash_recovery(self):
        upload_id = "sess-crash-rec-1"
        file_name = "recovered.iso"
        final_file = os.path.join(self.temp_dir, file_name)
        with open(final_file, "wb") as f:
            f.write(b"COMPLETE_CONTENT_12345")
        file_size = os.path.getsize(final_file)

        meta_file = os.path.join(self.temp_dir, f"{file_name}.wvc_upload_{upload_id}.meta")
        import json
        with open(meta_file, "w") as mf:
            json.dump({
                "file_name": file_name,
                "expected_chunk": 3,
                "total_chunks": 3,
                "current_size": file_size,
                "created_at": time.time(),
                "updated_at": time.time(),
            }, mf)

        # No .done marker exists yet; _is_session_completed should recover the session from .meta
        lock_dir = _get_upload_lock_directory()
        marker_path = os.path.join(lock_dir, f"upload_{upload_id}.done")
        if os.path.exists(marker_path):
            os.unlink(marker_path)

        recovered = _is_session_completed(upload_id, file_name, path=self.temp_dir)
        self.assertTrue(recovered)
        # .meta should now be cleaned up and .done marker created
        self.assertFalse(os.path.exists(meta_file))
        self.assertTrue(os.path.exists(marker_path))
        if os.path.exists(marker_path):
            os.unlink(marker_path)

    def test_lock_file_not_unlinked_on_completion(self):
        upload_id = "sess-lock-persist-1"
        file_name = "lock_persist.iso"
        dummy_chunk = SimpleUploadedFile(file_name, b"data")
        handle_uploaded_file(
            self.mock_socket_conn,
            self.temp_dir,
            file_name,
            dummy_chunk,
            is_last_chunk=True,
            chunk_index=0,
            total_chunks=1,
            upload_id=upload_id,
        )
        lock_dir = _get_upload_lock_directory()
        lock_path = os.path.join(lock_dir, f"upload_session_{upload_id}.lock")
        self.assertTrue(os.path.exists(lock_path))

    def test_destination_exists_requires_completed_session(self):
        file_name = "already_here.iso"
        dest_path = os.path.join(self.temp_dir, file_name)
        with open(dest_path, "wb") as f:
            f.write(b"existing content")

        dummy_chunk = SimpleUploadedFile(file_name, b"new chunk")
        with self.assertRaises(FileExistsError):
            handle_uploaded_file(
                self.mock_socket_conn,
                self.temp_dir,
                file_name,
                dummy_chunk,
                is_last_chunk=True,
                chunk_index=0,
                total_chunks=1,
                upload_id="sess-rogue-123",
            )

    @patch("paramiko.SSHClient")
    def test_sftp_upload_multi_chunk_and_retry(self, mock_ssh_cls):
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value = mock_ssh
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp

        remote_files = {}

        def mock_lstat(path):
            if path in remote_files:
                stat_obj = MagicMock()
                stat_obj.st_mode = 0o100644
                return stat_obj
            raise FileNotFoundError(f"{path} not found")

        mock_sftp.lstat.side_effect = mock_lstat

        class FakeRemoteFile:
            def __init__(self, path, mode):
                self.path = path
                self.mode = mode
                if "w" in mode or path not in remote_files:
                    remote_files[path] = b""
                self.buf = bytearray(remote_files[path])
                self.pos = 0

            def write(self, data):
                if isinstance(data, str):
                    data = data.encode("utf-8")
                self.buf[self.pos:self.pos + len(data)] = data
                self.pos += len(data)
                remote_files[self.path] = bytes(self.buf)

            def read(self):
                return bytes(self.buf)

            def seek(self, pos):
                self.pos = pos

            def truncate(self, size):
                del self.buf[size:]
                remote_files[self.path] = bytes(self.buf)

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        mock_sftp.open.side_effect = lambda p, m: FakeRemoteFile(p, m)

        def mock_rename(src, dst):
            if src not in remote_files:
                raise FileNotFoundError(f"{src} not found")
            remote_files[dst] = remote_files.pop(src)

        mock_sftp.rename.side_effect = mock_rename
        mock_sftp.posix_rename.side_effect = mock_rename
        mock_sftp.remove.side_effect = lambda p: remote_files.pop(p, None)

        def mock_exec_command(command, **kwargs):
            if "os.replace(" in command:
                import shlex
                src, dst = shlex.split(command)[-2:]
                mock_rename(src, dst)
            if "os.link(" in command:
                import shlex
                src, dst = shlex.split(command)[-2:]
                if dst in remote_files:
                    raise FileExistsError(dst)
                remote_files[dst] = remote_files[src]
            stdout = MagicMock()
            stdout.channel.recv_exit_status.return_value = 0
            return MagicMock(), stdout, MagicMock()

        mock_ssh.exec_command.side_effect = mock_exec_command

        upload_id = "sess-sftp-multi-1"
        file_name = "debian.iso"
        c0 = SimpleUploadedFile(file_name, b"DEBIAN_CHUNK0_")
        c1 = SimpleUploadedFile(file_name, b"DEBIAN_CHUNK1")

        handle_uploaded_file(
            self.mock_ssh_conn,
            "/var/lib/libvirt/images",
            file_name,
            c0,
            is_last_chunk=False,
            chunk_index=0,
            total_chunks=2,
            upload_id=upload_id,
        )
        self.assertEqual(mock_ssh.connect.call_args.kwargs["timeout"], 10)
        mock_sftp.get_channel.return_value.settimeout.assert_called_with(30)

        # Simulate bytes written before a worker crashed without advancing metadata.
        part_remote = f"/var/lib/libvirt/images/{file_name}.wvc_upload_{upload_id}.part"
        remote_files[part_remote] += b"UNCOMMITTED"

        handle_uploaded_file(
            self.mock_ssh_conn,
            "/var/lib/libvirt/images",
            file_name,
            c1,
            is_last_chunk=True,
            chunk_index=1,
            total_chunks=2,
            upload_id=upload_id,
        )

        final_remote = "/var/lib/libvirt/images/debian.iso"
        self.assertIn(final_remote, remote_files)
        self.assertEqual(remote_files[final_remote], b"DEBIAN_CHUNK0_DEBIAN_CHUNK1")

        # Retry Chunk 1
        handle_uploaded_file(
            self.mock_ssh_conn,
            "/var/lib/libvirt/images",
            file_name,
            c1,
            is_last_chunk=True,
            chunk_index=1,
            total_chunks=2,
            upload_id=upload_id,
        )
        self.assertEqual(remote_files[final_remote], b"DEBIAN_CHUNK0_DEBIAN_CHUNK1")

    def test_handle_uploaded_file_ssh_traversal_check(self):
        dummy_chunk = SimpleUploadedFile("test.iso", b"data")
        with patch("os.path.basename", side_effect=lambda x: x):
            with self.assertRaises(PermissionError):
                handle_uploaded_file(self.mock_ssh_conn, "/var/lib/libvirt/images", "../../../evil.iso", dummy_chunk, is_last_chunk=True, upload_id="sess-ssh-1")


class StorageUploadViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="storage_admin", password="password", email="storage_admin@example.com")
        self.client.login(username="storage_admin", password="password")
        self.compute = Compute.objects.create(name="LocalCompute", hostname="localhost", type=1, login="u", password="p")

    @patch("storages.views.wvmStorage")
    def test_iso_upload_rejects_other_extensions(self, mock_wvm):
        mock_conn = mock_wvm.return_value
        mock_conn.conn = CONN_SOCKET
        mock_conn.get_target_path.return_value = "/tmp"
        url = reverse("storage", args=[self.compute.id, "default"])
        response = self.client.post(url, {
            "iso_upload": "1", "file": SimpleUploadedFile("notes.txt", b"notes"),
            "file_name": "notes.txt", "chunk_index": "0", "total_chunks": "1",
            "upload_id": "invalid-extension-test",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only .iso files", response.json()["error"])

    @patch("storages.views.handle_uploaded_file")
    @patch("storages.views.wvmStorage")
    def test_tcp_upload_passes_declared_size(self, mock_wvm, mock_upload):
        mock_conn = mock_wvm.return_value
        mock_conn.conn = CONN_TCP
        mock_conn.get_target_path.return_value = "/var/lib/libvirt/images"
        url = reverse("storage", args=[self.compute.id, "default"])
        payload = {
            "iso_upload": "1", "file_name": "test.iso", "chunk_index": "0",
            "total_chunks": "1", "upload_id": "tcp-view-test",
        }
        response = self.client.post(url, {
            **payload, "file": SimpleUploadedFile("test.iso", b"iso"),
        })
        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()

        response = self.client.post(url, {
            **payload, "file": SimpleUploadedFile("test.iso", b"iso"),
            "file_size": "3",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_upload.call_args.kwargs["file_size"], 3)

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
        res = self.client.post(url, {"iso_upload": "1", "file_name": "test.iso", "upload_id": "sess-view-1"})
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
            "upload_id": "sess-view-2",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid file name", res.json().get("error", ""))

    @patch("storages.views.wvmStorage")
    def test_iso_upload_missing_upload_id(self, mock_wvm):
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
            "file_name": "test.iso",
            "chunk_index": "0",
            "total_chunks": "1",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("Missing upload_id", res.json().get("error", ""))

    @patch("storages.views.wvmStorage")
    def test_iso_upload_final_chunk_retry_idempotency(self, mock_wvm):
        mock_conn = MagicMock()
        mock_conn.conn = CONN_SOCKET
        mock_conn.get_status.return_value = 1
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        mock_conn.get_target_path.return_value = temp_dir
        mock_conn.get_type.return_value = "dir"
        mock_conn.get_autostart.return_value = 1
        mock_conn.get_size.return_value = [1000, 500]
        mock_conn.get_volumes.return_value = []
        mock_conn.update_volumes.return_value = []
        mock_wvm.return_value = mock_conn

        url = reverse("storage", args=[self.compute.id, "default"])
        upload_id = "sess-view-retry-123"
        file_name = "test_retry.iso"
        chunk = SimpleUploadedFile(file_name, b"test payload")

        # 1. First upload (single chunk, total_chunks=1)
        res1 = self.client.post(url, {
            "iso_upload": "1",
            "file": chunk,
            "file_name": file_name,
            "chunk_index": "0",
            "total_chunks": "1",
            "upload_id": upload_id,
        })
        self.assertEqual(res1.status_code, 200)
        self.assertTrue(res1.json().get("success"))

        # Volume now appears in libvirt pool
        mock_conn.get_volumes.return_value = [file_name]

        # 2. Retrying final chunk with same upload_id returns 200 idempotent success
        chunk_retry = SimpleUploadedFile(file_name, b"test payload")
        res2 = self.client.post(url, {
            "iso_upload": "1",
            "file": chunk_retry,
            "file_name": file_name,
            "chunk_index": "0",
            "total_chunks": "1",
            "upload_id": upload_id,
        })
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json().get("success"))
        mock_conn.get_storages.assert_not_called()
        mock_conn.update_volumes.assert_not_called()
        mock_conn.refresh.assert_not_called()
        self.assertTrue(res2.json().get("reload"))

        # 3. Upload with different upload_id returns 400 error
        chunk_attacker = SimpleUploadedFile(file_name, b"evil payload")
        res3 = self.client.post(url, {
            "iso_upload": "1",
            "file": chunk_attacker,
            "file_name": file_name,
            "chunk_index": "0",
            "total_chunks": "1",
            "upload_id": "sess-attacker-999",
        })
        self.assertEqual(res3.status_code, 400)
        self.assertIn("ISO image already exists", res3.json().get("error", ""))

        # 4. Upload with same upload_id but by a different superuser cannot bypass/claim
        User.objects.create_superuser(username="other_admin", password="password", email="other@example.com")
        self.client.login(username="other_admin", password="password")
        res4 = self.client.post(url, {
            "iso_upload": "1",
            "file": chunk_retry,
            "file_name": file_name,
            "chunk_index": "0",
            "total_chunks": "1",
            "upload_id": upload_id,
        })
        self.assertEqual(res4.status_code, 400)
        self.assertIn("ISO image already exists", res4.json().get("error", ""))
