from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseRedirect
from django.utils.translation import ugettext_lazy as _
from django.urls import reverse
from django.contrib import messages
from libvirt import libvirtError
from computes.models import Compute
from create.models import Flavor
from create.forms import FlavorAddForm, NewVMForm
from instances.models import Instance
from vrtManager.create import wvmCreate
from vrtManager import util
from logs.views import addlogmsg

from webvirtcloud.settings import QEMU_CONSOLE_LISTEN_ADDRESSES
from webvirtcloud.settings import INSTANCE_VOLUME_DEFAULT_CACHE
from webvirtcloud.settings import INSTANCE_VOLUME_DEFAULT_BUS
from webvirtcloud.settings import INSTANCE_CPU_DEFAULT_MODE
from webvirtcloud.settings import INSTANCE_MACHINE_DEFAULT_TYPE
from webvirtcloud.settings import QEMU_CONSOLE_DEFAULT_TYPE
from webvirtcloud.settings import INSTANCE_VOLUME_DEFAULT_IO
from webvirtcloud.settings import INSTANCE_VOLUME_DEFAULT_DETECT_ZEROES
from webvirtcloud.settings import INSTANCE_VOLUME_DEFAULT_DISCARD
from webvirtcloud.settings import INSTANCE_ARCH_DEFAULT_TYPE
from webvirtcloud.settings import INSTANCE_FIRMWARE_DEFAULT_TYPE



def create_instance_select_type(request, compute_id):
    """
    :param request:
    :param compute_id:
    :return:
    """
    if not request.user.is_superuser:
        return HttpResponseRedirect(reverse('index'))

    conn = None
    error_messages = list()
    storages = list()
    networks = list()
    hypervisors = list()
    meta_prealloc = False
    compute = get_object_or_404(Compute, pk=compute_id)

    try:
        conn = wvmCreate(compute.hostname,
                         compute.login,
                         compute.password,
                         compute.type)
        instances = conn.get_instances()
        all_hypervisors = conn.get_hypervisors_machines()
        # Supported hypervisors by webvirtcloud: i686, x86_64(for now)
        supported_arch = ["x86_64", "i686", "aarch64", "armv7l", "ppc64", "ppc64le", "s390x"]
        hypervisors = [hpv for hpv in all_hypervisors.keys() if hpv in supported_arch]
        default_machine = INSTANCE_MACHINE_DEFAULT_TYPE
        default_arch = INSTANCE_ARCH_DEFAULT_TYPE

        if request.method == 'POST':
            if 'create_xml' in request.POST:
                xml = request.POST.get('dom_xml', '')
                try:
                    name = util.get_xml_path(xml, '/domain/name')
                except util.etree.Error as err:
                    name = None
                if name in instances:
                    error_msg = _("A virtual machine with this name already exists")
                    error_messages.append(error_msg)
                else:
                    try:
                        conn._defineXML(xml)
                        return HttpResponseRedirect(reverse('instance', args=[compute_id, name]))
                    except libvirtError as lib_err:
                        error_messages.append(lib_err)

    except libvirtError as lib_err:
        error_messages.append(lib_err)

    return render(request, 'create_instance_w1.html', locals())


