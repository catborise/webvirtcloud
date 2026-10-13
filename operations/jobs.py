"""The contract every kind of job follows (spec section 5), the kinds by
name (filled by each app's jobs.py via register) and the context a running
job reports through."""

from dataclasses import dataclass, field

from . import utils
from .models import LeaseLost, Operation, fenced

KINDS = {}


@dataclass
class Plan:
    claims: dict
    resource: str
    label: str
    intent: dict = field(default_factory=dict)


@dataclass
class Outcome:
    """What a run or an inspection decided. RUNNING: libvirt still works;
    stalled: its work reports nothing left to do but does not end;
    progress: (done, total) of that work, when libvirt tells."""

    state: str
    result: dict = field(default_factory=dict)
    error: str = ""
    phase: str = ""
    stalled: bool = False
    progress: tuple | None = None


class Cancelled(Exception):
    """Raised by a kind's run when a cancel stopped its work."""


def get_kind(name):
    return KINDS[name]


def label_of(name):
    kind = KINDS.get(name)
    return kind.label if kind is not None else name


def register(cls):
    KINDS[cls.name] = cls()
    return cls


class Kind:
    name = ""
    label = ""
    cancellable = False
    time_limit = None
    progress_bytes = False  # progress counts bytes: the job page shows sizes

    def normalize(self, raw):
        """Plain, allowlisted values from the request, without libvirt."""
        raise NotImplementedError

    def validate(self, user, params):
        """In the request: permissions and libvirt state. Raise to refuse."""

    def plan(self, user, params):
        raise NotImplementedError

    def precheck(self, op, ctx):
        """In the worker, before any effect: permission and state again."""

    def run(self, op, ctx):
        raise NotImplementedError

    def progress(self, op):
        return None

    def cancel(self, op):
        raise NotImplementedError

    def reconcile(self, op):
        raise NotImplementedError

    def log_entry(self, op, state):
        """(host, instance, message) of the Logs row written with each end of
        the job (in the same transaction); its host and instance also go on
        the row of a resolution. None: no row."""
        return None


class Context:
    def __init__(self, op, worker):
        self.op = op
        self.worker = worker
        self.effects_started = False

    def _write(self, **fields):
        with fenced(self.worker.identity):
            if not Operation.objects.filter(pk=self.op.pk, worker=self.worker.identity).update(**fields):
                raise LeaseLost("the job is no longer this worker's")
        for name, value in fields.items():
            setattr(self.op, name, value)

    def record_intent(self, **values):
        """Commit what run is about to change; the first libvirt effect may
        follow only after this returns."""
        self._write(intent={**self.op.intent, **values}, effects_started=True)
        self.effects_started = True

    def set_phase(self, text):
        self._write(phase=utils.safe_text(text)[:255])
