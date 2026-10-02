from django.shortcuts import redirect
from django.urls import Resolver404, resolve

ALLOWED_VIEWS = {"accounts:change_password", "accounts:logout", "accounts:login"}


class ForcePasswordChangeMiddleware:
    """Send users who still have the generated admin password to the change form."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.get("must_change_password"):
            try:
                view_name = resolve(request.path_info).view_name
            except Resolver404:
                view_name = None
            if view_name not in ALLOWED_VIEWS:
                return redirect("accounts:change_password")
        return self.get_response(request)