def create_instance(request, compute_id, arch, machine):
    """
    :param request:
    :param compute_id:
    :param arch:
    :param machine:
    :return:
    """
    if not request.user.is_superuser:
        return HttpResponseRedirect(reverse('index'))

    conn = None
    error_messages = list()
    storages = list()
    networks = list()
    hypervisors = list()
    firmwares = list()
    meta_prealloc = False
    compute = get_object_or_404(Compute, pk=compute_id)
    flavors = Flavor.objects.filter().order_by('id')

    try:
        conn = wvmCreate(compute.hostname,
                         compute.login,
                         compute.password,
                         compute.type)

        default_firmware = INSTANCE_FIRMWARE_DEFAULT_TYPE
        default_cpu_mode = INSTANCE_CPU_DEFAULT_MODE
        instances = conn.get_instances()
        videos = conn.get_video_models(arch, machine)
        cache_modes = sorted(conn.get_cache_modes().items())
        default_cache = INSTANCE_VOLUME_DEFAULT_CACHE.lower()
        default_io = INSTANCE_VOLUME_DEFAULT_IO.lower()
        default_zeroes = INSTANCE_VOLUME_DEFAULT_DETECT_ZEROES.lower()
        default_discard = INSTANCE_VOLUME_DEFAULT_DISCARD.lower()
        listener_addr = QEMU_CONSOLE_LISTEN_ADDRESSES
        mac_auto = util.randomMAC()
        disk_devices = conn.get_disk_device_types(arch, machine)
        disk_buses = conn.get_disk_bus_types(arch, machine)
        default_bus = INSTANCE_VOLUME_DEFAULT_BUS
        networks = sorted(conn.get_networks())
        nwfilters = conn.get_nwfilters()
        storages = sorted(conn.get_storages(only_actives=True))
        default_graphics = QEMU_CONSOLE_DEFAULT_TYPE

        dom_caps = conn.get_dom_capabilities(arch, machine)
        caps = conn.get_capabilities(arch)

        virtio_support = conn.is_supports_virtio(arch, machine)
        hv_supports_uefi = conn.supports_uefi_xml(dom_caps["loader_enums"])
        # Add BIOS
        label = conn.label_for_firmware_path(arch, None)
        if label: firmwares.append(label)
        # Add UEFI
        loader_path = conn.find_uefi_path_for_arch(arch, dom_caps["loaders"])
        label = conn.label_for_firmware_path(arch, loader_path)
        if label: firmwares.append(label)
        firmwares = list(set(firmwares))

    except libvirtError as lib_err:
        error_messages.append(lib_err)

    if conn:
        if not storages:
            msg = _("You haven't defined any storage pools")
            error_messages.append(msg)
        if not networks:
            msg = _("You haven't defined any network pools")
            error_messages.append(msg)

        if request.method == 'POST':
            if 'create_flavor' in request.POST:
                form = FlavorAddForm(request.POST)
                if form.is_valid():
                    data = form.cleaned_data
                    create_flavor = Flavor(label=data['label'],
                                           vcpu=data['vcpu'],
                                           memory=data['memory'],
                                           disk=data['disk'])
                    create_flavor.save()
                    return HttpResponseRedirect(request.get_full_path())
            if 'delete_flavor' in request.POST:
                flavor_id = request.POST.get('flavor', '')
                delete_flavor = Flavor.objects.get(id=flavor_id)
                delete_flavor.delete()
                return HttpResponseRedirect(request.get_full_path())
            if 'create' in request.POST:
                firmware = dict()
                volume_list = list()
                is_disk_created = False
                clone_path = ""
                form = NewVMForm(request.POST)
                if form.is_valid():
                    data = form.cleaned_data
                    if data['meta_prealloc']:
                        meta_prealloc = True
                    if instances:
                        if data['name'] in instances:
                            msg = _("A virtual machine with this name already exists")
                            error_messages.append(msg)
                        if Instance.objects.filter(name__exact=data['name']):
                            messages.warning(request, _("There is an instance with same name. Are you sure?"))
                    if not error_messages:
                        if data['hdd_size']:
                            if not data['mac']:
                                error_msg = _("No Virtual Machine MAC has been entered")
                                error_messages.append(error_msg)
                            else:
                                try:
                                    path = conn.create_volume(data['storage'], data['name'], data['hdd_size'],
                                                              metadata=meta_prealloc)
                                    volume = dict()
                                    volume['path'] = path
                                    volume['type'] = conn.get_volume_type(path)
                                    volume['device'] = 'disk'
                                    if data['virtio']:
                                        volume['bus'] = INSTANCE_VOLUME_DEFAULT_BUS
                                    volume_list.append(volume)
                                    is_disk_created = True
                                except libvirtError as lib_err:
                                    error_messages.append(lib_err)
                        elif data['template']:
                            templ_path = conn.get_volume_path(data['template'])
                            dest_vol = conn.get_volume_path(data["name"] + ".img", data['storage'])
                            if dest_vol:
                                error_msg = _("Image has already exist. Please check volumes or change instance name")
                                error_messages.append(error_msg)
                            else:
                                clone_path = conn.clone_from_template(data['name'], templ_path, data['storage'], metadata=meta_prealloc)
                                volume = dict()
                                volume['path'] = clone_path
                                volume['type'] = conn.get_volume_type(clone_path)
                                volume['device'] = 'disk'
                                if data['virtio']:
                                    volume['bus'] = INSTANCE_VOLUME_DEFAULT_BUS
                                volume_list.append(volume)
                                is_disk_created = True
                        else:
                            if not data['images']:
                                error_msg = _("First you need to create or select an image")
                                error_messages.append(error_msg)
                            else:
                                for idx, vol in enumerate(data['images'].split(',')):
                                    try:
                                        path = conn.get_volume_path(vol)
                                        volume = dict()
                                        volume['path'] = path
                                        volume['type'] = conn.get_volume_type(path)
                                        volume['device'] = request.POST.get('device' + str(idx), '')
                                        volume['bus'] = request.POST.get('bus' + str(idx), '')
                                        volume_list.append(volume)
                                    except libvirtError as lib_err:
                                        error_messages.append(lib_err)
                        if data['cache_mode'] not in conn.get_cache_modes():
                            error_msg = _("Invalid cache mode")
                            error_messages.append(error_msg)

                        if 'UEFI' in data["firmware"]:
                            firmware["loader"] = data["firmware"].split(":")[1].strip()
                            firmware["secure"] = 'no'
                            firmware["readonly"] = 'yes'
                            firmware["type"] = 'pflash'
                            if 'secboot' in firmware["loader"] and machine != 'q35':
                                messages.warning(request, "Changing machine type from '%s' to 'q35' "
                                                          "which is required for UEFI secure boot." % machine)
                                machine = 'q35'
                                firmware["secure"] = 'yes'

                        if not error_messages:
                            uuid = util.randomUUID()
                            try:
                                conn.create_instance(name=data['name'], memory=data['memory'], vcpu=data['vcpu'],
                                                     vcpu_mode=data['vcpu_mode'], uuid=uuid, arch=arch, machine=machine,
                                                     firmware=firmware,
                                                     images=volume_list, cache_mode=data['cache_mode'],
                                                     io_mode=default_io, discard_mode=default_discard, detect_zeroes_mode=default_zeroes,
                                                     networks=data['networks'], virtio=data['virtio'],
                                                     listen_addr=data["listener_addr"], nwfilter=data["nwfilter"],
                                                     graphics=data["graphics"], video=data["video"],
                                                     console_pass=data["console_pass"], mac=data['mac'],
                                                     qemu_ga=data['qemu_ga'])
                                create_instance = Instance(compute_id=compute_id, name=data['name'], uuid=uuid)
                                create_instance.save()
                                msg = _("Instance is created.")
                                messages.success(request, msg)
                                addlogmsg(request.user.username, create_instance.name, msg)
                                return HttpResponseRedirect(reverse('instance', args=[compute_id, data['name']]))
                            except libvirtError as lib_err:
                                if data['hdd_size'] or len(volume_list) > 0:
                                    if is_disk_created:
                                        for vol in volume_list:
                                            conn.delete_volume(vol['path'])
                                error_messages.append(lib_err)
        conn.close()
    return render(request, 'create_instance_w2.html', locals())
