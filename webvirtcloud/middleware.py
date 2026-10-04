from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from libvirt import libvirtError

API_PREFIX = "/api/"


class ExceptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        # API clients get JSON with a failure status, not the HTML error page.
        if request.path_info.startswith(API_PREFIX):
            if isinstance(exception, libvirtError):
                return JsonResponse({"detail": str(exception)}, status=400)
            if isinstance(exception, TimeoutError):
                return JsonResponse({"detail": "Operation is busy. Please retry."}, status=409)
            return None
        if isinstance(exception, libvirtError):
            messages.error(
                request,
                _("libvirt Error - %(exception)s") % {"exception": exception},
            )
            return render(request, "500.html", {"libvirt_error": True}, status=500)
            # TODO: check connecting to host via VPN
