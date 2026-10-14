"""Jobs of the instances app (operations framework, spec section 5)."""

import re

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_noop
from django.utils.translation import gettext_noop as _
from libvirt import (VIR_CONNECT_LIST_STORAGE_POOLS_ACTIVE, VIR_DOMAIN_SNAPSHOT_LIST_EXTERNAL, VIR_ERR_NO_DOMAIN,
                     libvirtError)
from vrtManager import util
from vrtManager.connection import wvmConnect
from vrtManager.create import wvmCreate
from vrtManager.instance import wvmInstance

from accounts.models import UserInstance
from appsettings.settings import app_settings
from computes.models import Compute
from instances import utils
from instances.models import Instance
from operations import claims
from operations.jobs import Kind, Outcome, Plan, register
from operations.models import Claim, Operation


def _authorized(user, pk):
    """The instance views' rule: change access and the snapshot permission."""
    from instances.views import get_instance

    if user is None or not user.is_active:
        raise PermissionDenied
    instance = get_instance(user, pk, perm_type="change")
    if not user.has_perm("instances.snapshot_instances"):
        raise PermissionDenied
    return instance


def _proxy(instance):
    c = instance.compute
    return wvmInstance(c.hostname, c.login, c.password, c.type, instance.name, uuid=instance.uuid)


def _instance(pk):
    return Instance.objects.select_related("compute").get(pk=pk)


CLONE_POST_KEY_RE = re.compile(r"^(clone-net-mac-\d+|disk-[a-z0-9]+|meta-[a-z0-9]+)$")
CLONE_FIELDS = ("name", "clone-title", "clone-description")


def clone_data(user, instance, fields):
    """The clone's settings from the form fields and the source as it is now;
    OperationError with the reason when it cannot be made."""
    from instances.views import VOLUME_NAME_RE, quota_refused

    data = dict()
    data["name"] = fields.get("name", "").strip()
    data["clone-title"] = fields.get("clone-title", "").strip()
    data["clone-description"] = fields.get("clone-description", "").strip()

    # the clone copies the disks of the persistent definition
    disk_sum = sum(int(disk.get("size") or 0) >> 30 for disk in instance.config_disks)
    quota_msg = utils.check_user_quota(user, 1, instance.vcpu, instance.memory, disk_sum)

    data["disk_owner_uid"] = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_UID)
    data["disk_owner_gid"] = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_GID)

    # Only superusers may choose disk names and MAC addresses; the form only
    # shows those fields to them.
    if user.is_superuser:
        for key, value in fields.items():
            if CLONE_POST_KEY_RE.fullmatch(key):
                data[key] = value.strip()

    if app_settings.CLONE_INSTANCE_AUTO_NAME == "True" and not data["name"]:
        auto_vname = utils.get_clone_free_names()[0]
        data["name"] = auto_vname
        data["clone-net-mac-0"] = utils.get_dhcp_mac_address(auto_vname)
        for disk in instance.config_disks:
            data[f"disk-{disk['dev']}"] = utils.get_clone_disk_name(disk, instance.name, auto_vname)

    if not user.is_superuser:
        for disk in instance.config_disks:
            data[f"disk-{disk['dev']}"] = utils.get_clone_disk_name(disk, instance.name, data["name"])
        for num in range(max(1, len(instance.config_networks))):
            key = f"clone-net-mac-{num}"
            if not data.get(key):
                data[key] = (utils.get_dhcp_mac_address(data["name"]) if num == 0 else "") or utils.get_random_mac_address()

    check_instance = Instance.objects.filter(name=data["name"])
    invalid_macs = [
        value for key, value in data.items()
        if key.startswith("clone-net-mac-") and not utils.MAC_ADDRESS_RE.fullmatch(value)
    ]
    invalid_disks = [
        value for key, value in data.items()
        # None: a disk without a volume, which clone_instance does not copy.
        if key.startswith("disk-") and value is not None and not VOLUME_NAME_RE.fullmatch(value)
    ]

    if not user.is_superuser and quota_msg:
        raise util.OperationError(quota_refused(quota_msg, _(
            "User '%(quota_msg)s' quota reached, cannot create '%(clone_name)s'!"
        ) % {"quota_msg": quota_msg, "clone_name": data["name"]}))
    if check_instance:
        raise util.OperationError(_("Instance '%(clone_name)s' already exists!") % {"clone_name": data["name"]})
    if not re.match(r"^[a-zA-Z0-9-]+$", data["name"]):
        raise util.OperationError(
            _("Instance name '%(clone_name)s' contains invalid characters!") % {"clone_name": data["name"]})
    if "clone-net-mac-0" not in data or invalid_macs:
        raise util.OperationError(_("Instance MAC '%(clone_mac)s' invalid format!") % {"clone_mac": ", ".join(invalid_macs)})
    if invalid_disks:
        raise util.OperationError(
            _("Disk name '%(disk_name)s' contains invalid characters!") % {"disk_name": ", ".join(str(v) for v in invalid_disks)})
    return data


