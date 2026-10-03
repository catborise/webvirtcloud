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
