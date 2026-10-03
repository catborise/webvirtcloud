"""Safety helpers for the live libvirt test suite.

Everything the suite creates is named with PREFIX, and its volumes live
in a dedicated pool. Cleanup only removes prefixed domains and the
volumes of that pool, so a broken test cannot delete other VMs or images
on a shared host. inventory() lets a suite check that nothing else
changed.
"""

import os

import libvirt

PREFIX = "wvc-test-"
POOL = "wvc-test"
POOL_PATH = "/var/lib/libvirt/wvc-test"

UNDEFINE_FLAGS = (
    libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
    | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
    | libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
    | libvirt.VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA
)


def enabled():
    """The suite creates and deletes VMs, so it only runs on an explicitly named host."""
    return bool(os.environ.get("TEST_LIBVIRT_HOST"))


def ensure_pool(conn):
    try:
        pool = conn.storagePoolLookupByName(POOL)
    except libvirt.libvirtError:
        pool = conn.storagePoolDefineXML(
            f"<pool type='dir'><name>{POOL}</name><target><path>{POOL_PATH}</path></target></pool>"
        )
        pool.build(0)
    if not pool.isActive():
        pool.create(0)
    pool.refresh(0)
    return pool


def cleanup(conn):
    """Remove prefixed domains and every volume of the test pool."""
    for dom in conn.listAllDomains():
        if not dom.name().startswith(PREFIX):
            continue
        if dom.isActive():
            dom.destroy()
        dom.undefineFlags(UNDEFINE_FLAGS)
    try:
        pool = conn.storagePoolLookupByName(POOL)
    except libvirt.libvirtError:
        return
    if pool.isActive():
        pool.refresh(0)
        for vol in pool.listAllVolumes():
            vol.delete(0)


def remove_pool(conn):
    cleanup(conn)
    try:
        pool = conn.storagePoolLookupByName(POOL)
    except libvirt.libvirtError:
        return
    if pool.isActive():
        pool.destroy()
    pool.delete(0)
    pool.undefine()


def inventory(conn):
    """Domains and volumes outside the test namespace."""
    domains = {(d.name(), d.UUIDString()) for d in conn.listAllDomains() if not d.name().startswith(PREFIX)}
    volumes = set()
    for pool in conn.listAllStoragePools():
        if pool.name() == POOL or not pool.isActive():
            continue
        pool.refresh(0)
        volumes.update((pool.name(), v.name()) for v in pool.listAllVolumes())
    return domains, volumes