def vm_claims(compute, name):
    """A job that adds a VM: other VMs stay usable; the VM list reconcile waits."""
    return {claims.compute_key(compute.pk): Claim.SHARED, claims.reconcile_key(compute.pk): Claim.EXCLUSIVE,
            claims.name_key(compute.pk, name): Claim.EXCLUSIVE}


def record_vm(op, owner):
    """The panel's records of the new VM, as the synchronous views wrote them."""
    vm = Instance.objects.get_or_create(compute_id=op.intent["compute"], uuid=op.intent["uuid"],
                                        defaults={"name": op.intent["name"]})[0]
    if owner and op.user_id:
        UserInstance.objects.get_or_create(instance=vm, user_id=op.user_id,
                                           defaults={"is_delete": True, "is_change": True, "is_vnc": True})


def vm_outcome(op, owner, volumes):
    """The VM is defined last: if it exists, the job succeeded. If it does not and
    none of its planned volumes exists either, the job failed and changed nothing."""
    c = Compute.objects.get(pk=op.intent["compute"])
    conn = wvmConnect(c.hostname, c.login, c.password, c.type).wvm
    try:
        conn.lookupByUUIDString(op.intent["uuid"])
    except libvirtError as err:
        if err.get_error_code() != VIR_ERR_NO_DOMAIN:
            raise  # unreachable: recovery waits and retries
        held = set()
        for pool in conn.listAllStoragePools(VIR_CONNECT_LIST_STORAGE_POOLS_ACTIVE):
            held.update(pool.listVolumes())
        left = sorted(held & set(volumes))
        if not left:
            return Outcome(Operation.FAILED, error="The VM was not defined and no volume of it was left.")
        return Outcome(Operation.UNCERTAIN,
                       error=f"The VM was not defined; check these volumes, then resolve this job: {', '.join(left)}")
    record_vm(op, owner)
    return Outcome(Operation.SUCCEEDED)


def _clonable(user, pk):
    from instances.views import _clone_source

    if user is None or not user.has_perm("instances.clone_instances"):
        raise PermissionDenied
    return _clone_source(user, pk)


def _merged(jobs):
    """(done, total) bytes of the snapshot's block jobs."""
    return sum(j["cur"] for j in jobs), sum(j["end"] for j in jobs)


