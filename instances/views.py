try:
    import crypt_r as crypt
except ImportError:
    import crypt
import functools
import json
import os
import re
import socket
import subprocess
import time
from bisect import insort

from accounts.models import UserInstance, UserSSHKey
from admin.decorators import superuser_only
from appsettings.models import AppSettings
from appsettings.settings import app_settings
from computes.models import Compute
from computes.utils import libvirt_compute_lock, libvirt_instance_lock, user_quota_lock
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.db.models import prefetch_related_objects
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_noop as _
from django.views.decorators.http import require_POST
from libvirt import (VIR_DOMAIN_UNDEFINE_KEEP_NVRAM,
                     VIR_DOMAIN_UNDEFINE_MANAGED_SAVE,
                     VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA,
                     VIR_DOMAIN_UNDEFINE_NVRAM,
                     VIR_DOMAIN_START_PAUSED,
                     libvirtError)
from logs.views import addlogmsg
from vrtManager import util
from vrtManager.create import wvmCreate
from vrtManager.instance import wvmInstances
from vrtManager.interface import wvmInterface
from vrtManager.storage import wvmStorage
from vrtManager.util import randomPasswd

from instances.models import Instance

from . import utils
from .forms import ConsoleForm, FlavorForm, NewVMForm
from .models import Flavor


def index(request):
    instances = None

    computes = list(Compute.objects.all().order_by("name"))
    for compute in computes:
        utils.refr(compute)
    prefetch_related_objects(computes, "instance_set__userinstance_set")

    if request.user.is_superuser or request.user.has_perm("instances.view_instances"):
        instances = Instance.objects.all().prefetch_related("userinstance_set")
    else:
        instances = Instance.objects.filter(
            userinstance__user=request.user
        ).prefetch_related("userinstance_set")

    return render(
        request, "allinstances.html", {"computes": computes, "instances": instances}
    )


def instance(request, pk):
    instance: Instance = get_instance(request.user, pk)
    compute: Compute = instance.compute
    computes = Compute.objects.all().order_by("name")
    computes_count = computes.count()
    users = User.objects.all().order_by("username")
    publickeys = UserSSHKey.objects.filter(user_id=request.user.id)
    keymaps = settings.QEMU_KEYMAPS
    # The console form renders the VNC password, so only users who may manage
    # console settings get the form (and the password) at all.
    can_manage_console = utils.can_manage_console(request.user, instance)
    console_form = None
    if can_manage_console:
        console_form = ConsoleForm(
            initial={
                "listen_on": instance.console_listener_address,
                "password": instance.console_passwd,
                "keymap": instance.console_keymap,
            }
        )
    console_listener_addresses = settings.QEMU_CONSOLE_LISTENER_ADDRESSES
    bottom_bar = app_settings.VIEW_INSTANCE_DETAIL_BOTTOM_BAR
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )
    try:
        userinstance = UserInstance.objects.get(
            instance__compute_id=compute.id,
            instance__name=instance.name,
            user__id=request.user.id,
        )
    except UserInstance.DoesNotExist:
        userinstance = None

    memory_range = [256, 512, 768, 1024, 2048, 3072, 4096, 6144, 8192, 16384]
    if instance.memory not in memory_range:
        insort(memory_range, instance.memory)
    if instance.cur_memory not in memory_range:
        insort(memory_range, instance.cur_memory)
    clone_free_names = utils.get_clone_free_names()
    user_quota_msg = utils.check_user_quota(request.user, 0, 0, 0, 0)

    default_bus = app_settings.INSTANCE_VOLUME_DEFAULT_BUS
    default_io = app_settings.INSTANCE_VOLUME_DEFAULT_IO
    default_discard = app_settings.INSTANCE_VOLUME_DEFAULT_DISCARD
    default_zeroes = app_settings.INSTANCE_VOLUME_DEFAULT_DETECT_ZEROES
    default_cache = app_settings.INSTANCE_VOLUME_DEFAULT_CACHE
    default_format = app_settings.INSTANCE_VOLUME_DEFAULT_FORMAT
    # default_disk_owner_uid = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_UID)
    # default_disk_owner_gid = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_GID)

    # clone_instance_auto_name = app_settings.CLONE_INSTANCE_AUTO_NAME

    # try:
    #     instance = Instance.objects.get(compute=compute, name=vname)
    #     if instance.uuid != uuid:
    #         instance.uuid = uuid
    #         instance.save()
    #         msg = _(f"Fixing UUID {uuid}")
    #         addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    # except Instance.DoesNotExist:
    #     instance = Instance(compute=compute, name=vname, uuid=uuid)
    #     instance.save()
    #     msg = _("Instance does not exist: Creating new instance")
    #     addlogmsg(request.user.username, instance.compute.name, instance.name, msg)

    # userinstances = UserInstance.objects.filter(instance=instance).order_by('user__username')
    userinstances = instance.userinstance_set.order_by("user__username")
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )

    # Host resources
    vcpu_host = len(instance.vcpu_range)
    memory_host = instance.proxy.get_max_memory()
    bus_host = instance.proxy.get_disk_bus_types(instance.arch, instance.machine)
    networks_host = sorted(instance.proxy.get_networks())
    interfaces_host = sorted(instance.proxy.get_ifaces())
    nwfilters_host = instance.proxy.get_nwfilters()
    storages_host = sorted(instance.proxy.get_storages(True))
    net_models_host = instance.proxy.get_network_models()

    if app_settings.VM_DRBD_STATUS == "True":
        instance.drbd = drbd_status(request, pk)
        instance.save()

    return render(request, "instance.html", locals(),)


def status(request, pk):
    instance = get_instance(request.user, pk)
    return JsonResponse({"status": instance.proxy.get_status()})


def drbd_status(request, pk):
    instance = get_instance(request.user, pk)
    result = "None DRBD"

    if instance.compute.type == 2:
        conn = instance.compute.login + "@" + instance.compute.hostname
        remoteDrbdStatus = subprocess.run(
            ["ssh", conn, "sudo", "/usr/sbin/drbdadm", "status", "&&", "exit"],
            stdout=subprocess.PIPE,
            text=True,
        )

        if remoteDrbdStatus.stdout:
            try:
                instanceFindDrbd = re.compile(
                    instance.name + "[_]*[A-Z]* role:(.+?)\n  disk:(.+?)\n",
                    re.IGNORECASE,
                )
                instanceDrbd = instanceFindDrbd.findall(remoteDrbdStatus.stdout)

                primaryCount = 0
                secondaryCount = 0
                statusDisk = "OK"

                for disk in instanceDrbd:
                    if disk[0] == "Primary":
                        primaryCount = primaryCount + 1
                    elif disk[0] == "Secondary":
                        secondaryCount = secondaryCount + 1
                    if disk[1] != "UpToDate":
                        statusDisk = "NOK"

                if primaryCount > 0 and secondaryCount > 0:
                    statusRole = "NOK"
                else:
                    if primaryCount > secondaryCount:
                        statusRole = "Primary"
                    else:
                        statusRole = "Secondary"

                result = statusRole + "/" + statusDisk

            except:
                print("Error to get drbd role and status")

    return result


def stats(request, pk):
    instance = get_instance(request.user, pk)
    json_blk = []
    json_net = []

    # TODO: stats are inaccurate
    cpu_usage = instance.proxy.cpu_usage()
    mem_usage = instance.proxy.mem_usage()
    blk_usage = instance.proxy.disk_usage()
    net_usage = instance.proxy.net_usage()

    current_time = time.strftime("%H:%M:%S")
    for blk in blk_usage:
        json_blk.append(
            {
                "dev": blk["dev"],
                "data": [int(blk["rd"]) / 1048576, int(blk["wr"]) / 1048576],
            }
        )

    for net in net_usage:
        json_net.append(
            {
                "dev": net["dev"],
                "data": [int(net["rx"]) / 1048576, int(net["tx"]) / 1048576],
            }
        )

    return JsonResponse(
        {
            "cpudata": int(cpu_usage["cpu"]),
            "memdata": mem_usage,
            "blkdata": json_blk,
            "netdata": json_net,
            "timeline": current_time,
        }
    )


def osinfo(request, pk):
    instance = get_instance(request.user, pk)
    results = instance.proxy.osinfo()

    return JsonResponse(results)


@superuser_only
def guess_mac_address(request, vname):
    data = {"vname": vname}
    mac = utils.get_dhcp_mac_address(vname)
    if not mac:
        mac = utils.get_random_mac_address()
    data["mac"] = mac
    return JsonResponse(data)


@superuser_only
def random_mac_address(request):
    data = dict()
    data["mac"] = utils.get_random_mac_address()
    return JsonResponse(data)


