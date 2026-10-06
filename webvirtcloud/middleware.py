from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
import libvirt
from libvirt import libvirtError
from vrtManager.util import ConnectionFailed

API_PREFIX = "/api/"
# errors of the connection to the host, not of the operation: both the
# domain and the code must match (a daemon that lacks a procedure answers
# VIR_FROM_RPC / VIR_ERR_NO_SUPPORT, which the user should see)
TRANSPORT_ERROR_DOMAINS = {libvirt.VIR_FROM_RPC, libvirt.VIR_FROM_REMOTE, libvirt.VIR_FROM_SSH, libvirt.VIR_FROM_LIBSSH}
TRANSPORT_ERROR_CODES = {
    libvirt.VIR_ERR_INTERNAL_ERROR,  # "connection closed due to keepalive timeout"
    libvirt.VIR_ERR_SYSTEM_ERROR,  # "Cannot recv data: ..."
    libvirt.VIR_ERR_RPC,
    libvirt.VIR_ERR_NO_CONNECT,
    libvirt.VIR_ERR_AUTH_FAILED,
    libvirt.VIR_ERR_AUTH_UNAVAILABLE,
    libvirt.VIR_ERR_AUTH_CANCELLED,
    libvirt.VIR_ERR_SSH,
    libvirt.VIR_ERR_LIBSSH,
}
UNREACHABLE = _("The host of this virtual machine cannot be reached right now.")


def error_text(request, exception):
    """
    The libvirt error to show. A connection error names the host and how it
    is reached, which only administrators (superusers and the global
    view_instances permission, who see the hosts) get; other users learn
    that the host cannot be reached.
    """
    connection_error = isinstance(exception, ConnectionFailed) or (
        isinstance(exception, libvirtError)
        and exception.get_error_domain() in TRANSPORT_ERROR_DOMAINS
        and exception.get_error_code() in TRANSPORT_ERROR_CODES
    )
    user = getattr(request, "user", None)
    if connection_error and not (user and (user.is_superuser or user.has_perm("instances.view_instances"))):
        return str(UNREACHABLE)
    return str(exception)


class ExceptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        # API clients get JSON with a failure status, not the HTML error page.
        if request.path_info.startswith(API_PREFIX):
            if isinstance(exception, libvirtError):
                return JsonResponse({"detail": error_text(request, exception)}, status=400)
            if isinstance(exception, TimeoutError):
                return JsonResponse({"detail": "Operation is busy. Please retry."}, status=409)
            return None
        if isinstance(exception, libvirtError):
            messages.error(
                request,
                _("libvirt Error - %(exception)s") % {"exception": error_text(request, exception)},
            )
            return render(request, "500.html", {"libvirt_error": True}, status=500)
            # TODO: check connecting to host via VPN
