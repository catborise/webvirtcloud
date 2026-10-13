"""Jobs of the instances app (operations framework, spec section 5)."""

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_noop
from libvirt import VIR_DOMAIN_SNAPSHOT_LIST_EXTERNAL, VIR_ERR_NO_DOMAIN, libvirtError
from vrtManager import util
from vrtManager.instance import wvmInstance

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
