from contextlib import contextmanager
import fcntl
import logging
import os
import stat
import tempfile
import threading
import time
from datetime import timedelta

from accounts.models import UserInstance
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from computes.models import Compute
from instances.models import Instance, InstanceTombstone

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

            test_file = os.path.join(candidate, f".test_write_{os.getpid()}_{threading.get_ident()}")
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
def _libvirt_lock(key, filename, timeout=15.0, shared=False):
    """Use flock for both threads and processes; exclusive locks also use a mutex."""
    held_locks = _get_thread_held_locks()
    if key in held_locks:
        if held_locks[key] and not shared:
            raise RuntimeError("Cannot upgrade a shared compute lock inside a VM operation")
        yield
        return

    thread_lock = None if shared else _get_compute_thread_lock(key)
    start_time = time.monotonic()
    if thread_lock is not None and not thread_lock.acquire(timeout=timeout):
        raise TimeoutError(f"Timeout waiting for lock on {key}")
    try:
        lock_dir = _get_lock_directory()
        if not lock_dir:
            raise OSError(f"Could not acquire secure lock directory for {key}")
        path = os.path.join(lock_dir, filename)
        if os.path.islink(path):
            raise PermissionError(f"Lock file {path} is a symlink")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        acquired = False
        try:
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            while True:
                try:
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() - start_time >= timeout:
                        raise TimeoutError(f"Timeout waiting for lock on {key}")
                    time.sleep(0.05)
            held_locks[key] = shared
            try:
                yield
            finally:
                held_locks.pop(key, None)
        finally:
            try:
                if acquired:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    finally:
        if thread_lock is not None:
            thread_lock.release()


@contextmanager
def libvirt_compute_lock(compute, timeout=15.0, *, shared=False):
    """Exclude reconciliation/migration while allowing independent VM operations."""
    compute_pk = getattr(compute, "pk", getattr(compute, "id", None))
    if not compute_pk:
        yield
        return
    with _libvirt_lock(compute_pk, f"sync_compute_{compute_pk}.lock", timeout, shared):
        yield


@contextmanager
def user_quota_lock(user, timeout=15.0):
    """One user's quota check and the change it allows run one at a time, on every compute.
    Take it before any compute or VM lock."""
    with _libvirt_lock(("quota", user.pk), f"quota_user_{user.pk}.lock", timeout):
        yield


@contextmanager
def libvirt_instance_lock(instance, timeout=15.0):
    """Serialize one VM and share the compute barrier with other VM operations."""
    with libvirt_compute_lock(instance.compute, timeout, shared=True):
        with _libvirt_lock(("instance", instance.pk), f"instance_{instance.pk}.lock", timeout):
            yield


def _move_ownership(source, target):
    """Give target the owners and template flag of source; a user's rights are merged."""
    for ui in UserInstance.objects.filter(instance=source):
        existing = UserInstance.objects.filter(instance=target, user=ui.user).first()
        if existing is None:
            ui.instance = target
            ui.save(update_fields=["instance"])
            continue
        for flag in ("is_change", "is_delete", "is_vnc"):
            if getattr(ui, flag):
                setattr(existing, flag, True)
        existing.save()
    if source.is_template and not target.is_template:
        target.is_template = True
        target.save(update_fields=["is_template"])


def _bury(inst):
    owners = [
        {"user": ui.user_id, "is_change": ui.is_change, "is_delete": ui.is_delete, "is_vnc": ui.is_vnc}
        for ui in UserInstance.objects.filter(instance=inst)
    ]
    if owners or inst.is_template:
        InstanceTombstone.objects.create(uuid=inst.uuid, name=inst.name, is_template=inst.is_template, owners=owners)
        logger.info("Instance %s (%s) disappeared; ownership kept", inst.name, inst.uuid)


def _restore(inst):
    tombstone = InstanceTombstone.objects.filter(uuid=inst.uuid).order_by("-removed").first()
    if tombstone is None:
        return
    users = set(get_user_model().objects.filter(id__in=[o["user"] for o in tombstone.owners]).values_list("id", flat=True))
    for owner in tombstone.owners:
        if owner["user"] in users:
            UserInstance.objects.create(
                instance=inst,
                user_id=owner["user"],
                is_change=owner["is_change"],
                is_delete=owner["is_delete"],
                is_vnc=owner["is_vnc"],
            )
    if tombstone.is_template:
        inst.is_template = True
        inst.save(update_fields=["is_template"])
    InstanceTombstone.objects.filter(uuid=inst.uuid).delete()
    logger.info("Instance %s (%s) is back; ownership restored", inst.name, inst.uuid)


def refresh_instance_database(compute):
    """
    Synchronizes the WebVirtCloud database with libvirt domain state.
    Libvirt is the single source of truth:
      - VMs removed in virt-manager/virsh are deleted from the database; their
        ownership is moved to the same UUID on another compute or kept in a
        tombstone for INSTANCE_OWNERSHIP_RETENTION_DAYS (R-05).
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

                # 2. Instances gone from this compute. Their ownership goes to the
                #    same UUID on another compute (an out-of-band move) or is kept
                #    in a tombstone in case the VM comes back (R-05).
                for inst in db_instances:
                    if inst.uuid not in host_domains:
                        elsewhere = Instance.objects.filter(uuid=inst.uuid).exclude(compute=compute).first()
                        if elsewhere:
                            _move_ownership(inst, elsewhere)
                        else:
                            _bury(inst)
                        inst.delete()

                # 3. Instances created out-of-band. A UUID that also exists on another
                #    compute is a copy and gets no owners; otherwise a tombstone's
                #    ownership is restored.
                retention = getattr(settings, "INSTANCE_OWNERSHIP_RETENTION_DAYS", 30)
                InstanceTombstone.objects.filter(removed__lt=timezone.now() - timedelta(days=retention)).delete()
                for uuid, name in host_domains.items():
                    if uuid not in db_uuids:
                        inst, created = Instance.objects.get_or_create(
                            compute=compute,
                            uuid=uuid,
                            defaults={"name": name},
                        )
                        if created and not Instance.objects.filter(uuid=uuid).exclude(compute=compute).exists():
                            _restore(inst)
    except (OSError, PermissionError, TimeoutError) as e:
        logger.error("Could not synchronize instances for compute %s: %s; failing closed", compute_pk, e)
        return
