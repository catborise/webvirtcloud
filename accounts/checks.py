from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security)
def password_change_middleware_check(app_configs, **kwargs):
    middleware = list(settings.MIDDLEWARE)
    required = "accounts.middleware.ForcePasswordChangeMiddleware"
    authentication = "django.contrib.auth.middleware.AuthenticationMiddleware"
    if required not in middleware or (
        authentication in middleware
        and middleware.index(required) < middleware.index(authentication)
    ):
        return [
            Error(
                "ForcePasswordChangeMiddleware must follow AuthenticationMiddleware.",
                hint=f"Add '{required}' to MIDDLEWARE in webvirtcloud/settings.py; see README.md.",
                id="accounts.E001",
            )
        ]
    return []


@register(Tags.security)
def login_required_middleware_check(app_configs, **kwargs):
    middleware = list(settings.MIDDLEWARE)
    required = "django.contrib.auth.middleware.LoginRequiredMiddleware"
    old = "login_required.middleware.LoginRequiredMiddleware"
    if old in middleware or required not in middleware:
        return [
            Error(
                "MIDDLEWARE must use Django's LoginRequiredMiddleware.",
                hint=f"In webvirtcloud/settings.py replace '{old}' with '{required}' "
                "and delete LOGIN_REQUIRED_IGNORE_VIEW_NAMES; see README.md.",
                id="accounts.E002",
            )
        ]
    return []