@register
class DeleteExternalSnapshot(Kind):
    name = "snapshot.delete"
    label = gettext_noop("Delete snapshot")
    time_limit = 6 * 3600  # a merge cannot be stopped: over time is only noted
    progress_bytes = True  # libvirt block job cur/end

    def normalize(self, raw):
        return {"instance": int(raw["instance"]), "name": str(raw.get("name", ""))}

    def validate(self, user, params):
        instance = _authorized(user, params["instance"])
        if params["name"] not in _proxy(instance).instance.snapshotListNames(VIR_DOMAIN_SNAPSHOT_LIST_EXTERNAL):
            raise util.OperationError(f"The VM has no external snapshot named {params['name']}")

    def plan(self, user, params):
        instance = _instance(params["instance"])
        return Plan(
            claims={claims.compute_key(instance.compute_id): Claim.SHARED,
                    claims.instance_key(instance.uuid): Claim.EXCLUSIVE},
            resource=claims.instance_key(instance.uuid),
            label=f"{instance.compute.name}/{instance.name}",
            intent={"compute": instance.compute_id},
        )

    def precheck(self, op, ctx):
        instance = _authorized(op.user, op.params["instance"])
        if instance.compute_id != op.intent["compute"]:
            raise util.OperationError("The VM moved to another compute after the job was queued")
        proxy = _proxy(instance)
        proxy._snapshot(op.params["name"], external=True)
        proxy._check_snapshot_state()

    def run(self, op, ctx):
        proxy = _proxy(_instance(op.params["instance"]))
        ctx.record_intent(before=proxy.snapshot_files_state())
        ctx.set_phase(gettext_noop("Merging the snapshot"))
        return {"left_behind": proxy.delete_external_snapshot(op.params["name"])}

    def progress(self, op):
        jobs = _proxy(_instance(op.params["instance"])).get_snapshot_jobs()
        return _merged(jobs) if jobs else None

    def reconcile(self, op):
        instance = Instance.objects.select_related("compute").filter(pk=op.params["instance"]).first()
        if instance is None or instance.compute_id != op.intent.get("compute"):
            return Outcome(Operation.UNCERTAIN, error="The VM is no longer on the compute the job ran on")
        try:
            proxy = _proxy(instance)
            listed = op.params["name"] in proxy.instance.snapshotListNames(VIR_DOMAIN_SNAPSHOT_LIST_EXTERNAL)
            jobs = proxy.get_snapshot_jobs()
        except util.ConnectionFailed:
            raise  # unreachable: recovery waits and retries
        except libvirtError as err:  # OperationError is a libvirtError too
            if err.get_error_code() == VIR_ERR_NO_DOMAIN:
                return Outcome(Operation.UNCERTAIN, error="The VM is no longer on its compute")
            raise
        if jobs:
            return Outcome(Operation.RUNNING, phase=gettext_noop("libvirt is still merging the snapshot"),
                           stalled=all(j["cur"] == j["end"] for j in jobs),
                           progress=_merged(jobs))
        if not listed:
            before = op.intent.get("before")  # recorded with effects_started; absent only if run never began
            left = proxy.left_behind_since(before) if before else []
            return Outcome(Operation.SUCCEEDED, result={"left_behind": left})
        return Outcome(Operation.UNCERTAIN,
                       error="The snapshot is still listed and no merge runs. It cannot be told whether the disks "
                             "changed; check the VM before deleting the snapshot again or reverting to it.")

    def log_entry(self, op, state):
        compute, _sep, vm = op.resource_label.rpartition("/")  # a VM name has no "/"
        message = f"Delete external snapshot: {op.params['name']}"
        return compute, vm, message if state == Operation.SUCCEEDED else f"{message} ({state})"


