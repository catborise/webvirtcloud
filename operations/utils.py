"""Secrets never reach a job record, a response or a log line. What a refused
request says about the job holding the resource: details for its owner and
superusers, a minimal summary for everyone else."""

import re

from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.translation import gettext as _

from . import jobs

_NAMES = r"(?:pass|secret|token|key)\w*"
_PATTERNS = [
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    # quoted values, also XML attributes: passwd='x', password="x"
    (re.compile(rf"""({_NAMES}['"]?\s*[:=]\s*)(['"])(?:\\.|(?!\2)[^\\])*\\?(?:\2|\Z)""", re.I | re.S), r"\1\2***\2"),
    (re.compile(rf"""({_NAMES}['"]?\s*[:=]\s*)[^\s'",;<>]+""", re.I), r"\1***"),
    # <secret ...>value</secret>-like elements
    (re.compile(r"(<(secret|passphrase)\b[^>]*>).*?(</\2>)", re.I | re.S), r"\1***\3"),
]
_SECRET_KEY = re.compile(r"pass|secret|token|private|key$", re.I)


def safe_text(text):
    text = str(text)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def check_params(params):
    if isinstance(params, dict):
        for key, value in params.items():
            if _SECRET_KEY.search(str(key)):
                raise ValueError(f"A job cannot store the field {key}")
            check_params(value)
    elif isinstance(params, (list, tuple)):
        for value in params:
            check_params(value)


def may_see(op, user):
    return user.is_superuser or (op.user_id is not None and op.user_id == user.pk)


def summary(op, user):
    base = {"kind": op.kind, "label": str(_(jobs.label_of(op.kind))), "state": op.state, "since": op.created_at}
    if may_see(op, user):
        base.update({"id": str(op.id), "user": op.username})
    return base


def busy_message(busy, user):
    op = busy.blocker
    if op is None:
        return _("A short operation is in progress on this resource; try again in a moment.")
    values = {"label": _(jobs.label_of(op.kind)), "state": op.state, "since": timesince(op.created_at, timezone.now())}
    if may_see(op, user):
        return _("%(label)s is %(state)s on this resource (started by %(user)s, %(since)s ago); "
                 "see the Jobs page.") % {**values, "user": op.username}
    return _("%(label)s is %(state)s on this resource (since %(since)s).") % values
