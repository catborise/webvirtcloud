"""Chunked ISO upload operations shared by the storage view."""

import fcntl
import json
import logging
import os
import posixpath
import re
import stat
import tempfile
import time
from xml.sax.saxutils import escape

import paramiko
from django.utils.translation import gettext_lazy as _

from vrtManager.connection import CONN_SSH, CONN_SOCKET, CONN_TCP, CONN_TLS

logger = logging.getLogger(__name__)
LOCK_TIMEOUT_SECONDS = 15
SSH_CONNECT_TIMEOUT_SECONDS = 10
SSH_IO_TIMEOUT_SECONDS = 30


def _fsync_dir(dir_path):
    if not dir_path or not os.path.isdir(dir_path):
        return
    try:
        dir_fd = os.open(dir_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _atomic_finalize_local(target_temp, target_final):
    parent_dir = os.path.dirname(target_final)
    # 1. Primary: POSIX os.link creates a hard link atomically and fails with FileExistsError if target_final exists.
    try:
        os.link(target_temp, target_final)
        _fsync_dir(parent_dir)
        try:
            os.unlink(target_temp)
            _fsync_dir(parent_dir)
        except OSError:
            pass
        return
    except FileExistsError:
        raise FileExistsError(_("Destination file already exists"))
    except OSError as link_err:
        # 2. Secondary: Linux kernel renameat2 with RENAME_NOREPLACE (flag 1)
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            ret = libc.renameat2(
                ctypes.c_int(-100),
                target_temp.encode("utf-8"),
                ctypes.c_int(-100),
                target_final.encode("utf-8"),
                ctypes.c_uint(1),
            )
            if ret == 0:
                _fsync_dir(parent_dir)
                return
            err = ctypes.get_errno()
            if err == 17:  # EEXIST
                raise FileExistsError(_("Destination file already exists"))
        except (AttributeError, OSError):
            pass

        # If atomic no-replace cannot be guaranteed, fail safely without destroying target_temp
        raise OSError(_("Filesystem does not support atomic no-replace finalization: {}").format(link_err))


def _atomic_finalize_ssh(sftp, target_temp, target_final):
    """
    Finalize with standard SFTP rename, which requires an unused destination.
    Atomic visibility depends on the remote SFTP server and filesystem.
    """
    try:
        sftp.rename(target_temp, target_final)
    except OSError as e:
        if "exist" in str(e).lower() or "already" in str(e).lower():
            raise FileExistsError(_("Destination file already exists")) from e
        raise


_STALE_UPLOAD_PATTERN = re.compile(r"^.+\.wvc_upload_[a-zA-Z0-9_\-]{8,64}\.(part|meta|meta\.tmp)$")


def _sftp_replace_file(sftp, src, dst, ssh=None):
    """Replace metadata atomically, leaving the old copy intact on failure."""
    posix_rename = getattr(sftp, "posix_rename", None)
    if callable(posix_rename):
        try:
            posix_rename(src, dst)
            return
        except (IOError, OSError) as e:
            err_str = str(e).lower()
            if not ("unsupported" in err_str or getattr(e, "code", None) == 8 or isinstance(e, NotImplementedError)):
                raise
    if ssh is not None:
        import shlex
        cmd = f"python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' {shlex.quote(src)} {shlex.quote(dst)}"
        try:
            stdin, stdout, stderr = ssh.exec_command(cmd, timeout=SSH_IO_TIMEOUT_SECONDS)
            if stdout.channel.recv_exit_status() == 0:
                return
        except Exception as exc:
            raise OSError(_("Remote server cannot safely replace upload metadata")) from exc
    raise OSError(_("Remote server cannot safely replace upload metadata"))


def _cleanup_stale_local_uploads(base_dir, max_age_seconds=86400):
    try:
        now = time.time()
        for entry in os.scandir(base_dir):
            if entry.is_file() and _STALE_UPLOAD_PATTERN.match(entry.name):
                try:
                    mtime = entry.stat().st_mtime
                    if now - mtime > max_age_seconds:
                        if entry.name.endswith(".meta"):
                            try:
                                with open(entry.path, "r", encoding="utf-8") as f:
                                    meta = json.load(f)
                                if not meta.get("upload_id") and not meta.get("expected_chunk"):
                                    continue
                            except Exception:
                                pass
                        os.unlink(entry.path)
                except OSError:
                    pass
    except OSError:
        pass


def _cleanup_stale_sftp_uploads(sftp, remote_base, max_age_seconds=86400):
    try:
        now = time.time()
        for attr in sftp.listdir_attr(remote_base):
            name = attr.filename
            if _STALE_UPLOAD_PATTERN.match(name):
                try:
                    if attr.st_mtime and (now - attr.st_mtime > max_age_seconds):
                        target_file = posixpath.join(remote_base, name)
                        sftp.remove(target_file)
                except Exception:
                    pass
    except Exception:
        pass


def _get_upload_lock_directory():
    candidates = [
        "/srv/webvirtcloud/data/locks",
        os.path.join(tempfile.gettempdir(), f"webvirtcloud_upload_locks_{os.geteuid()}"),
    ]
    for candidate in candidates:
        try:
            if os.path.islink(candidate):
                continue
            if not os.path.exists(candidate):
                os.makedirs(candidate, mode=0o700, exist_ok=True)
            if not os.path.isdir(candidate) or os.path.islink(candidate):
                continue
            stat_info = os.stat(candidate)
            if stat_info.st_uid != os.geteuid():
                continue
            if (stat.S_IMODE(stat_info.st_mode) & 0o077) != 0:
                try:
                    os.chmod(candidate, 0o700)
                except OSError:
                    continue
                stat_info = os.stat(candidate)
                if (stat.S_IMODE(stat_info.st_mode) & 0o077) != 0:
                    continue
            return candidate
        except (OSError, PermissionError):
            continue
    return None


def _cleanup_stale_lock_dir(lock_dir, max_age_seconds=86400):
    if not lock_dir or not os.path.exists(lock_dir):
        return
    try:
        now = time.time()
        for entry in os.scandir(lock_dir):
            if entry.is_file():
                if entry.name.startswith("upload_") and (entry.name.endswith(".done") or entry.name.endswith(".done.tmp")):
                    try:
                        if now - entry.stat().st_mtime > max_age_seconds:
                            os.unlink(entry.path)
                    except OSError:
                        pass
    except OSError:
        pass


def _mark_session_completed(lock_dir, upload_id, file_name, path=None, user_id=None, compute_id=None, pool=None):
    if not lock_dir or not upload_id or not file_name:
        return
    marker_path = os.path.join(lock_dir, f"upload_{upload_id}.done")
    if os.path.islink(marker_path):
        try:
            os.unlink(marker_path)
        except OSError:
            pass
    try:
        tmp_marker = f"{marker_path}.tmp"
        marker_data = {
            "upload_id": upload_id,
            "file_name": file_name,
            "path": posixpath.normpath(path) if path else None,
            "user_id": user_id,
            "compute_id": compute_id,
            "pool": pool,
            "completed_at": time.time(),
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp_marker, flags, 0o600)
        with open(fd, "w", encoding="utf-8", closefd=True) as f:
            json.dump(marker_data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_marker, marker_path)
        _fsync_dir(lock_dir)
    except Exception as e:
        logger.error("Failed to write upload session completion marker %s: %s", marker_path, e)
        raise


def _is_session_completed(upload_id, file_name, path=None, user_id=None, compute_id=None, pool=None, lock_dir=None, check_local_recovery=True):
    if not upload_id or not file_name:
        return False
    if not lock_dir:
        lock_dir = _get_upload_lock_directory()
    if not lock_dir:
        return False
    marker_path = os.path.join(lock_dir, f"upload_{upload_id}.done")
    if os.path.exists(marker_path) and not os.path.islink(marker_path):
        try:
            with open(marker_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            marker_valid = True
            if data.get("upload_id") != upload_id or data.get("file_name") != file_name:
                marker_valid = False
            if path and data.get("path"):
                if posixpath.normpath(path) != posixpath.normpath(data["path"]):
                    marker_valid = False
            if user_id is not None and data.get("user_id") is not None:
                if str(data["user_id"]) != str(user_id):
                    marker_valid = False
            if compute_id is not None and data.get("compute_id") is not None:
                if str(data["compute_id"]) != str(compute_id):
                    marker_valid = False
            if pool is not None and data.get("pool") is not None:
                if str(data["pool"]) != str(pool):
                    marker_valid = False
            if time.time() - data.get("completed_at", 0) > 86400:
                try:
                    os.unlink(marker_path)
                    _fsync_dir(lock_dir)
                except OSError:
                    pass
                marker_valid = False
            if marker_valid:
                return True
        except Exception:
            pass

    # Crash-recovery: check if metadata file exists in path with complete chunks and matching size
    if path and check_local_recovery:
        meta_suffix = f".wvc_upload_{upload_id}.meta"
        meta_path = os.path.join(path, f"{file_name}{meta_suffix}")
        final_path = os.path.join(path, file_name)
        if os.path.exists(meta_path) and os.path.exists(final_path) and not os.path.islink(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
                if (
                    meta.get("file_name") == file_name
                    and meta.get("expected_chunk") == meta.get("total_chunks")
                    and os.path.getsize(final_path) == meta.get("current_size")
                ):
                    try:
                        _mark_session_completed(
                            lock_dir, upload_id, file_name, path,
                            user_id=user_id, compute_id=compute_id, pool=pool,
                        )
                        os.unlink(meta_path)
                        _fsync_dir(path)
                    except Exception:
                        pass
                    return True
            except Exception:
                pass

    return False


class _UploadTarget:
    """Filesystem differences for one upload; chunk ordering lives in one place."""

    def __init__(self, conn, path, file_name, upload_id):
        self.conn = conn
        self.remote = conn.conn == CONN_SSH
        if not self.remote and conn.conn != CONN_SOCKET:
            raise ValueError(_("Unsupported connection type for file upload."))
        self.base = posixpath.normpath(path) if self.remote else os.path.abspath(path)
        join = posixpath.join if self.remote else os.path.join
        normalize = posixpath.normpath if self.remote else os.path.abspath
        self.final = normalize(join(self.base, file_name))
        self.part = normalize(join(self.base, f"{file_name}.wvc_upload_{upload_id}.part"))
        self.meta = normalize(join(self.base, f"{file_name}.wvc_upload_{upload_id}.meta"))
        for target in (self.final, self.part, self.meta):
            if self.remote:
                contained = target.startswith(self.base.rstrip("/") + "/")
            else:
                contained = os.path.commonpath((self.base, target)) == self.base
            if not contained:
                raise PermissionError(_("Security issues with file uploading: path traversal detected"))
        self.ssh = None
        self.sftp = None

    def __enter__(self):
        if self.remote:
            hostname, separator, port = self.conn.host.partition(":")
            self.ssh = paramiko.SSHClient()
            try:
                self.ssh.load_system_host_keys()
                self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
                self.ssh.connect(
                    hostname=hostname, port=int(port) if separator else 22,
                    username=self.conn.login, password=self.conn.passwd,
                    timeout=SSH_CONNECT_TIMEOUT_SECONDS,
                    banner_timeout=SSH_CONNECT_TIMEOUT_SECONDS,
                    auth_timeout=SSH_CONNECT_TIMEOUT_SECONDS,
                    channel_timeout=SSH_CONNECT_TIMEOUT_SECONDS,
                )
                self.sftp = self.ssh.open_sftp()
                self.sftp.get_channel().settimeout(SSH_IO_TIMEOUT_SECONDS)
            except Exception:
                self.ssh.close()
                raise
        return self

    def __exit__(self, *_):
        try:
            if self.sftp is not None:
                self.sftp.close()
        finally:
            if self.ssh is not None:
                self.ssh.close()

    def info(self, path):
        try:
            info = self.sftp.lstat(path) if self.remote else os.lstat(path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise PermissionError(_("Security issues with file uploading: symlink detected"))
        return info

    def read_meta(self):
        if self.info(self.meta) is None:
            return None
        try:
            if self.remote:
                with self.sftp.open(self.meta, "r") as stream:
                    raw = stream.read()
            else:
                with open(self.meta, "r", encoding="utf-8") as stream:
                    raw = stream.read()
            return json.loads(raw)
        except Exception as exc:
            raise ValueError(_("Corrupted upload session metadata")) from exc

    def write_meta(self, data):
        temporary = self.meta + ".tmp"
        if self.remote:
            if self.info(temporary) is not None:
                self.sftp.remove(temporary)
            with self.sftp.open(temporary, "w") as stream:
                stream.write(json.dumps(data))
                if callable(getattr(stream, "flush", None)):
                    stream.flush()
            _sftp_replace_file(self.sftp, temporary, self.meta, ssh=self.ssh)
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temporary, flags, 0o600)
            with open(fd, "w", encoding="utf-8", closefd=True) as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.meta)
            _fsync_dir(self.base)

    def write_chunk(self, chunk, offset, new):
        if self.remote:
            with self.sftp.open(self.part, "wb" if new else "r+b") as stream:
                if not new:
                    stream.seek(offset)
                    stream.truncate(offset)
                for data in chunk.chunks():
                    stream.write(data)
                    offset += len(data)
                if callable(getattr(stream, "flush", None)):
                    stream.flush()
            return offset
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL) if new else os.O_RDWR
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.part, flags, 0o600)
        with open(fd, "wb" if new else "r+b", closefd=True) as stream:
            if not new:
                stream.seek(offset)
                stream.truncate()
            for data in chunk.chunks():
                stream.write(data)
                offset += len(data)
            stream.flush()
            os.fsync(stream.fileno())
        return offset

    def finalize(self):
        if self.remote:
            _atomic_finalize_ssh(self.sftp, self.part, self.final)
        else:
            _atomic_finalize_local(self.part, self.final)

    def remove_meta(self):
        try:
            if self.remote:
                self.sftp.remove(self.meta)
            else:
                os.unlink(self.meta)
                _fsync_dir(self.base)
        except (OSError, IOError):
            pass

    def cleanup(self, lock_dir):
        if self.remote:
            _cleanup_stale_sftp_uploads(self.sftp, self.base)
        else:
            _cleanup_stale_local_uploads(self.base)
            _cleanup_stale_lock_dir(lock_dir)


