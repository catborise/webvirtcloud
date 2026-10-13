import uuid

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def submission_key():
    """A new key per rendered form: a resubmitted form returns its job."""
    return format_html('<input type="hidden" name="submission_key" value="{}">', uuid.uuid4().hex)
