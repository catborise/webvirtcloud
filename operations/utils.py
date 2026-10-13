"""Secrets never reach a job record, a response or a log line."""

import re

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