def _matches_session(meta, file_name, total_chunks, user_id, compute_id, pool):
    if meta.get("file_name") != file_name:
        raise ValueError(_("File name does not match upload session"))
    if meta.get("total_chunks") != total_chunks:
        raise ValueError(_("Inconsistent total_chunks for session"))
    for key, expected in (("user_id", user_id), ("compute_id", compute_id), ("pool", pool)):
        if expected is not None and meta.get(key) is not None and str(meta[key]) != str(expected):
            raise PermissionError(_("Upload session belongs to another request context"))


def _acquire_upload_lock(lock_fd):
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(_("Timed out waiting for upload session lock"))
            time.sleep(0.05)


def _write_libvirt_meta(meta_path, data):
    temporary = meta_path + ".tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    with open(fd, "w", encoding="utf-8", closefd=True) as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, meta_path)
    _fsync_dir(os.path.dirname(meta_path))


def _handle_libvirt_upload(
    conn, path, file_name, file_chunk, is_last_chunk, chunk_index,
    total_chunks, upload_id, user_id, compute_id, pool, file_size, lock_dir,
):
    """Upload to a remote pool through the existing libvirt connection."""
    if not isinstance(file_size, int) or file_size <= 0:
        raise ValueError(_("A positive file_size is required for TCP/TLS uploads"))
    if file_chunk.size <= 0:
        raise ValueError(_("Empty upload chunk"))

    meta_path = os.path.join(lock_dir, f"upload_{upload_id}.libvirt.json")
    if os.path.islink(meta_path):
        raise PermissionError(_("Invalid upload session metadata"))
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as stream:
                meta = json.load(stream)
        except (OSError, ValueError) as exc:
            raise ValueError(_("Corrupted upload session metadata")) from exc
        _matches_session(meta, file_name, total_chunks, user_id, compute_id, pool)
        if meta.get("path") != path or meta.get("file_size") != file_size:
            raise ValueError(_("Upload session parameters changed"))
    else:
        if chunk_index != 0:
            raise ValueError(_("Upload session not found or expired"))
        meta = {
            "file_name": file_name, "total_chunks": total_chunks,
            "expected_chunk": 0, "current_size": 0, "file_size": file_size,
            "path": path, "user_id": user_id, "compute_id": compute_id,
            "pool": pool, "created_at": time.time(),
        }

    expected = meta["expected_chunk"]
    if chunk_index < expected:
        if is_last_chunk and expected == total_chunks:
            _mark_session_completed(
                lock_dir, upload_id, file_name, path,
                user_id=user_id, compute_id=compute_id, pool=pool,
            )
            os.unlink(meta_path)
            _fsync_dir(lock_dir)
        return
    if chunk_index != expected:
        raise ValueError(
            _("Invalid chunk sequence. Expected chunk %(exp)d, got %(got)d")
            % {"exp": expected, "got": chunk_index}
        )

    offset = meta["current_size"]
    if offset + file_chunk.size > file_size:
        raise ValueError(_("Upload exceeds declared file size"))
    if is_last_chunk and offset + file_chunk.size != file_size:
        raise ValueError(_("Final chunk does not match declared file size"))

    # A volume is visible as soon as libvirt creates it. Keep the session until
    # the final chunk finishes so retries can resume safely at a known offset.
    volumes = conn.get_volumes()
    if meta["expected_chunk"] == 0 and not os.path.exists(meta_path):
        if file_name in volumes:
            raise FileExistsError(_("Destination file already exists"))
        xml = (
            "<volume><name>{}</name><capacity unit='bytes'>{}</capacity>"
            "<target><format type='raw'/></target></volume>"
        ).format(escape(file_name), file_size)
        conn.pool.createXML(xml, 0)
        _write_libvirt_meta(meta_path, meta)
    elif file_name not in volumes:
        raise ValueError(_("Upload volume is missing"))

    volume = conn.get_volume(file_name)
    stream = conn.wvm.newStream(0)
    try:
        volume.upload(stream, offset, file_chunk.size, 0)
        written = 0
        for data in file_chunk.chunks():
            while data:
                sent = stream.send(data)
                if sent <= 0:
                    raise OSError(_("Libvirt stream stopped during upload"))
                written += sent
                data = data[sent:]
        if written != file_chunk.size:
            raise OSError(_("Upload chunk size changed during transfer"))
        stream.finish()
    except Exception:
        stream.abort()
        raise

    meta["current_size"] = offset + written
    meta["expected_chunk"] = expected + 1
    meta["updated_at"] = time.time()
    _write_libvirt_meta(meta_path, meta)
    if is_last_chunk:
        _mark_session_completed(
            lock_dir, upload_id, file_name, path,
            user_id=user_id, compute_id=compute_id, pool=pool,
        )
        os.unlink(meta_path)
        _fsync_dir(lock_dir)


