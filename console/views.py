# pylint: disable=no-name-in-module,no-member
import re

from accounts.models import UserInstance
from appsettings.settings import app_settings
from django.conf import settings
from django.http.response import HttpResponseServerError
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from instances.models import Instance
from libvirt import libvirtError
from vrtManager.instance import wvmInstance


def to_bool(val, default=False):
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes")


def console(request):
    """
    :param request:
    :return:
    """
    console_error = None
    token = ""
    view_type = "lite"
    view_only = to_bool(app_settings.CONSOLE_VIEW_ONLY, default=False)
    scale = to_bool(app_settings.CONSOLE_SCALE, default=False)
    resize_session = to_bool(app_settings.CONSOLE_RESIZE_SESSION, default=False)
    clip_viewport = to_bool(app_settings.CONSOLE_CLIP_VIEWPORT, default=False)

    if request.method == "GET":
        token = request.GET.get("token", token)
        view_type = (
            "full"
            if str(request.GET.get("view", view_type)).strip().lower() == "full"
            else "lite"
        )
        if "view_only" in request.GET:
            view_only = to_bool(request.GET.get("view_only"), default=view_only)
        if "scale" in request.GET:
            scale = to_bool(request.GET.get("scale"), default=scale)
        if "resize_session" in request.GET:
            resize_session = to_bool(
                request.GET.get("resize_session"), default=resize_session
            )
        if "clip_viewport" in request.GET:
            clip_viewport = to_bool(
                request.GET.get("clip_viewport"), default=clip_viewport
            )

    try:
        temptoken = token.split("-", 1)
        host = int(temptoken[0])
        uuid = temptoken[1]

        if not request.user.is_superuser and not request.user.has_perm(
            "instances.view_instances"
        ):
            try:
                userInstance = UserInstance.objects.get(
                    instance__compute_id=host,
                    instance__uuid=uuid,
                    user__id=request.user.id,
                )
                instance = Instance.objects.get(compute_id=host, uuid=uuid)
            except UserInstance.DoesNotExist:
                instance = None
                console_error = _(
                    "User does not have permission to access console or host/instance not exist"
                )
                return HttpResponseServerError(console_error)
        else:
            instance = Instance.objects.get(compute_id=host, uuid=uuid)

        conn = wvmInstance(
            instance.compute.hostname,
            instance.compute.login,
            instance.compute.password,
            instance.compute.type,
            instance.name,
        )
        console_type = conn.get_console_type()
        console_websocket_port = conn.get_console_websocket_port()
        console_passwd = conn.get_console_passwd()
    except (libvirtError, ValueError, IndexError, Instance.DoesNotExist):
        console_type = None
        console_websocket_port = None
        console_passwd = None

    ws_public_port = getattr(settings, "WS_PUBLIC_PORT", 6080)
    ws_public_host = getattr(settings, "WS_PUBLIC_HOST", None)
    ws_public_path = getattr(settings, "WS_PUBLIC_PATH", "/")

    ws_port = console_websocket_port if console_websocket_port else ws_public_port
    ws_host = ws_public_host if ws_public_host else request.get_host()
    ws_path = ws_public_path if ws_public_path else "/"

    if ":" in ws_host:
        ws_host = re.sub(":[0-9]+", "", ws_host)

    if ws_path:
        ws_path = ws_path.strip("/") + "/" if ws_path.strip("/") else ""

    if console_type == "vnc" or console_type == "spice":
        console_page = "console-" + console_type + "-" + view_type + ".html"
        response = render(request, console_page, locals())
    elif console_type == "pty":
        socketio_public_host = getattr(settings, "SOCKETIO_PUBLIC_HOST", None)
        socketio_public_port = getattr(settings, "SOCKETIO_PUBLIC_PORT", 6081)
        socketio_public_path = getattr(settings, "SOCKETIO_PUBLIC_PATH", "socket.io/")

        socketio_host = (
            socketio_public_host if socketio_public_host else request.get_host()
        )
        socketio_port = socketio_public_port if socketio_public_port else 6081
        socketio_path = socketio_public_path if socketio_public_path else "/"

        if ":" in socketio_host:
            socketio_host = re.sub(":[0-9]+", "", socketio_host)

        response = render(request, "console-xterm.html", locals())
    else:
        if console_type is None:
            console_error = _(
                "Fail to get console. Please check the console configuration of your VM."
            )
        else:
            console_error = _("Console type '%(type)s' has not support") % {
                "type": console_type
            }
        response = render(request, "console-vnc-lite.html", locals())

    response.set_cookie("token", token)
    return response