@superuser_only
def guess_clone_name(request):
    dhcp_file = "/srv/webvirtcloud/dhcpd.conf"
    prefix = app_settings.CLONE_INSTANCE_DEFAULT_PREFIX
    if os.path.isfile(dhcp_file):
        instance_names = [
            i.name for i in Instance.objects.filter(name__startswith=prefix)
        ]
        with open(dhcp_file, "r") as f:
            for line in f:
                line = line.strip()
                if f"host {prefix}" in line:
                    fqdn = line.split(" ")[1]
                    hostname = fqdn.split(".")[0]
                    if hostname.startswith(prefix) and hostname not in instance_names:
                        return JsonResponse({"name": hostname})
    return JsonResponse({})


def sshkeys(request, pk):
    """
    :param request:
    :param vname:
    :return:
    """
    instance = get_instance(request.user, pk)
    instance_keys = []
    userinstances = UserInstance.objects.filter(instance=instance)

    for ui in userinstances:
        keys = UserSSHKey.objects.filter(user=ui.user)
        for k in keys:
            instance_keys.append(k.keypublic)
    if request.GET.get("plain", ""):
        response = "\n".join(instance_keys)
        response += "\n"
        return HttpResponse(response, content_type="text/plain; charset=utf-8")
    return JsonResponse(instance_keys, safe=False)


def _same_origin_referer(request, default):
    """The Referer without its fragment if it is on this site, else default."""
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        url=referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer.split("#")[0]
    return default


def get_safe_redirect(request, default=None):
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        url=referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referer)
    if default:
        return redirect(default)
    return redirect("instances:index")


def get_instance(user, pk, perm_type="view"):
    """
    Check that instance is available for user, if not raise 404 or PermissionDenied.
    perm_type:
      - 'view': superuser, has_perm("instances.view_instances"), or UserInstance owner
      - 'power': superuser or UserInstance owner
      - 'change': superuser or (UserInstance owner and is_change)
      - 'delete': superuser or (UserInstance owner and is_delete)
    """
    valid_perms = {"view", "power", "change", "delete"}
    if perm_type not in valid_perms:
        raise PermissionDenied

    instance = get_object_or_404(Instance, pk=pk)
    user_instances = user.userinstance_set.filter(instance=instance)
    has_owner_rel = user_instances.exists()

    if not (
        user.is_superuser
        or user.has_perm("instances.view_instances")
        or has_owner_rel
    ):
        raise Http404()

    if user.is_superuser or perm_type == "view":
        return instance

    user_inst = user_instances.first()
    if perm_type == "power":
        if not has_owner_rel:
            raise PermissionDenied
    elif perm_type == "change":
        if not (user_inst and user_inst.is_change):
            raise PermissionDenied
    elif perm_type == "delete":
        if not (user_inst and user_inst.is_delete):
            raise PermissionDenied
    else:
        raise PermissionDenied

    return instance


def _busy(request, pk):
    if hasattr(request, "accepted_renderer"):
        return JsonResponse({"detail": "Instance operation is busy. Please retry."}, status=409)
    messages.error(request, _("Instance operation is busy. Please retry."))
    return get_safe_redirect(request, default=reverse("instances:instance", args=[pk]))


def serialize_instance_mutation(func):
    """Serialize one VM, after checking visibility, without blocking sibling VMs."""
    @functools.wraps(func)
    def wrapper(request, pk, *args, **kwargs):
        inst = get_instance(request.user, pk)
        try:
            with libvirt_instance_lock(inst):
                return func(request, pk, *args, **kwargs)
        except TimeoutError:
            return _busy(request, pk)

    return wrapper


def serialize_user_quota(func):
    """Outermost lock for quota-checked changes: concurrent requests of one
    user (also on different computes) must not pass the check together."""
    @functools.wraps(func)
    def wrapper(request, pk, *args, **kwargs):
        if request.user.is_superuser:  # quotas do not apply
            return func(request, pk, *args, **kwargs)
        try:
            with user_quota_lock(request.user):
                return func(request, pk, *args, **kwargs)
        except TimeoutError:
            return _busy(request, pk)

    return wrapper


def serialize_compute_mutation(func):
    """Hold the whole compute: disk deletions must not race another VM attaching that disk."""
    @functools.wraps(func)
    def wrapper(request, pk, *args, **kwargs):
        inst = get_instance(request.user, pk)
        if request.method in ("GET", "HEAD"):  # e.g. the destroy confirmation page
            return func(request, pk, *args, **kwargs)
        try:
            with libvirt_compute_lock(inst.compute):
                return func(request, pk, *args, **kwargs)
        except TimeoutError:
            return _busy(request, pk)

    return wrapper


DISK_FORMAT_RE = re.compile(r"^[a-z0-9]+$")
DISK_SERIAL_RE = re.compile(r"^[A-Za-z0-9_.+-]*$")
VOLUME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")


def invalid_disk_options(
    instance,
    bus=None,
    cache=None,
    io=None,
    discard=None,
    zeroes=None,
    format=None,
    serial=None,
):
    """
    Return the names of the given disk options whose values are not allowed.
    These values end up in libvirt disk XML, so they must come from the
    hypervisor's own lists (or match a strict pattern). None means "not given".
    """
    proxy = instance.proxy
    checks = {
        "bus": (
            bus,
            lambda v: v in proxy.get_disk_bus_types(instance.arch, instance.machine),
        ),
        "cache": (cache, lambda v: v in proxy.get_cache_modes()),
        "io": (io, lambda v: v in proxy.get_io_modes()),
        "discard": (discard, lambda v: v in proxy.get_discard_modes()),
        "detect_zeroes": (zeroes, lambda v: v in proxy.get_detect_zeroes_modes()),
        "format": (format, lambda v: bool(DISK_FORMAT_RE.fullmatch(v))),
        "serial": (serial, lambda v: bool(DISK_SERIAL_RE.fullmatch(v))),
    }
    return [
        name
        for name, (value, is_valid) in checks.items()
        if value is not None and not is_valid(value)
    ]


def reject_disk_options(request, invalid):
    messages.error(
        request,
        _("Invalid disk options: %(options)s") % {"options": ", ".join(invalid)},
    )
    return get_safe_redirect(request)


@require_POST
@serialize_instance_mutation
def poweron(request, pk):
    instance = get_instance(request.user, pk, perm_type="power")
    if instance.is_template:
        messages.warning(request, _("Templates cannot be started."))
    else:
        instance.proxy.start()
        addlogmsg(
            request.user.username, instance.compute.name, instance.name, _("Power On")
        )

    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id])
    )


@require_POST
@serialize_instance_mutation
def powercycle(request, pk):
    instance = get_instance(request.user, pk, perm_type="power")
    instance.proxy.force_shutdown()
    instance.proxy.start()
    addlogmsg(
        request.user.username, instance.compute.name, instance.name, _("Power Cycle")
    )
    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id])
    )


@require_POST
@serialize_instance_mutation
def poweroff(request, pk):
    instance = get_instance(request.user, pk, perm_type="power")
    instance.proxy.shutdown()
    addlogmsg(
        request.user.username, instance.compute.name, instance.name, _("Power Off")
    )

    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id])
    )


@require_POST
@superuser_only
@serialize_instance_mutation
def suspend(request, pk):
    instance = get_instance(request.user, pk)
    instance.proxy.suspend()
    addlogmsg(request.user.username, instance.compute.name, instance.name, _("Suspend"))
    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id])
    )


@require_POST
@superuser_only
@serialize_instance_mutation
def resume(request, pk):
    instance = get_instance(request.user, pk)
    instance.proxy.resume()
    addlogmsg(request.user.username, instance.compute.name, instance.name, _("Resume"))
    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id])
    )


@require_POST
@serialize_instance_mutation
def force_off(request, pk):
    instance = get_instance(request.user, pk, perm_type="power")
    instance.proxy.force_shutdown()
    addlogmsg(
        request.user.username, instance.compute.name, instance.name, _("Force Off")
    )
    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id])
    )


@serialize_compute_mutation
def destroy(request, pk):
    if request.method in ["POST", "DELETE"]:
        instance = get_instance(request.user, pk, perm_type="delete")
        with libvirt_instance_lock(instance):
            proxy = instance.proxy
            # Paused and other active states must stop too, or the VM keeps
            # running as a transient domain after the undefine.
            if proxy.instance.isActive():
                proxy.force_shutdown()

            to_delete, shared = [], []
            if request.POST.get("delete_disk", ""):
                to_delete, shared = proxy.split_disk_paths_by_use()

            # Undefine before deleting any disk: if it fails, nothing is lost.
            # Snapshot metadata goes with the domain; internal snapshot data
            # stays in the disk images that are kept.
            flags = VIR_DOMAIN_UNDEFINE_MANAGED_SAVE | VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
            if request.POST.get("delete_nvram", ""):
                flags |= VIR_DOMAIN_UNDEFINE_NVRAM
            else:
                flags |= VIR_DOMAIN_UNDEFINE_KEEP_NVRAM
            proxy.delete(flags)
            instance.delete()

            for path in to_delete:
                try:
                    proxy.get_volume_by_path(path).delete(0)
                except libvirtError as err:
                    messages.error(request, _("Disk %(path)s was not deleted: %(err)s") % {"path": path, "err": err})
            for path in shared:
                messages.warning(request, _("Disk %(path)s is used by another VM and was kept") % {"path": path})
        addlogmsg(
            request.user.username, instance.compute.name, instance.name, _("Destroy")
        )
        return redirect(reverse("instances:index"))

    instance = get_instance(request.user, pk, perm_type="delete")
    try:
        userinstance = instance.userinstance_set.get(user=request.user)
    except Exception:
        userinstance = UserInstance(
            is_delete=request.user.is_superuser
        )

    return render(
        request,
        "instances/destroy_instance_form.html",
        {
            "instance": instance,
            "userinstance": userinstance,
        },
    )