@register
class CloneInstance(Kind):
    name = "instance.clone"
    label = gettext_noop("Clone VM")
    time_limit = 12 * 3600  # a copy cannot be stopped: over time is only noted

    def normalize(self, raw):
        fields = {k: str(v) for k, v in raw.items() if k in CLONE_FIELDS or CLONE_POST_KEY_RE.fullmatch(k)}
        return {"instance": int(raw["instance"]), "fields": fields}

    def plan(self, user, params):
        instance = _clonable(user, params["instance"])
        if instance.proxy.get_status() != 5:
            raise util.OperationError("Shut the VM down before cloning it")
        data = clone_data(user, instance, params["fields"])
        keys = vm_claims(instance.compute, data["name"])
        keys[claims.instance_key(instance.uuid)] = Claim.SHARED
        return Plan(claims=keys, resource=claims.instance_key(instance.uuid),
                    label=f"{instance.compute.name}/{instance.name}",
                    intent={"compute": instance.compute_id, "uuid": util.randomUUID(), "name": data["name"],
                            "clone": data})

    def precheck(self, op, ctx):
        instance = _clonable(op.user, op.params["instance"])
        if instance.compute_id != op.intent["compute"]:
            raise util.OperationError("The VM moved to another compute after the job was queued")

    def run(self, op, ctx):
        instance = _instance(op.params["instance"])
        ctx.record_intent()
        ctx.set_phase(gettext_noop("Copying disks"))
        instance.proxy.clone_instance(op.intent["clone"], op.intent["uuid"])
        record_vm(op, owner=True)
        return {}

    def reconcile(self, op):
        disks = [v for k, v in op.intent["clone"].items() if k.startswith("disk-") and v]
        return vm_outcome(op, owner=True, volumes=disks)

    def log_entry(self, op, state):
        compute, _sep, source = op.resource_label.rpartition("/")
        message = f"Create a clone of '{source}'"
        return compute, op.intent["name"], message if state == Operation.SUCCEEDED else f"{message} ({state})"


