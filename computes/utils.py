import logging
from django.db import transaction
from instances.models import Instance

logger = logging.getLogger(__name__)


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
    try:
        if hasattr(compute, "status") and compute.status is False:
            return
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
            # If we fail to read a domain's metadata, abort sync to prevent accidental deletion
            return

    db_instances = Instance.objects.filter(compute=compute)
    db_uuids = set(db_instances.values_list("uuid", flat=True))

    with transaction.atomic():
        # 1. Update name for any VM whose name changed out-of-band in virsh/virt-manager
        for inst in db_instances:
            if inst.uuid in host_domains:
                new_name = host_domains[inst.uuid]
                if inst.name != new_name:
                    inst.name = new_name
                    inst.save(update_fields=["name"])

        # 2. Delete instances that were genuinely undefined/deleted in virsh/virt-manager
        db_instances.exclude(uuid__in=host_domains.keys()).delete()

        # 3. Create new instances that were created out-of-band in virsh/virt-manager
        for uuid, name in host_domains.items():
            if uuid not in db_uuids:
                Instance.objects.create(compute=compute, name=name, uuid=uuid)