@require_POST
@superuser_only
def migrate(request, pk):
    instance = get_instance(request.user, pk)

    compute_id = request.POST.get("compute_id", "")
    live = request.POST.get("live_migrate", False)
    unsafe = request.POST.get("unsafe_migrate", False)
    xml_del = request.POST.get("xml_delete", False)
    offline = request.POST.get("offline_migrate", False)
    autoconverge = request.POST.get("autoconverge", False)
    compress = request.POST.get("compress", False)
    postcopy = request.POST.get("postcopy", False)

    current_host = instance.compute.hostname
    target_host = Compute.objects.get(id=compute_id)

    try:
        utils.migrate_instance(
            target_host,
            instance,
            request.user,
            live,
            unsafe,
            xml_del,
            offline,
            autoconverge,
            compress,
            postcopy,
        )
    except libvirtError as err:
        messages.error(request, err)

    migration_method = "live" if live is True else "offline"
    msg = _("Instance is migrated(%(method)s) to %(hostname)s") % {
        "hostname": target_host.hostname,
        "method": migration_method,
    }
    addlogmsg(request.user.username, current_host, instance.name, msg)

    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id])
    )


GSTFSD_PORT = 16510
GSTFSD_TIMEOUT = 120  # gstfsd starts a guestfs appliance, which takes a while