@register
class CreateInstance(Kind):
    name = "instance.create"
    label = gettext_noop("Create VM")
    time_limit = 12 * 3600  # a copy cannot be stopped: over time is only noted

    def normalize(self, raw):
        # what the API leaves out means what create_instance did by default
        params = {"template": "", "images": "", "firmware": "", "net_model": "virtio", "add_cdrom": "sata",
                  "add_input": "default", "meta_prealloc": False, "hdd_size": 0}
        params.update({k: v for k, v in raw.items() if k not in ("submission_key", "create", "csrfmiddlewaretoken")})
        params.update(compute=int(raw["compute"]), devices=[list(d) for d in raw.get("devices", [])])
        if "UEFI" in params["firmware"] and "secboot" in params["firmware"].partition(":")[2]:
            params["machine"] = "q35"  # a secure boot loader needs SMM, on q35 only
        return params

    def validate(self, user, params):
        from instances.views import CDROM_BUSES

        if not user.is_superuser:
            raise PermissionDenied
        data = params
        c = Compute.objects.get(pk=data["compute"])
        conn = wvmCreate(c.hostname, c.login, c.password, c.type)
        cdrom_buses = [bus for bus in conn.get_disk_bus_types(data["arch"], data["machine"]) if bus in CDROM_BUSES]
        if data["name"] in conn.get_instances():
            raise util.OperationError(_("A virtual machine with this name already exists"))
        if Instance.objects.filter(name__exact=data["name"]):
            raise util.OperationError(_("There is an instance with same name. Remove it and try again!"))
        if not (data["hdd_size"] or data["template"] or data["images"]):
            raise util.OperationError(_("First you need to create or select an image"))
        if not data["hdd_size"] and data["template"] and conn.get_volume_path(data["name"] + ".img", data["storage"]):
            raise util.OperationError(_("Image has already exist. Please check volumes or change instance name"))
        if data["cache_mode"] not in conn.get_cache_modes():
            raise util.OperationError(_("Invalid cache mode"))
        if data["add_cdrom"] != "None" and data["add_cdrom"] not in cdrom_buses:
            raise util.OperationError(_("A CD-ROM cannot use the %(bus)s bus") % {"bus": data["add_cdrom"]})
        try:
            utils.nic_macs(data["mac"], data["networks"])
        except ValueError as err:
            raise util.OperationError(str(err))
        if "UEFI" in data["firmware"] and ":" not in data["firmware"]:
            raise util.OperationError(_("Invalid firmware"))

    def plan(self, user, params):
        compute = Compute.objects.get(pk=params["compute"])
        return Plan(claims=vm_claims(compute, params["name"]), resource=claims.compute_key(compute.pk),
                    label=f"{compute.name}/{params['name']}",
                    intent={"compute": compute.pk, "uuid": util.randomUUID(), "name": params["name"]})

    def run(self, op, ctx):
        data = op.params
        c = Compute.objects.get(pk=data["compute"])
        conn = wvmCreate(c.hostname, c.login, c.password, c.type)
        meta_prealloc = bool(data["meta_prealloc"])
        default_bus = app_settings.INSTANCE_VOLUME_DEFAULT_BUS
        default_disk_format = app_settings.INSTANCE_VOLUME_DEFAULT_FORMAT
        default_disk_owner_uid = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_UID)
        default_disk_owner_gid = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_GID)

        firmware = dict()
        if "UEFI" in data["firmware"]:
            firmware["loader"] = data["firmware"].split(":")[1].strip()
            firmware["secure"] = "yes" if "secboot" in firmware["loader"] else "no"
            firmware["readonly"] = "yes"
            firmware["type"] = "pflash"

        def volume(path, device="disk", bus=default_bus):
            return {
                "path": path,
                "type": conn.get_volume_format_type(path),
                "device": device,
                "bus": bus,
                "scsi_model": app_settings.INSTANCE_VOLUME_DEFAULT_SCSI_CONTROLLER if bus == "scsi" else None,
                "cache_mode": data["cache_mode"],
                "discard_mode": app_settings.INSTANCE_VOLUME_DEFAULT_DISCARD,
                "detect_zeroes_mode": app_settings.INSTANCE_VOLUME_DEFAULT_DETECT_ZEROES,
                "io_mode": app_settings.INSTANCE_VOLUME_DEFAULT_IO,
            }

        ctx.record_intent()
        ctx.set_phase(gettext_noop("Copying disks"))
        created = []  # volumes this job allocated
        volume_list = []
        try:
            if data["hdd_size"]:
                path = conn.create_volume(data["storage"], data["name"], data["hdd_size"], default_disk_format,
                                          meta_prealloc, default_disk_owner_uid, default_disk_owner_gid)
                created.append(path)
                volume_list.append(volume(path))
            elif data["template"]:
                path = conn.clone_from_template(data["name"], conn.get_volume_path(data["template"]), data["storage"],
                                                meta_prealloc, default_disk_owner_uid, default_disk_owner_gid)
                created.append(path)
                volume_list.append(volume(path))
            else:
                for vol, (device, bus) in zip(data["images"].split(","), data["devices"]):
                    volume_list.append(volume(conn.get_volume_path(vol), device, bus))

            ctx.set_phase(gettext_noop("Defining the VM"))
            conn.create_instance(
                name=data["name"],
                memory=data["memory"],
                vcpu=data["vcpu"],
                vcpu_mode=data["vcpu_mode"],
                uuid=op.intent["uuid"],
                arch=data["arch"],
                machine=data["machine"],
                firmware=firmware,
                volumes=volume_list,
                networks=data["networks"],
                virtio=data["virtio"],
                listener_addr=data["listener_addr"],
                nwfilter=data["nwfilter"],
                net_model="virtio" if data["net_model"] == "default" else data["net_model"],
                video=data["video"],
                console_pass="",
                mac=data["mac"],
                qemu_ga=data["qemu_ga"],
                add_cdrom=data["add_cdrom"],
                add_input=data["add_input"],
            )
        except Exception as err:
            # the VM is not defined: its new volumes go
            leftovers = []
            for path in created:
                try:
                    conn.delete_volume(path)
                except libvirtError:
                    leftovers.append(path)
            if leftovers:
                raise util.OperationError(f"{err}. Remove these volumes by hand: {', '.join(leftovers)}") from err
            raise
        record_vm(op, owner=False)
        return {}

    def reconcile(self, op):
        data = op.params
        names = [data["name"] + ext for ext in ("", ".img", ".qcow", ".qcow2")]
        return vm_outcome(op, owner=False, volumes=names if data["hdd_size"] or data["template"] else [])

    def log_entry(self, op, state):
        compute, _sep, name = op.resource_label.rpartition("/")
        message = "Instance is created"
        return compute, name, message if state == Operation.SUCCEEDED else f"{message} ({state})"
