"""Kinds of jobs by name (filled by each app's jobs.py via register)."""

KINDS = {}


def get_kind(name):
    return KINDS[name]


def label_of(name):
    kind = KINDS.get(name)
    return kind.label if kind is not None else name