def handle_uploaded_file(
    conn, path, file_name, file_chunk, is_last_chunk, chunk_index=0,
    total_chunks=1, upload_id=None, user_id=None, compute_id=None, pool=None,
    file_size=None,
):
    clean_name = os.path.basename(file_name).strip()
    if not clean_name or clean_name in (".", "..") or "\x00" in clean_name:
        raise ValueError(_("Invalid file name"))
    if len(clean_name) <= 4 or not clean_name.lower().endswith(".iso"):
        raise ValueError(_("Only .iso files can be uploaded"))
    if total_chunks < 1 or chunk_index < 0 or chunk_index >= total_chunks:
        raise ValueError(_("Invalid chunk index or total chunks."))
    if is_last_chunk != (chunk_index == total_chunks - 1):
        raise ValueError(_("Invalid final chunk flag"))
    if not upload_id or not isinstance(upload_id, str):
        raise ValueError(_("Missing required upload_id"))
    upload_id = upload_id.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", upload_id):
        raise ValueError(_("Invalid upload_id format"))

    libvirt_upload = conn.conn in (CONN_TCP, CONN_TLS)
    target = None if libvirt_upload else _UploadTarget(conn, path, clean_name, upload_id)
    lock_dir = _get_upload_lock_directory()
    if not lock_dir:
        raise OSError(_("Could not acquire secure upload lock directory"))
    lock_path = os.path.join(lock_dir, f"upload_session_{upload_id}.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    lock_fd = os.open(lock_path, flags, 0o600)
    try:
        _acquire_upload_lock(lock_fd)
        if libvirt_upload:
            if _is_session_completed(
                upload_id, clean_name, path, user_id=user_id,
                compute_id=compute_id, pool=pool, lock_dir=lock_dir,
                check_local_recovery=False,
            ):
                if is_last_chunk:
                    if clean_name not in conn.get_volumes():
                        raise ValueError(_("Upload volume is missing"))
                    return
                raise FileExistsError(_("Destination file already exists"))
            return _handle_libvirt_upload(
                conn, path, clean_name, file_chunk, is_last_chunk, chunk_index,
                total_chunks, upload_id, user_id, compute_id, pool, file_size, lock_dir,
            )
        with target:
            if chunk_index == 0:
                target.cleanup(lock_dir)
            final_info = target.info(target.final)
            if final_info is not None:
                if is_last_chunk and _is_session_completed(
                    upload_id, clean_name, target.base, user_id=user_id,
                    compute_id=compute_id, pool=pool, lock_dir=lock_dir,
                ):
                    return
                # A crash after rename but before the completion marker is recoverable.
                meta = target.read_meta() if is_last_chunk else None
                if meta is not None:
                    _matches_session(meta, clean_name, total_chunks, user_id, compute_id, pool)
                    if (meta.get("expected_chunk") == total_chunks
                            and final_info.st_size == meta.get("current_size")):
                        _mark_session_completed(lock_dir, upload_id, clean_name, target.base,
                                                user_id=user_id, compute_id=compute_id, pool=pool)
                        target.remove_meta()
                        return
                raise FileExistsError(_("Destination file already exists"))

            meta = target.read_meta()
            if meta is None:
                if chunk_index != 0 or target.info(target.part) is not None:
                    raise ValueError(_("Upload session not found or expired"))
                meta = {
                    "file_name": clean_name, "total_chunks": total_chunks,
                    "expected_chunk": 0, "current_size": 0,
                    "created_at": time.time(), "user_id": user_id,
                    "compute_id": compute_id, "pool": pool,
                }
            else:
                _matches_session(meta, clean_name, total_chunks, user_id, compute_id, pool)
                if target.info(target.part) is None:
                    raise ValueError(_("Upload session not found or expired"))

            expected = meta["expected_chunk"]
            if chunk_index < expected:
                if is_last_chunk and expected == total_chunks:
                    target.finalize()
                    _mark_session_completed(lock_dir, upload_id, clean_name, target.base,
                                            user_id=user_id, compute_id=compute_id, pool=pool)
                    target.remove_meta()
                return
            if chunk_index != expected:
                raise ValueError(
                    _("Invalid chunk sequence. Expected chunk %(exp)d, got %(got)d")
                    % {"exp": expected, "got": chunk_index}
                )

            meta["current_size"] = target.write_chunk(file_chunk, meta["current_size"], new=expected == 0)
            meta["expected_chunk"] = expected + 1
            meta["updated_at"] = time.time()
            target.write_meta(meta)
            if is_last_chunk:
                target.finalize()
                _mark_session_completed(lock_dir, upload_id, clean_name, target.base,
                                        user_id=user_id, compute_id=compute_id, pool=pool)
                target.remove_meta()
    finally:
        os.close(lock_fd)
