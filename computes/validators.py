import re
from ipaddress import ip_address

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

have_symbol = re.compile("[^a-zA-Z0-9._-]+")
wrong_ip = re.compile("^0.|^255.")
wrong_name = re.compile("[^a-zA-Z0-9._-]+")


def validate_hostname(value):
    sym = have_symbol.match(value)
    wip = wrong_ip.match(value)

    if sym:
        raise ValidationError(
            _('Hostname must contain only numbers, or the domain name separated by "."')
        )
    elif wip:
        raise ValidationError(_("Wrong IP address"))


def validate_name(value):
    have_symbol = wrong_name.match("[^a-zA-Z0-9._-]+")
    if have_symbol:
        raise ValidationError(_("The hostname must not contain any special characters"))


_hostname = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?")


def validate_migration_address(value):
    """An IP address (no IPv6 scope) or a host name: no scheme, port or brackets."""
    try:
        if not getattr(ip_address(value), "scope_id", None):
            return
    except ValueError:
        if _hostname.fullmatch(value):
            return
    raise ValidationError(_("Enter an IP address or a host name, without a scheme or port"))
