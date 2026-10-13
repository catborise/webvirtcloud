"""Kinds used by the operations tests; registered on import. test.scripted
takes its behaviour from params and from barriers that tests install before
submitting (keyed by submission key)."""

from operations.jobs import Cancelled, Kind, Outcome, Plan, register
from operations.models import Claim, Operation

BARRIERS = {}  # submission key -> threading.Event the run waits on
CANCELLED = []


def reset():
    BARRIERS.clear()
    CANCELLED.clear()


@register
class NoopKind(Kind):
    name = "test.noop"
    label = "No-op"

    def normalize(self, raw):
        return {"uuid": str(raw.get("uuid", "u1"))}

    def plan(self, user, params):
        key = "instance:" + params["uuid"]
        return Plan(claims={key: Claim.EXCLUSIVE}, resource=key, label="c/vm")

    def run(self, op, ctx):
        ctx.record_intent(step="effect")
        return {"done": True}

    def reconcile(self, op):
        return Outcome(Operation.SUCCEEDED, result={"recovered": True})

    def log_entry(self, op, state):
        return ("c", "vm", f"No-op {state}")


@register
class ScriptedKind(NoopKind):
    """params: fail_before, fail_after, outcome (what reconcile returns)."""

    name = "test.scripted"
    label = "Scripted"
    cancellable = True

    def normalize(self, raw):
        return {"uuid": str(raw.get("uuid", "u1")), "fail_before": bool(raw.get("fail_before")),
                "fail_after": bool(raw.get("fail_after")), "outcome": str(raw.get("outcome", "uncertain"))}

    def run(self, op, ctx):
        if op.params["fail_before"]:
            raise RuntimeError("refused before effects")
        ctx.record_intent(step="effect")
        if op.params["fail_after"]:
            raise RuntimeError("failed after effects")
        barrier = BARRIERS.get(op.submission_key)
        if barrier is not None:
            barrier.wait(30)
            if str(op.id) in CANCELLED:
                raise Cancelled()
        return {"ran": True}

    def cancel(self, op):
        CANCELLED.append(str(op.id))
        barrier = BARRIERS.get(op.submission_key)
        if barrier is not None:
            barrier.set()

    def reconcile(self, op):
        return Outcome(op.params["outcome"], error="scripted")