def gstfsd_request(hostname, data):
    """Send one request to gstfsd and return its JSON reply, or an error reply."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(GSTFSD_TIMEOUT)
    try:
        s.connect((hostname, GSTFSD_PORT))
        s.send(json.dumps(data).encode())
        return json.loads(s.recv(1024).strip())
    except (OSError, ValueError) as err:
        return {"return": "error", "message": _("gstfsd error: %(err)s") % {"err": err}}
    finally:
        s.close()


@require_POST
@serialize_instance_mutation
def set_root_pass(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")

    if request.method == "POST":
        passwd = request.POST.get("passwd", None)
        if passwd:
            passwd_hash = crypt.crypt(passwd, crypt.mksalt(crypt.METHOD_SHA512))
            # gstfsd opens the domain by name: send the host's current one.
            data = {"action": "password", "passwd": passwd_hash, "vname": instance.proxy.instance.name()}

            if instance.proxy.get_status() == 5:
                result = gstfsd_request(instance.compute.hostname, data)
                if result["return"] == "success":
                    msg = _("Reset root password")
                    addlogmsg(
                        request.user.username, instance.compute.name, instance.name, msg
                    )
                    messages.success(request, msg)
                else:
                    messages.error(request, result["message"])
            else:
                msg = _("Please shutdown down your instance and then try again")
                messages.error(request, msg)
    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id]) + "#access"
    )


@require_POST
@serialize_instance_mutation
def add_public_key(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")

    if request.method == "POST":
        sshkeyid = request.POST.get("sshkeyid", "")
        publickey = get_object_or_404(UserSSHKey, id=sshkeyid, user=request.user)
        data = {
            "action": "publickey",
            "key": publickey.keypublic,
            "vname": instance.proxy.instance.name(),
        }

        if instance.proxy.get_status() == 5:
            result = gstfsd_request(instance.compute.hostname, data)
            if result["return"] == "error":
                msg = result["message"]
            else:
                msg = _("Installed new SSH public key %(keyname)s") % {
                    "keyname": publickey.keyname
                }
            addlogmsg(request.user.username, instance.compute.name, instance.name, msg)

            if result["return"] == "success":
                messages.success(request, msg)
            else:
                messages.error(request, msg)
        else:
            msg = _("Please shutdown down your instance and then try again")
            messages.error(request, msg)
    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id]) + "#access"
    )


@require_POST
@serialize_user_quota
@serialize_instance_mutation
def resizevm_cpu(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    vcpu = instance.proxy.get_vcpu()

    new_vcpu = request.POST.get("vcpu", "")
    new_cur_vcpu = request.POST.get("cur_vcpu", "")

    quota_msg = utils.check_user_quota(
        request.user, 0, int(new_vcpu) - vcpu, 0, 0
    )
    if not request.user.is_superuser and quota_msg:
        msg = _(
            "User %(quota_msg)s quota reached, cannot resize CPU of '%(instance_name)s'!"
        ) % {
            "quota_msg": quota_msg,
            "instance_name": instance.name,
        }
        messages.error(request, msg)
    else:
        cur_vcpu = new_cur_vcpu
        vcpu = new_vcpu
        instance.proxy.resize_cpu(cur_vcpu, vcpu)
        msg = _("CPU is resized:  %(old)s to %(new)s") % {
            "old": cur_vcpu,
            "new": vcpu,
        }
        addlogmsg(
            request.user.username, instance.compute.name, instance.name, msg
        )
        messages.success(request, msg)
    return redirect(reverse("instances:instance", args=[instance.id]) + "#resize")


@require_POST
@serialize_user_quota
@serialize_instance_mutation
def resize_memory(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    memory = instance.proxy.get_memory()
    cur_memory = instance.proxy.get_cur_memory()

    new_memory = request.POST.get("memory", "")
    new_memory_custom = request.POST.get("memory_custom", "")
    if new_memory_custom:
        new_memory = new_memory_custom
    new_cur_memory = request.POST.get("cur_memory", "")
    new_cur_memory_custom = request.POST.get("cur_memory_custom", "")
    if new_cur_memory_custom:
        new_cur_memory = new_cur_memory_custom
    quota_msg = utils.check_user_quota(
        request.user, 0, 0, int(new_memory) - memory, 0
    )
    if not request.user.is_superuser and quota_msg:
        msg = _(
            "User %(quota_msg)s quota reached, cannot resize memory of '%(instance_name)s'!"
        ) % {
            "quota_msg": quota_msg,
            "instance_name": instance.name,
        }
        messages.error(request, msg)
    else:
        instance.proxy.resize_mem(new_cur_memory, new_memory)
        msg = _(
            "Memory is resized: current/max: %(old_cur)s/%(old_max)s to %(new_cur)s/%(new_max)s"
        ) % {
            "old_cur": cur_memory,
            "old_max": memory,
            "new_cur": new_cur_memory,
            "new_max": new_memory,
        }
        addlogmsg(
            request.user.username, instance.compute.name, instance.name, msg
        )
        messages.success(request, msg)

    return redirect(reverse("instances:instance", args=[instance.id]) + "#resize")


@require_POST
@serialize_user_quota
@serialize_instance_mutation
def resize_disk(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    disks = instance.proxy.get_disk_devices()

    disks_new = list()
    for disk in disks:
        input_disk_size = (
            int(request.POST.get("disk_size_" + disk["dev"], "0")) * 1073741824
        )
        if input_disk_size > disk["size"] + (64 << 20):
            disk["size_new"] = input_disk_size
            disks_new.append(disk)

    if not disks_new:
        messages.warning(request, _("No disks were selected or valid for resizing."))
        return redirect(reverse("instances:instance", args=[instance.id]) + "#resize")

    disk_sum = sum([disk["size"] >> 30 for disk in disks_new])
    disk_new_sum = sum([disk["size_new"] >> 30 for disk in disks_new])
    quota_msg = utils.check_user_quota(
        request.user, 0, 0, 0, disk_new_sum - disk_sum
    )
    if not request.user.is_superuser and quota_msg:
        msg = _(
            "User %(quota_msg)s quota reached, cannot resize disks of '%(instance_name)s'!"
        ) % {
            "quota_msg": quota_msg,
            "instance_name": instance.name,
        }
        messages.error(request, msg)
    else:
        instance.proxy.resize_disk(disks_new)
        devs_str = ", ".join([d["dev"] for d in disks_new])
        msg = _("Disk is resized: %(dev)s") % {"dev": devs_str}
        addlogmsg(
            request.user.username, instance.compute.name, instance.name, msg
        )
        messages.success(request, msg)

    return redirect(reverse("instances:instance", args=[instance.id]) + "#resize")


@require_POST
@superuser_only
@serialize_instance_mutation
def add_new_vol(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    media = instance.proxy.get_media_devices()
    disks = instance.proxy.get_disk_devices()
    storage = request.POST.get("storage", "")
    name = request.POST.get("name", "")
    format = request.POST.get("format", app_settings.INSTANCE_VOLUME_DEFAULT_FORMAT)
    size = request.POST.get("size", 0)
    meta_prealloc = True if request.POST.get("meta_prealloc", False) else False
    bus = request.POST.get("bus", app_settings.INSTANCE_VOLUME_DEFAULT_BUS)
    cache = request.POST.get("cache", app_settings.INSTANCE_VOLUME_DEFAULT_CACHE)

    invalid = invalid_disk_options(instance, bus=bus, cache=cache, format=format)
    if not VOLUME_NAME_RE.fullmatch(name):
        invalid.append("name")
    if invalid:
        return reject_disk_options(request, invalid)

    conn_create = wvmCreate(
        instance.compute.hostname,
        instance.compute.login,
        instance.compute.password,
        instance.compute.type,
    )
    target_dev = utils.get_new_disk_dev(media, disks, bus)

    source = conn_create.create_volume(
        storage,
        name,
        size,
        format,
        meta_prealloc,
        int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_UID),
        int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_GID),
    )

    conn_pool = wvmStorage(
        instance.compute.hostname,
        instance.compute.login,
        instance.compute.password,
        instance.compute.type,
        storage,
    )

    pool_type = conn_pool.get_type()
    disk_type = conn_pool.get_volume_type(os.path.basename(source))

    if pool_type == "rbd":
        source_info = conn_pool.get_rbd_source()
    else:  # add more disk types to handle different pool and disk types
        source_info = None

    instance.proxy.attach_disk(
        target_dev,
        source,
        source_info=source_info,
        pool_type=pool_type,
        disk_type=disk_type,
        target_bus=bus,
        format_type=format,
        cache_mode=cache,
    )
    msg = _("Attach new disk: %(name)s (%(format)s)") % {
        "name": name,
        "format": format,
    }
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#disks")


@require_POST
@superuser_only
@serialize_instance_mutation
def add_existing_vol(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    storage = request.POST.get("selected_storage", "")
    name = request.POST.get("vols", "")
    bus = request.POST.get("bus", app_settings.INSTANCE_VOLUME_DEFAULT_BUS)
    cache = request.POST.get("cache", app_settings.INSTANCE_VOLUME_DEFAULT_CACHE)

    invalid = invalid_disk_options(instance, bus=bus, cache=cache)
    if invalid:
        return reject_disk_options(request, invalid)

    media = instance.proxy.get_media_devices()
    disks = instance.proxy.get_disk_devices()

    conn_create = wvmStorage(
        instance.compute.hostname,
        instance.compute.login,
        instance.compute.password,
        instance.compute.type,
        storage,
    )

    # Only volumes that really exist in the selected pool; this also rules
    # out path traversal through the volume name.
    if name not in conn_create.get_volumes():
        return reject_disk_options(request, ["vols"])

    format_type = conn_create.get_volume_format_type(name)
    disk_type = conn_create.get_volume_type(name)
    pool_type = conn_create.get_type()
    if pool_type == "rbd":
        source_info = conn_create.get_rbd_source()
        path = conn_create.get_source_name()
    else:
        source_info = None
        path = conn_create.get_target_path()

    target_dev = utils.get_new_disk_dev(media, disks, bus)
    source = f"{path}/{name}"

    instance.proxy.attach_disk(
        target_dev,
        source,
        source_info=source_info,
        pool_type=pool_type,
        disk_type=disk_type,
        target_bus=bus,
        format_type=format_type,
        cache_mode=cache,
    )
    msg = _("Attach Existing disk: %(target_dev)s") % {"target_dev": target_dev}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#disks")


@require_POST
@superuser_only
@serialize_instance_mutation
def edit_volume(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    if "edit_volume" in request.POST:
        target_dev = request.POST.get("dev", "")

        new_path = request.POST.get("vol_path", "")
        shareable = bool(request.POST.get("vol_shareable", False))
        readonly = bool(request.POST.get("vol_readonly", False))
        disks = instance.proxy.get_disk_devices(config=True)
        current = next((disk for disk in disks if disk["dev"] == target_dev), {})
        new_bus = request.POST.get("vol_bus", current.get("bus"))
        serial = request.POST.get("vol_serial", "")
        format = request.POST.get("vol_format", "")
        cache = request.POST.get(
            "vol_cache", app_settings.INSTANCE_VOLUME_DEFAULT_CACHE
        )
        io = request.POST.get("vol_io_mode", app_settings.INSTANCE_VOLUME_DEFAULT_IO)
        discard = request.POST.get(
            "vol_discard_mode", app_settings.INSTANCE_VOLUME_DEFAULT_DISCARD
        )
        zeroes = request.POST.get(
            "vol_detect_zeroes", app_settings.INSTANCE_VOLUME_DEFAULT_DETECT_ZEROES
        )
        # The form shows "None" (or nothing) for a disk without driver type.
        if format in ("", "None"):
            format = current.get("format") or ""

        invalid = invalid_disk_options(
            instance,
            bus=new_bus,
            cache=cache,
            io=io,
            discard=discard,
            zeroes=zeroes,
            # "": the disk has no driver type and keeps having none.
            format=format or None,
            serial=serial,
        )
        if not current:
            invalid.append("dev")
        if invalid:
            return reject_disk_options(request, invalid)

        instance.proxy.edit_disk(
            target_dev,
            new_path,
            readonly,
            shareable,
            new_bus,
            serial,
            format,
            cache,
            io,
            discard,
            zeroes,
        )

        if not instance.proxy.get_status() == 5:
            messages.success(
                request,
                _(
                    "Volume changes are applied. "
                    + "But it will be activated after shutdown"
                ),
            )
        else:
            messages.success(request, _("Volume is changed successfully."))
        msg = _("Edit disk: %(target_dev)s") % {"target_dev": target_dev}
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)

    return redirect(request.META.get("HTTP_REFERER") + "#disks")


@require_POST
@superuser_only
@serialize_compute_mutation
def delete_vol(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    dev = request.POST.get("dev", "")

    # Resolve the volume from the VM's own XML; never trust the posted
    # storage/name, which could point at any volume on the compute.
    disk = next((d for d in instance.disks if d["dev"] == dev), None)
    if disk is None or not disk["storage"] or not disk["image"]:
        messages.error(
            request,
            _("Disk %(dev)s is not a storage volume of this instance") % {"dev": dev},
        )
        return redirect(request.META.get("HTTP_REFERER") + "#disks")

    conn_delete = wvmStorage(
        instance.compute.hostname,
        instance.compute.login,
        instance.compute.password,
        instance.compute.type,
        disk["storage"],
    )

    if disk["path"] in instance.proxy.paths_used_by_other_domains():
        messages.error(
            request,
            _("Volume %(vol)s is used by another VM; detach it instead") % {"vol": disk["image"]},
        )
        return redirect(request.META.get("HTTP_REFERER") + "#disks")

    msg = _("Delete disk: %(dev)s") % {"dev": dev}
    instance.proxy.detach_disk(dev)
    # Never delete a volume the guest may still be writing to.
    if not instance.proxy.wait_disk_detached(dev):
        messages.warning(
            request,
            _("The guest has not released disk %(dev)s yet; volume %(vol)s was kept. "
              "Delete it from the storage pool once the VM is shut down.")
            % {"dev": dev, "vol": disk["image"]},
        )
        return redirect(request.META.get("HTTP_REFERER") + "#disks")
    conn_delete.del_volume(disk["image"])

    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#disks")


@require_POST
@superuser_only
@serialize_instance_mutation
def detach_vol(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    dev = request.POST.get("dev", "")
    instance.proxy.detach_disk(dev)
    msg = _("Detach disk: %(dev)s") % {"dev": dev}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)

    return redirect(request.META.get("HTTP_REFERER") + "#disks")


@require_POST
@superuser_only
@serialize_instance_mutation
def add_cdrom(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    bus = request.POST.get("bus", "ide" if instance.machine == "pc" else "sata")
    invalid = invalid_disk_options(instance, bus=bus)
    if invalid:
        return reject_disk_options(request, invalid)

    target = utils.get_new_disk_dev(instance.media, instance.disks, bus)
    instance.proxy.attach_disk(
        target,
        "",
        disk_device="cdrom",
        cache_mode="none",
        target_bus=bus,
        readonly=True,
    )
    msg = _("Add CD-ROM: %(target)s") % {"target": target}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)

    return redirect(request.META.get("HTTP_REFERER") + "#disks")


@require_POST
@superuser_only
@serialize_instance_mutation
def detach_cdrom(request, pk, dev):
    instance = get_instance(request.user, pk, perm_type="change")
    # dev = request.POST.get('detach_cdrom', '')
    instance.proxy.detach_disk(dev)
    msg = _("Detach CD-ROM: %(dev)s") % {"dev": dev}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)

    return redirect(request.META.get("HTTP_REFERER") + "#disks")


@require_POST
@superuser_only
@serialize_instance_mutation
def unmount_iso(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    dev = request.POST.get("umount_iso", "")
    try:
        instance.proxy.umount_iso(dev)
    except libvirtError as err:
        messages.error(request, err)
    else:
        msg = _("Unmount media: %(dev)s") % {"dev": dev}
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(_same_origin_referer(request, reverse("instances:instance", args=[pk])) + "#disks")


@require_POST
@superuser_only
@serialize_instance_mutation
def mount_iso(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    image = request.POST.get("media", "")
    dev = request.POST.get("mount_iso", "")
    try:
        instance.proxy.mount_iso(dev, image)
    except libvirtError as err:
        messages.error(request, err)
    else:
        msg = _("Mount media: %(dev)s") % {"dev": dev}
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(_same_origin_referer(request, reverse("instances:instance", args=[pk])) + "#disks")


@require_POST
@serialize_instance_mutation
def snapshot(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )

    if allow_admin_or_not_template and request.user.has_perm(
        "instances.snapshot_instances"
    ):
        name = request.POST.get("name", "")
        desc = request.POST.get("description", "")
        try:
            instance.proxy.create_snapshot(name, desc)
        except libvirtError as err:
            # e.g. UEFI firmware whose NVRAM format does not allow internal snapshots
            messages.error(request, _("Snapshot was not created: %(err)s") % {"err": err})
        else:
            msg = _("Create snapshot: %(snap)s") % {"snap": name}
            addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#managesnapshot")


@require_POST
@serialize_instance_mutation
def delete_snapshot(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )
    if allow_admin_or_not_template and request.user.has_perm(
        "instances.snapshot_instances"
    ):
        snap_name = request.POST.get("name", "")
        instance.proxy.snapshot_delete(snap_name)
        msg = _("Delete snapshot: %(snap)s") % {"snap": snap_name}
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#managesnapshot")


@require_POST
@serialize_instance_mutation
def revert_snapshot(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )
    if allow_admin_or_not_template and request.user.has_perm(
        "instances.snapshot_instances"
    ):
        snap_name = request.POST.get("name", "")
        try:
            instance.proxy.snapshot_revert(snap_name)
        except libvirtError as err:
            messages.error(request, _("Snapshot was not reverted: %(err)s") % {"err": err})
            return redirect(request.META.get("HTTP_REFERER") + "#managesnapshot")
        msg = _("Successful revert snapshot: ")
        msg += snap_name
        messages.success(request, msg)
        msg = _("Revert snapshot: %(snap)s") % {"snap": snap_name}
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#managesnapshot")


@require_POST
@serialize_instance_mutation
def create_external_snapshot(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )

    if allow_admin_or_not_template and request.user.has_perm(
        "instances.snapshot_instances"
    ):
        name = request.POST.get("name", "")
        desc = request.POST.get("description", "")
        instance.proxy.create_external_snapshot("s1." + name, instance, desc=desc)
        msg = _("Create external snapshot: %(snap)s") % {"snap": name}
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#managesnapshot")


def get_external_snapshots(request, pk):
    instance = get_instance(request.user, pk)
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )

    if allow_admin_or_not_template and request.user.has_perm(
        "instances.snapshot_instances"
    ):
        external_snapshots = instance.proxy.get_external_snapshots()
    return external_snapshots


@require_POST
@serialize_instance_mutation
def revert_external_snapshot(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )

    if allow_admin_or_not_template and request.user.has_perm(
        "instances.snapshot_instances"
    ):
        instance_state = True if instance.proxy.get_status() != 5 else False
        name = request.POST.get("name", "")
        date = request.POST.get("date", "")
        desc = request.POST.get("desc", "")
        instance.proxy.force_shutdown() if instance_state else None
        instance.proxy.revert_external_snapshot(name, date, desc)
        instance.proxy.start() if instance_state else None
        msg = _("Revert external snapshot: %(snap)s") % {"snap": name}
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#managesnapshot")


@require_POST
@serialize_instance_mutation
def delete_external_snapshot(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    instance_state = True if instance.proxy.get_status() == 5 else False
    allow_admin_or_not_template = (
        request.user.is_superuser or request.user.is_staff or not instance.is_template
    )

    if allow_admin_or_not_template and request.user.has_perm(
        "instances.snapshot_instances"
    ):
        name = request.POST.get("name", "")

        instance.proxy.start(VIR_DOMAIN_START_PAUSED) if instance_state else None

        try:
            instance.proxy.delete_external_snapshot(name)
            msg = _("Delete external snapshot: %(snap)s") % {"snap": name}
            addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
        finally:
            instance.proxy.force_shutdown() if instance_state else None

    return redirect(request.META.get("HTTP_REFERER") + "#managesnapshot")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_vcpu(request, pk):
    instance = get_instance(request.user, pk)
    id = request.POST.get("id", "")
    enabled = request.POST.get("set_vcpu", "")
    if enabled == "True":
        instance.proxy.set_vcpu(id, 1)
    else:
        instance.proxy.set_vcpu(id, 0)
    msg = _("VCPU %(id)s is enabled=%(enabled)s") % {"id": id, "enabled": enabled}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#resize")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_vcpu_hotplug(request, pk):
    instance = get_instance(request.user, pk)
    status = True if request.POST.get("vcpu_hotplug", "False") == "True" else False
    msg = _("VCPU Hot-plug is enabled=%(status)s") % {"status": status}
    instance.proxy.set_vcpu_hotplug(status)
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#resize")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_autostart(request, pk):
    instance = get_instance(request.user, pk)
    instance.proxy.set_autostart(1)
    msg = _("Set autostart")
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#boot_opt")


@require_POST
@superuser_only
@serialize_instance_mutation
def unset_autostart(request, pk):
    instance = get_instance(request.user, pk)
    instance.proxy.set_autostart(0)
    msg = _("Unset autostart")
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#boot_opt")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_bootmenu(request, pk):
    instance = get_instance(request.user, pk)
    instance.proxy.set_bootmenu(1)
    msg = _("Enable boot menu")
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#boot_opt")


@require_POST
@superuser_only
@serialize_instance_mutation
def unset_bootmenu(request, pk):
    instance = get_instance(request.user, pk)
    instance.proxy.set_bootmenu(0)
    msg = _("Disable boot menu")
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#boot_opt")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_bootorder(request, pk):
    instance = get_instance(request.user, pk)
    bootorder = request.POST.get("bootorder", "")
    if bootorder:
        order_list = {}
        for idx, val in enumerate(bootorder.split(",")):
            dev_type, dev = val.split(":", 1)
            order_list[idx] = {"type": dev_type, "dev": dev}
        instance.proxy.set_bootorder(order_list)
        msg = _("Set boot order")

        if not instance.proxy.get_status() == 5:
            messages.success(
                request,
                _(
                    "Boot menu changes applied. "
                    + "But it will be activated after shutdown"
                ),
            )
        else:
            messages.success(request, _("Boot order changed successfully."))
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#boot_opt")


@require_POST
@superuser_only
@serialize_instance_mutation
def change_xml(request, pk):
    instance = get_instance(request.user, pk)
    new_xml = request.POST.get("inst_xml", "")
    if new_xml:
        instance.proxy._defineXML(new_xml)
        msg = _("Change instance XML")
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#xmledit")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_guest_agent(request, pk):
    instance = get_instance(request.user, pk)
    status = request.POST.get("guest_agent")
    if status == "True":
        instance.proxy.add_guest_agent()
    if status == "False":
        instance.proxy.remove_guest_agent()

    msg = _("Set Guest Agent: %(status)s") % {"status": status}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#options")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_video_model(request, pk):
    instance = get_instance(request.user, pk)
    video_model = request.POST.get("video_model", "vga")
    instance.proxy.set_video_model(video_model)
    msg = _("Set Video Model: %(model)s") % {"model": video_model}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#options")


@require_POST
@superuser_only
@serialize_instance_mutation
def change_network(request, pk):
    instance = get_instance(request.user, pk)
    back = _same_origin_referer(request, reverse("instances:instance", args=[pk])) + "#network"

    # Each NIC has its own form; its fields end with that NIC's index.
    nums = [key[len("net-old-mac-"):] for key in request.POST if key.startswith("net-old-mac-")]
    if len(nums) != 1:
        messages.error(request, _("Select one network interface to change"))
        return redirect(back)
    num = nums[0]
    old_mac = request.POST[f"net-old-mac-{num}"]
    mac = request.POST.get(f"net-mac-{num}", "").strip() or old_mac
    try:
        util.validate_macaddr(mac)
    except ValueError as err:
        messages.error(request, err)
        return redirect(back)

    (source, source_type) = utils.get_network_tuple(request.POST.get(f"net-source-{num}", ""))
    if source_type == "iface":
        iface = wvmInterface(
            instance.compute.hostname,
            instance.compute.login,
            instance.compute.password,
            instance.compute.type,
            source,
        )
        source_type = iface.get_type()

    try:
        instance.proxy.change_network(
            old_mac,
            mac,
            source,
            source_type,
            request.POST.get(f"net-model-{num}"),
            request.POST.get(f"net-nwfilter-{num}", ""),
        )
    except libvirtError as err:
        messages.error(request, err)
        return redirect(back)

    msg = _("Change network: %(mac)s") % {"mac": old_mac}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    msg = _("Network Device Config is changed. Please shutdown instance to activate.")
    if instance.proxy.get_status() != 5:
        messages.success(request, msg)
    return redirect(back)


@require_POST
@superuser_only
@serialize_instance_mutation
def add_network(request, pk):
    instance = get_instance(request.user, pk)

    mac = request.POST.get("add-net-mac")
    nwfilter = request.POST.get("add-net-nwfilter")
    (source, source_type) = utils.get_network_tuple(request.POST.get("add-net-network"))
    model = request.POST.get("add-net-model")

    if source_type == "iface":
        iface = wvmInterface(
            instance.compute.hostname,
            instance.compute.login,
            instance.compute.password,
            instance.compute.type,
            source,
        )
        source_type = iface.get_type()

    instance.proxy.add_network(mac, source, source_type, model=model, nwfilter=nwfilter)
    msg = _("Add network: %(mac)s") % {"mac": mac}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#network")


@require_POST
@superuser_only
@serialize_instance_mutation
def delete_network(request, pk):
    instance = get_instance(request.user, pk)
    mac_address = request.POST.get("delete_network", "")

    instance.proxy.delete_network(mac_address)
    msg = _("Delete Network: %(mac)s") % {"mac": mac_address}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#network")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_link_state(request, pk):
    instance = get_instance(request.user, pk)

    mac_address = request.POST.get("mac", "")
    state = request.POST.get("set_link_state")
    state = "down" if state == "up" else "up"
    instance.proxy.set_link_state(mac_address, state)
    msg = _("Set Link State: %(state)s") % {"state": state}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#network")


@require_POST
@superuser_only
@serialize_instance_mutation
def set_qos(request, pk):
    instance = get_instance(request.user, pk)

    qos_dir = request.POST.get("qos_direction", "")
    average = request.POST.get("qos_average") or 0
    peak = request.POST.get("qos_peak") or 0
    burst = request.POST.get("qos_burst") or 0
    keys = request.POST.keys()
    mac_key = [key for key in keys if "mac" in key]
    if mac_key:
        mac = request.POST.get(mac_key[0])

    instance.proxy.set_qos(mac, qos_dir, average, peak, burst)
    if instance.proxy.get_status() == 5:
        messages.success(
            request, _("%(qos_dir)s QoS is set") % {"qos_dir": qos_dir.capitalize()}
        )
    else:
        messages.success(
            request,
            _(
                "%(qos_dir)s QoS is set. Network XML is changed. \
                Stop and start network to activate new config."
            )
            % {"qos_dir": qos_dir.capitalize()},
        )

    return redirect(request.META.get("HTTP_REFERER") + "#network")


@require_POST
@superuser_only
@serialize_instance_mutation
def unset_qos(request, pk):
    instance = get_instance(request.user, pk)
    qos_dir = request.POST.get("qos_direction", "")
    mac = request.POST.get("net-mac")
    instance.proxy.unset_qos(mac, qos_dir)

    if instance.proxy.get_status() == 5:
        messages.success(
            request, _("%(qos_dir)s QoS is deleted") % {"qos_dir": qos_dir.capitalize()}
        )
    else:
        messages.success(
            request,
            _(
                "%(qos_dir)s QoS is deleted. Network XML is changed. \
                Stop and start network to activate new config."
            )
            % {"qos_dir": qos_dir.capitalize()},
        )
    return redirect(request.META.get("HTTP_REFERER") + "#network")


@require_POST
@superuser_only
def add_owner(request, pk):
    instance = get_instance(request.user, pk)
    user_id = request.POST.get("user_id")

    check_inst = 0

    if app_settings.ALLOW_INSTANCE_MULTIPLE_OWNER == "False":
        check_inst = UserInstance.objects.filter(instance=instance).count()

    if check_inst > 0:
        messages.error(
            request, _("Only one owner is allowed and the one already added")
        )
    else:
        add_user_inst = UserInstance(instance=instance, user_id=user_id)
        add_user_inst.save()
        user = User.objects.get(id=user_id)
        msg = _("Add owner: %(user)s") % {"user": user}
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#users")


@require_POST
@superuser_only
def del_owner(request, pk):
    instance = get_instance(request.user, pk)
    userinstance_id = int(request.POST.get("userinstance", ""))
    userinstance = UserInstance.objects.get(pk=userinstance_id)
    userinstance.delete()
    msg = _("Delete owner: %(userinstance_id)s ") % {"userinstance_id": userinstance_id}
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return redirect(request.META.get("HTTP_REFERER") + "#users")


MAC_ADDRESS_RE = re.compile(r"^([0-9A-F]{2})(:?[0-9A-F]{2}){5}$", re.IGNORECASE)
CLONE_POST_KEY_RE = re.compile(r"^(clone-net-mac-\d+|disk-[a-z0-9]+|meta-[a-z0-9]+)$")


@require_POST
@permission_required("instances.clone_instances", raise_exception=True)
@serialize_user_quota
def clone(request, pk):
    # Cloning copies the source disks, so it needs change permission on the
    # source VM. Templates are meant to be deployed from, so viewing is enough.
    instance = get_instance(request.user, pk)
    if not instance.is_template:
        instance = get_instance(request.user, pk, perm_type="change")

    clone_data = dict()
    clone_data["name"] = request.POST.get("name", "").strip()
    clone_data["clone-title"] = request.POST.get("clone-title", "").strip()
    clone_data["clone-description"] = request.POST.get("clone-description", "").strip()

    disk_sum = sum(int(disk.get("size") or 0) >> 30 for disk in instance.disks)
    quota_msg = utils.check_user_quota(
        request.user, 1, instance.vcpu, instance.memory, disk_sum
    )

    clone_data["disk_owner_uid"] = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_UID)
    clone_data["disk_owner_gid"] = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_GID)

    # Only superusers may choose disk names and MAC addresses; the form only
    # shows those fields to them.
    if request.user.is_superuser:
        for key, value in request.POST.items():
            if CLONE_POST_KEY_RE.fullmatch(key):
                clone_data[key] = value.strip()

    if app_settings.CLONE_INSTANCE_AUTO_NAME == "True" and not clone_data["name"]:
        auto_vname = utils.get_clone_free_names()[0]
        clone_data["name"] = auto_vname
        clone_data["clone-net-mac-0"] = utils.get_dhcp_mac_address(auto_vname)
        for disk in instance.disks:
            disk_dev = f"disk-{disk['dev']}"
            disk_name = utils.get_clone_disk_name(disk, instance.name, auto_vname)
            clone_data[disk_dev] = disk_name

    if not request.user.is_superuser:
        for disk in instance.disks:
            clone_data[f"disk-{disk['dev']}"] = utils.get_clone_disk_name(
                disk, instance.name, clone_data["name"]
            )
        for num in range(max(1, len(instance.networks))):
            key = f"clone-net-mac-{num}"
            if not clone_data.get(key):
                clone_data[key] = (
                    utils.get_dhcp_mac_address(clone_data["name"]) if num == 0 else ""
                ) or utils.get_random_mac_address()

    check_instance = Instance.objects.filter(name=clone_data["name"])
    invalid_macs = [
        value
        for key, value in clone_data.items()
        if key.startswith("clone-net-mac-") and not MAC_ADDRESS_RE.fullmatch(value)
    ]
    invalid_disks = [
        value
        for key, value in clone_data.items()
        # None: a disk without a volume, which clone_instance does not copy.
        if key.startswith("disk-")
        and value is not None
        and not VOLUME_NAME_RE.fullmatch(value)
    ]

    if not request.user.is_superuser and quota_msg:
        msg = _(
            "User '%(quota_msg)s' quota reached, cannot create '%(clone_name)s'!"
        ) % {
            "quota_msg": quota_msg,
            "clone_name": clone_data["name"],
        }
        messages.error(request, msg)
    elif check_instance:
        msg = _("Instance '%(clone_name)s' already exists!") % {
            "clone_name": clone_data["name"]
        }
        messages.error(request, msg)
    elif not re.match(r"^[a-zA-Z0-9-]+$", clone_data["name"]):
        msg = _("Instance name '%(clone_name)s' contains invalid characters!") % {
            "clone_name": clone_data["name"]
        }
        messages.error(request, msg)
    elif "clone-net-mac-0" not in clone_data or invalid_macs:
        msg = _("Instance MAC '%(clone_mac)s' invalid format!") % {
            "clone_mac": ", ".join(invalid_macs)
        }
        messages.error(request, msg)
    elif invalid_disks:
        msg = _("Disk name '%(disk_name)s' contains invalid characters!") % {
            "disk_name": ", ".join(str(v) for v in invalid_disks)
        }
        messages.error(request, msg)
    else:
        try:
            # The whole compute: the destination name and disk names must not be
            # taken by a concurrent clone of another VM while volumes are copied.
            with libvirt_compute_lock(instance.compute):
                new_uuid = instance.proxy.clone_instance(clone_data)
                new_instance = Instance.objects.get_or_create(
                    compute=instance.compute,
                    uuid=new_uuid,
                    defaults={"name": clone_data["name"]},
                )[0]
                if new_instance.name != clone_data["name"]:
                    new_instance.name = clone_data["name"]
                    new_instance.save(update_fields=["name"])
                UserInstance.objects.get_or_create(
                    instance_id=new_instance.id,
                    user_id=request.user.id,
                    defaults={"is_delete": True, "is_change": True, "is_vnc": True},
                )
            msg = _("Create a clone of '%(instance_name)s'") % {
                "instance_name": instance.name
            }
            messages.success(request, msg)
            addlogmsg(
                request.user.username, instance.compute.name, new_instance.name, msg
            )

            if app_settings.CLONE_INSTANCE_AUTO_MIGRATE == "True":
                new_compute = Compute.objects.order_by("?").first()
                utils.migrate_instance(
                    new_compute, new_instance, request.user, xml_del=True, offline=True
                )

            return redirect(reverse("instances:instance", args=[new_instance.id]))
        except Exception as e:
            messages.error(request, e)

    return get_safe_redirect(request, default=reverse("instances:instance", args=[pk]))


@require_POST
@serialize_instance_mutation
def update_console(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")

    if utils.can_manage_console(request.user, instance):
        form = ConsoleForm(request.POST or None)
        if form.is_valid():
            if (
                "generate_password" in form.changed_data
                or "clear_password" in form.changed_data
                or "password" in form.changed_data
            ):
                if form.cleaned_data["generate_password"]:
                    password = randomPasswd()
                elif form.cleaned_data["clear_password"]:
                    password = ""
                else:
                    password = form.cleaned_data["password"]

                if not instance.proxy.set_console_passwd(password):
                    msg = _(
                        "Error setting console password. "
                        + "You should check that your instance have an graphic device."
                    )
                    messages.error(request, msg)
                else:
                    msg = _("Set VNC password")
                    addlogmsg(
                        request.user.username, instance.compute.name, instance.name, msg
                    )

            if "keymap" in form.changed_data or "clear_keymap" in form.changed_data:
                if form.cleaned_data["clear_keymap"]:
                    instance.proxy.set_console_keymap("")
                else:
                    instance.proxy.set_console_keymap(form.cleaned_data["keymap"])

                msg = _("Set VNC keymap")
                addlogmsg(
                    request.user.username, instance.compute.name, instance.name, msg
                )

            if "listen_on" in form.changed_data:
                instance.proxy.set_console_listener_addr(form.cleaned_data["listen_on"])
                msg = _("Set VNC listen address")
                addlogmsg(
                    request.user.username, instance.compute.name, instance.name, msg
                )
        else:
            messages.error(request, _("Console settings were not saved: %(errors)s") % {"errors": form.errors.as_text()})

    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id]) + "#vncsettings"
    )


@require_POST
@serialize_instance_mutation
def change_options(request, pk):
    instance = get_instance(request.user, pk, perm_type="change")
    try:
        userinstance = instance.userinstance_set.get(user=request.user)
    except Exception:
        userinstance = UserInstance(is_change=False)

    if request.user.is_superuser or userinstance.is_change:
        # Only superusers and staff may (un)mark templates (ROADMAP S-22). The
        # checkbox is disabled for everyone else, and a disabled checkbox is
        # not submitted, so for them the flag must be left alone.
        if request.user.is_superuser or request.user.is_staff:
            instance.is_template = bool(request.POST.get("is_template", False))
            instance.save(update_fields=["is_template"])

        options = {}
        for post in request.POST:
            if post in ["title", "description"]:
                options[post] = request.POST.get(post, "")
        instance.proxy.set_options(options)

        msg = _("Edit options")
        addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    return get_safe_redirect(
        request, default=reverse("instances:instance", args=[instance.id]) + "#options"
    )


def getvvfile(request, pk):
    instance = get_instance(request.user, pk)
    # The .vv file contains the VNC password: same rule as the console.
    if not utils.can_open_console(request.user, instance):
        raise PermissionDenied
    conn = wvmInstances(
        instance.compute.hostname,
        instance.compute.login,
        instance.compute.password,
        instance.compute.type,
    )

    # The host's current name for this UUID; the stored name may be stale.
    name = instance.proxy.instance.name()
    msg = _("Send console.vv file")
    addlogmsg(request.user.username, instance.compute.name, instance.name, msg)
    response = HttpResponse(
        content="",
        content_type="application/x-virt-viewer",
        status=200,
        reason=None,
        charset="utf-8",
    )
    response.writelines("[virt-viewer]\n")
    response.writelines("type=" + conn.graphics_type(name) + "\n")
    if conn.graphics_listen(name) == "0.0.0.0":
        response.writelines("host=" + conn.host + "\n")
    else:
        response.writelines("host=" + conn.graphics_listen(name) + "\n")
    response.writelines("port=" + conn.graphics_port(name) + "\n")
    response.writelines("title=" + conn.domain_name(name) + "\n")
    response.writelines("password=" + conn.graphics_passwd(name) + "\n")
    response.writelines("enable-usbredir=1\n")
    response.writelines("disable-effects=all\n")
    response.writelines("secure-attention=ctrl+alt+ins\n")
    response.writelines("release-cursor=ctrl+alt\n")
    response.writelines("fullscreen=1\n")
    response.writelines("delete-this-file=1\n")
    response["Content-Disposition"] = 'attachment; filename="console.vv"'
    return response


@superuser_only
def create_instance_select_type(request, compute_id):
    """
    :param request:
    :param compute_id:
    :return:
    """

    conn = None
    storages = list()
    networks = list()
    hypervisors = list()
    meta_prealloc = False
    compute = get_object_or_404(Compute, pk=compute_id)

    conn = wvmCreate(compute.hostname, compute.login, compute.password, compute.type)
    instances = conn.get_instances()
    all_hypervisors = conn.get_hypervisors_machines()

    # Supported hypervisors by webvirtcloud: i686, x86_64(for now)
    supported_arch = [
        "x86_64",
        "i686",
        "aarch64",
        "armv7l",
        "ppc64",
        "ppc64le",
        "s390x",
    ]
    hypervisors = [hpv for hpv in all_hypervisors.keys() if hpv in supported_arch]
    default_machine = app_settings.INSTANCE_MACHINE_DEFAULT_TYPE
    default_arch = app_settings.INSTANCE_ARCH_DEFAULT_TYPE

    if request.method == "POST":
        if "create_xml" in request.POST:
            xml = request.POST.get("dom_xml", "")
            try:
                name = util.get_xml_path(xml, "/domain/name")
            except util.etree.Error:
                name = None
            if name in instances:
                error_msg = _("A virtual machine with this name already exists")
                messages.error(request, error_msg)
            else:
                with libvirt_compute_lock(compute):
                    conn._defineXML(xml)
                    utils.refr(compute)
                    instance = compute.instance_set.get(name=name)
                return redirect(reverse("instances:instance", args=[instance.id]))

    return render(request, "create_instance_w1.html", locals())


@superuser_only
def create_instance(request, compute_id, arch, machine):
    """
    :param request:
    :param compute_id:
    :param arch:
    :param machine:
    :return:
    """

    conn = None
    storages = list()
    networks = list()
    hypervisors = list()
    firmwares = list()
    meta_prealloc = False
    compute = get_object_or_404(Compute, pk=compute_id)
    flavors = Flavor.objects.filter().order_by("id")
    appsettings = AppSettings.objects.all()

    try:
        conn = wvmCreate(
            compute.hostname,
            compute.login,
            compute.password,
            compute.type
        )

        default_firmware = app_settings.INSTANCE_FIRMWARE_DEFAULT_TYPE
        default_cpu_mode = app_settings.INSTANCE_CPU_DEFAULT_MODE
        instances = conn.get_instances()
        videos = conn.get_video_models(arch, machine)
        default_video = app_settings.INSTANCE_VIDEO_DEFAULT_TYPE
        cache_modes = sorted(conn.get_cache_modes().items())
        default_cache = app_settings.INSTANCE_VOLUME_DEFAULT_CACHE
        default_io = app_settings.INSTANCE_VOLUME_DEFAULT_IO
        default_zeroes = app_settings.INSTANCE_VOLUME_DEFAULT_DETECT_ZEROES
        default_discard = app_settings.INSTANCE_VOLUME_DEFAULT_DISCARD
        default_disk_format = app_settings.INSTANCE_VOLUME_DEFAULT_FORMAT
        default_disk_owner_uid = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_UID)
        default_disk_owner_gid = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_GID)
        default_scsi_disk_model = app_settings.INSTANCE_VOLUME_DEFAULT_SCSI_CONTROLLER
        listener_addr = settings.QEMU_CONSOLE_LISTENER_ADDRESSES
        mac_auto = util.randomMAC()
        disk_devices = conn.get_disk_device_types(arch, machine)
        disk_buses = conn.get_disk_bus_types(arch, machine)
        default_bus = app_settings.INSTANCE_VOLUME_DEFAULT_BUS
        networks = sorted(conn.get_networks())
        nwfilters = conn.get_nwfilters()
        net_models_host = conn.get_network_models()
        default_nic_type = app_settings.INSTANCE_NIC_DEFAULT_TYPE
        storages = sorted(conn.get_storages(only_actives=True))
        default_cdrom = app_settings.INSTANCE_CDROM_ADD
        input_device_buses = ["default", "virtio", "usb"]
        default_input_device_bus = app_settings.INSTANCE_INPUT_DEFAULT_DEVICE

        dom_caps = conn.get_dom_capabilities(arch, machine)
        caps = conn.get_capabilities(arch)

        virtio_support = conn.is_supports_virtio(arch, machine)
        hv_supports_uefi = conn.supports_uefi_xml(dom_caps["loader_enums"])
        # Add BIOS
        label = conn.label_for_firmware_path(arch, None)
        if label:
            firmwares.append(label)
        # Add UEFI
        loader_path = conn.find_uefi_path_for_arch(arch, dom_caps["loaders"])
        label = conn.label_for_firmware_path(arch, loader_path)
        if label:
            firmwares.append(label)
        firmwares = list(set(firmwares))

        flavor_form = FlavorForm()

        if conn:
            if not storages:
                raise libvirtError(_("You haven't defined any storage pools"))
            if not networks:
                raise libvirtError(_("You haven't defined any network pools"))

            if request.method == "POST":
                if "create" in request.POST:
                    firmware = dict()
                    volume_list = list()
                    is_disk_created = False
                    clone_path = ""
                    form = NewVMForm(request.POST)
                    if form.is_valid():
                        data = form.cleaned_data
                        if data["meta_prealloc"]:
                            meta_prealloc = True
                        if instances:
                            if data["name"] in instances:
                                raise libvirtError(
                                    _("A virtual machine with this name already exists")
                                )
                            if Instance.objects.filter(name__exact=data["name"]):
                                raise libvirtError(
                                    _(
                                        "There is an instance with same name. Remove it and try again!"
                                    )
                                )

                        if data["hdd_size"]:
                            if not data["mac"]:
                                raise libvirtError(
                                    _("No Virtual Machine MAC has been entered")
                                )
                            else:
                                path = conn.create_volume(
                                    data["storage"],
                                    data["name"],
                                    data["hdd_size"],
                                    default_disk_format,
                                    meta_prealloc,
                                    default_disk_owner_uid,
                                    default_disk_owner_gid,
                                )
                                volume = dict()
                                volume["device"] = "disk"
                                volume["path"] = path
                                volume["type"] = conn.get_volume_format_type(path)
                                volume["cache_mode"] = data["cache_mode"]
                                volume["bus"] = default_bus
                                if volume["bus"] == "scsi":
                                    volume["scsi_model"] = default_scsi_disk_model
                                volume["discard_mode"] = default_discard
                                volume["detect_zeroes_mode"] = default_zeroes
                                volume["io_mode"] = default_io

                                volume_list.append(volume)
                                is_disk_created = True

                        elif data["template"]:
                            templ_path = conn.get_volume_path(data["template"])
                            dest_vol = conn.get_volume_path(
                                data["name"] + ".img", data["storage"]
                            )
                            if dest_vol:
                                raise libvirtError(
                                    _(
                                        "Image has already exist. Please check volumes or change instance name"
                                    )
                                )
                            else:
                                clone_path = conn.clone_from_template(
                                    data["name"],
                                    templ_path,
                                    data["storage"],
                                    meta_prealloc,
                                    default_disk_owner_uid,
                                    default_disk_owner_gid,
                                )
                                volume = dict()
                                volume["path"] = clone_path
                                volume["type"] = conn.get_volume_format_type(clone_path)
                                volume["device"] = "disk"
                                volume["cache_mode"] = data["cache_mode"]
                                volume["bus"] = default_bus
                                if volume["bus"] == "scsi":
                                    volume["scsi_model"] = default_scsi_disk_model
                                volume["discard_mode"] = default_discard
                                volume["detect_zeroes_mode"] = default_zeroes
                                volume["io_mode"] = default_io

                                volume_list.append(volume)
                                is_disk_created = True
                        else:
                            if not data["images"]:
                                raise libvirtError(
                                    _("First you need to create or select an image")
                                )
                            else:
                                for idx, vol in enumerate(data["images"].split(",")):
                                    path = conn.get_volume_path(vol)
                                    volume = dict()
                                    volume["path"] = path
                                    volume["type"] = conn.get_volume_format_type(path)
                                    volume["device"] = request.POST.get(
                                        "device" + str(idx), ""
                                    )
                                    volume["bus"] = request.POST.get(
                                        "bus" + str(idx), ""
                                    )
                                    if volume["bus"] == "scsi":
                                        volume["scsi_model"] = default_scsi_disk_model
                                    volume["cache_mode"] = data["cache_mode"]
                                    volume["discard_mode"] = default_discard
                                    volume["detect_zeroes_mode"] = default_zeroes
                                    volume["io_mode"] = default_io

                                    volume_list.append(volume)
                        if data["cache_mode"] not in conn.get_cache_modes():
                            error_msg = _("Invalid cache mode")
                            raise libvirtError

                        if "UEFI" in data["firmware"]:
                            firmware["loader"] = data["firmware"].split(":")[1].strip()
                            firmware["secure"] = "no"
                            firmware["readonly"] = "yes"
                            firmware["type"] = "pflash"
                            if "secboot" in firmware["loader"] and machine != "q35":
                                messages.warning(
                                    request,
                                    "Changing machine type from '%s' to 'q35' "
                                    "which is required for UEFI secure boot." % machine,
                                )
                                machine = "q35"
                                firmware["secure"] = "yes"

                        if data["net_model"] == "default":
                            data["net_model"] = "virtio"

                        uuid = util.randomUUID()
                        try:
                            with libvirt_compute_lock(compute):
                                conn.create_instance(
                                    name=data["name"],
                                    memory=data["memory"],
                                    vcpu=data["vcpu"],
                                    vcpu_mode=data["vcpu_mode"],
                                    uuid=uuid,
                                    arch=arch,
                                    machine=machine,
                                    firmware=firmware,
                                    volumes=volume_list,
                                    networks=data["networks"],
                                    virtio=data["virtio"],
                                    listener_addr=data["listener_addr"],
                                    nwfilter=data["nwfilter"],
                                    net_model=data["net_model"],
                                    video=data["video"],
                                    console_pass=data["console_pass"],
                                    mac=data["mac"],
                                    qemu_ga=data["qemu_ga"],
                                    add_cdrom=data["add_cdrom"],
                                    add_input=data["add_input"],
                                )
                                create_instance = Instance.objects.get_or_create(
                                    compute_id=compute_id, uuid=uuid, defaults={"name": data["name"]}
                                )[0]
                                if create_instance.name != data["name"]:
                                    create_instance.name = data["name"]
                                    create_instance.save(update_fields=["name"])
                            msg = _("Instance is created")
                            messages.success(request, msg)
                            addlogmsg(
                                request.user.username,
                                create_instance.compute.name,
                                create_instance.name,
                                msg,
                            )
                            return redirect(
                                reverse("instances:instance", args=[create_instance.id])
                            )
                        except libvirtError as lib_err:
                            if data["hdd_size"] or len(volume_list) > 0:
                                if is_disk_created:
                                    for vol in volume_list:
                                        conn.delete_volume(vol["path"])
                            messages.error(request, lib_err)
            conn.close()
    except libvirtError as lib_err:
        messages.error(request, lib_err)

    return render(request, "create_instance_w2.html", locals())


@superuser_only
def flavor_create(request):
    form = FlavorForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, _("Flavor Created"))
        return get_safe_redirect(request, default=reverse("instances:index"))

    return render(
        request,
        "common/form.html",
        {"form": form, "title": _("Create Flavor")},
    )


@superuser_only
def flavor_update(request, pk):
    flavor = get_object_or_404(Flavor, pk=pk)
    form = FlavorForm(request.POST or None, instance=flavor)
    if form.is_valid():
        form.save()
        messages.success(request, _("Flavor Updated"))
        return get_safe_redirect(request, default=reverse("instances:index"))

    return render(
        request,
        "common/form.html",
        {"form": form, "title": _("Update Flavor")},
    )


@superuser_only
def flavor_delete(request, pk):
    flavor = get_object_or_404(Flavor, pk=pk)
    if request.method == "POST":
        flavor.delete()
        messages.success(request, _("Flavor Deleted"))
        return get_safe_redirect(request, default=reverse("instances:index"))

    return render(
        request,
        "common/confirm_delete.html",
        {"object": flavor},
    )
