from contextlib import contextmanager
import fcntl
import logging
import os
import stat
import tempfile
import threading
import time
from django.db import transaction
from computes.models import Compute
from instances.models import Instance

logger = logging.getLogger(__name__)

_COMPUTE_THREAD_LOCKS = {}
_COMPUTE_THREAD_LOCKS_GUARD = threading.Lock()


def _get_compute_thread_lock(compute_pk):
    with _COMPUTE_THREAD_LOCKS_GUARD:
        if compute_pk not in _COMPUTE_THREAD_LOCKS:
            _COMPUTE_THREAD_LOCKS[compute_pk] = threading.Lock()
        return _COMPUTE_THREAD_LOCKS[compute_pk]


def _get_lock_directory():
    candidates = [
        "/srv/webvirtcloud/data/locks",
        os.path.join(tempfile.gettempdir(), f"webvirtcloud_locks_{os.geteuid()}"),
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
            # Ensure candidate directory is owned by the current process user
            if stat_info.st_uid != os.geteuid():
                continue
            # Ensure candidate directory has private permissions (mode 0700)
            if (stat.S_IMODE(stat_info.st_mode) & 0o077) != 0:
                try:
                    os.chmod(candidate, 0o700)
                except OSError:
                    continue
                stat_info = os.stat(candidate)
                if (stat.S_IMODE(stat_info.st_mode) & 0o077) != 0:
                    continue

            test_file = os.path.join(candidate, f".test_write_{os.getpid()}")
            with open(test_file, "w") as tf:
                tf.write("1")
            os.unlink(test_file)
            return candidate
        except (OSError, PermissionError):
            continue
    return None


_thread_local = threading.local()


def _get_thread_held_locks():
    if not hasattr(_thread_local, "compute_locks"):
        _thread_local.compute_locks = {}
    return _thread_local.compute_locks


@contextmanager
def libvirt_compute_lock(compute, timeout=15.0):
    """
    Context manager providing multi-thread and multi-process synchronization
    for libvirt compute operations and instance database synchronization.
    Supports re-entrant locking within the same thread.
    Fails closed on lock directory or permission errors.
    """
    compute_pk = getattr(compute, "pk", getattr(compute, "id", None))
    if not compute_pk:
        yield
        return

    held_locks = _get_thread_held_locks()
    if compute_pk in held_locks:
        held_locks[compute_pk] += 1
        try:
            yield
        finally:
            held_locks[compute_pk] -= 1
            if held_locks[compute_pk] <= 0:
                held_locks.pop(compute_pk, None)
        return

    thread_lock = _get_compute_thread_lock(compute_pk)
    start_time = time.time()
    acquired_thread = thread_lock.acquire(timeout=timeout)
    if not acquired_thread:
        logger.error("Timeout waiting for in-process thread lock on compute %s; aborting to fail closed", compute_pk)
        raise TimeoutError(f"Timeout waiting for lock on compute {compute_pk}")

    try:
        lock_dir = _get_lock_directory()
        if not lock_dir:
            logger.error("Failed to acquire application lock directory for compute %s; aborting to fail closed", compute_pk)
            raise OSError(f"Could not acquire secure lock directory for compute {compute_pk}")

        lock_file_path = os.path.join(lock_dir, f"sync_compute_{compute_pk}.lock")
        if os.path.islink(lock_file_path):
            logger.error("Lock file %s is a symlink! Aborting to fail closed", lock_file_path)
            raise PermissionError(f"Compute lock file {lock_file_path} is a symlink")

        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(lock_file_path, flags, 0o600)
        except OSError as e:
            logger.error("Could not open synchronization lock file for compute %s: %s; aborting to fail closed", compute_pk, e)
            raise

        lock_acquired = False
        try:
            while time.time() - start_time < timeout:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock_acquired = True
                    break
                except (BlockingIOError, OSError):
                    time.sleep(0.05)

            if not lock_acquired:
                logger.error("Timeout waiting for cross-process synchronization lock on compute %s; aborting to fail closed", compute_pk)
                raise TimeoutError(f"Timeout waiting for lock on compute {compute_pk}")

            held_locks[compute_pk] = 1
            try:
                yield
            finally:
                held_locks.pop(compute_pk, None)
        finally:
            if lock_acquired:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
    finally:
        thread_lock.release()


def refresh_instance_database(compute):
    """
    Synchronizes the WebVirtCloud database with libvirt domain state.
    Libvirt is the single source of truth:
      - VMs removed in virt-manager/virsh are deleted from the database.
      - VMs created in virt-manager/virsh are added to the database.
      - VMs renamed in virt-manager/virsh have their name updated without losing
        UserInstance permission relationships (matched by UUID).
      - If libvirt is unreachable (connection/network error), the database is left
        untouched to avoid false deletions.
    """
    compute_pk = getattr(compute, "pk", getattr(compute, "id", None))
    if not compute_pk:
        return

    try:
        with libvirt_compute_lock(compute, timeout=15.0):
            with transaction.atomic():
                comp_locked = Compute.objects.select_for_update().filter(pk=compute_pk).first()
                if not comp_locked:
                    return

                if hasattr(compute, "status") and compute.status is False:
                    return

                try:
                    domains = compute.proxy.wvm.listAllDomains()
                except Exception as e:
                    logger.warning(
                        "Failed to retrieve domains from compute %s (%s): %s",
                        getattr(compute, "name", compute),
                        getattr(compute, "hostname", ""),
                        e,
                    )
                    return

                # Map libvirt UUIDs to current domain names
                host_domains = {}
                for d in domains:
                    try:
                        host_domains[d.UUIDString()] = d.name()
                    except Exception as e:
                        logger.warning(
                            "Error reading domain metadata from compute %s: %s",
                            getattr(compute, "name", compute),
                            e,
                        )
                        return

                db_instances = list(Instance.objects.filter(compute=compute).select_for_update())
                db_uuids = set(inst.uuid for inst in db_instances)

                # 1. Update name for any VM whose name changed out-of-band in virsh/virt-manager
                for inst in db_instances:
                    if inst.uuid in host_domains:
                        new_name = host_domains[inst.uuid]
                        if inst.name != new_name:
                            inst.name = new_name
                            inst.save(update_fields=["name"])

                # 2. Delete instances that were genuinely undefined/deleted in virsh/virt-manager
                Instance.objects.filter(compute=compute).exclude(uuid__in=host_domains.keys()).delete()

                # 3. Create or update new instances that were created out-of-band in virsh/virt-manager
                for uuid, name in host_domains.items():
                    if uuid not in db_uuids:
                        Instance.objects.get_or_create(
                            compute=compute,
                            uuid=uuid,
                            defaults={"name": name},
                        )
    except (OSError, PermissionError, TimeoutError) as e:
        logger.error("Could not synchronize instances for compute %s: %s; failing closed", compute_pk, e)
        return
