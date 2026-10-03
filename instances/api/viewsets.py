from admin.permissions import IsSuperUser
from appsettings.settings import app_settings
from computes import utils
from computes.models import Compute
from django.http import Http404
from django.shortcuts import get_object_or_404
from instances.models import Flavor, Instance
from instances.utils import migrate_instance
from instances.views import destroy as instance_destroy
from instances.views import (
    force_off,
    get_instance,
    powercycle,
    poweroff,
    poweron,
    resume,
    suspend,
)
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from vrtManager import util
from vrtManager.create import wvmCreate
from webvirtcloud.serializers import StatusSerializer

from .serializers import (
    CreateInstanceSerializer,
    FlavorSerializer,
    InstanceDetailsSerializer,
    InstanceSerializer,
    MigrateSerializer,
)


def instance_action_response(action, request, pk, message):
    response = action(request, pk)
    if response.status_code >= 400:
        return response
    return Response({"status": message})


class InstancesViewSet(viewsets.ViewSet):
    """
    A simple ViewSet for listing or retrieving ALL/Compute Instances.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = InstanceSerializer
    lookup_value_converter = "int"

    def list(self, request):

        if request.user.is_superuser or request.user.has_perm(
            "instances.view_instances"
        ):
            queryset = Instance.objects.all().prefetch_related("userinstance_set")
        else:
            queryset = Instance.objects.filter(
                userinstance__user=request.user
            ).prefetch_related("userinstance_set")
        serializer = InstanceSerializer(
            queryset, many=True, context={"request": request}
        )

        return Response(serializer.data)

    def retrieve(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk)
        serializer = InstanceSerializer(queryset, context={"request": request})

        return Response(serializer.data)


class InstanceViewSet(viewsets.ViewSet):
    """
    A simple ViewSet for listing or retrieving Compute Instances.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = InstanceSerializer
    lookup_value_converter = "int"

    def list(self, request, compute_pk=None):
        compute = get_object_or_404(Compute, pk=compute_pk)

        is_admin = request.user.is_superuser or request.user.has_perm(
            "instances.view_instances"
        )

        if not is_admin:
            if not Instance.objects.filter(
                compute=compute, userinstance__user=request.user
            ).exists():
                return Response([])

        utils.refresh_instance_database(compute)

        if is_admin:
            queryset = Instance.objects.filter(compute=compute).prefetch_related(
                "userinstance_set"
            )
        else:
            queryset = Instance.objects.filter(
                compute=compute, userinstance__user=request.user
            ).prefetch_related("userinstance_set")
        serializer = InstanceSerializer(
            queryset, many=True, context={"request": request}
        )

        return Response(serializer.data)

    @extend_schema(responses=InstanceDetailsSerializer)
    def retrieve(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk)
        if compute_pk is not None and str(queryset.compute_id) != str(compute_pk):
            raise Http404()
        serializer = InstanceDetailsSerializer(queryset, context={"request": request})

        return Response(serializer.data)

    @extend_schema(responses=StatusSerializer)
    def destroy(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk, perm_type="delete")
        if compute_pk is not None and str(queryset.compute_id) != str(compute_pk):
            raise Http404()
        return instance_action_response(instance_destroy, request, pk, "Instance is destroyed")

    @extend_schema(request=None, responses=StatusSerializer)
    @action(detail=True, methods=["post"])
    def poweron(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk, perm_type="power")
        if compute_pk is not None and str(queryset.compute_id) != str(compute_pk):
            raise Http404()
        return instance_action_response(poweron, request, pk, "poweron command send")

    @extend_schema(request=None, responses=StatusSerializer)
    @action(detail=True, methods=["post"])
    def poweroff(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk, perm_type="power")
        if compute_pk is not None and str(queryset.compute_id) != str(compute_pk):
            raise Http404()
        return instance_action_response(poweroff, request, pk, "poweroff command send")

    @extend_schema(request=None, responses=StatusSerializer)
    @action(detail=True, methods=["post"])
    def powercycle(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk, perm_type="power")
        if compute_pk is not None and str(queryset.compute_id) != str(compute_pk):
            raise Http404()
        return instance_action_response(powercycle, request, pk, "powercycle command send")

    @extend_schema(request=None, responses=StatusSerializer)
    @action(detail=True, methods=["post"])
    def forceoff(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk, perm_type="power")
        if compute_pk is not None and str(queryset.compute_id) != str(compute_pk):
            raise Http404()
        return instance_action_response(force_off, request, pk, "force off command send")

    @extend_schema(request=None, responses=StatusSerializer)
    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk)
        if compute_pk is not None and str(queryset.compute_id) != str(compute_pk):
            raise Http404()
        return instance_action_response(suspend, request, pk, "suspend command send")

    @extend_schema(request=None, responses=StatusSerializer)
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None, compute_pk=None):
        queryset = get_instance(request.user, pk)
        if compute_pk is not None and str(queryset.compute_id) != str(compute_pk):
            raise Http404()
        return instance_action_response(resume, request, pk, "resume command send")


class MigrateViewSet(viewsets.ViewSet):
    """
    A viewset for migrating instances.
    """

    permission_classes = [IsSuperUser]
    serializer_class = MigrateSerializer
    queryset = ""

    @extend_schema(responses=StatusSerializer)
    def create(self, request):
        serializer = MigrateSerializer(data=request.data)
        if serializer.is_valid():
            instance = serializer.validated_data["instance"]
            target_host = serializer.validated_data["target_compute"]
            live = serializer.validated_data["live"]
            unsafe = serializer.validated_data["unsafe"]
            xml_del = serializer.validated_data["xml_del"]
            offline = serializer.validated_data["offline"]
            autoconverge = serializer.validated_data["autoconverge"]
            postcopy = serializer.validated_data["postcopy"]
            compress = serializer.validated_data["compress"]

            migrate_instance(
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

            return Response({"status": "instance migrate is started"})
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class FlavorViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows flavor to be viewed or edited.
    """

    queryset = Flavor.objects.all().order_by("id")
    lookup_value_converter = "int"
    serializer_class = FlavorSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [permissions.IsAuthenticated()]
        return [IsSuperUser()]


class CreateInstanceViewSet(viewsets.ViewSet):
    """
    A viewset for creating instances.
    """

    permission_classes = [IsSuperUser]
    serializer_class = CreateInstanceSerializer
    queryset = ""

    @extend_schema(responses=StatusSerializer)
    def create(self, request, compute_pk=None, arch=None, machine=None):
        serializer = CreateInstanceSerializer(
            data=request.data,
            context={"compute_pk": compute_pk, "arch": arch, "machine": machine},
        )
        if serializer.is_valid():
            volume_list = []
            default_bus = app_settings.INSTANCE_VOLUME_DEFAULT_BUS
            default_io = app_settings.INSTANCE_VOLUME_DEFAULT_IO
            default_discard = app_settings.INSTANCE_VOLUME_DEFAULT_DISCARD
            default_zeroes = app_settings.INSTANCE_VOLUME_DEFAULT_DETECT_ZEROES
            default_scsi_disk_model = (
                app_settings.INSTANCE_VOLUME_DEFAULT_SCSI_CONTROLLER
            )
            default_disk_format = app_settings.INSTANCE_VOLUME_DEFAULT_FORMAT
            default_disk_owner_uid = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_UID)
            default_disk_owner_gid = int(app_settings.INSTANCE_VOLUME_DEFAULT_OWNER_GID)
            compute = Compute.objects.get(pk=compute_pk)
            conn = wvmCreate(
                compute.hostname,
                compute.login,
                compute.password,
                compute.type,
            )

            path = conn.create_volume(
                serializer.validated_data["storage"],
                serializer.validated_data["name"],
                serializer.validated_data["hdd_size"],
                default_disk_format,
                serializer.validated_data["meta_prealloc"],
                default_disk_owner_uid,
                default_disk_owner_gid,
            )
            volume = {}
            volume["device"] = "disk"
            volume["path"] = path
            volume["type"] = conn.get_volume_format_type(path)
            volume["cache_mode"] = serializer.validated_data["cache_mode"]
            volume["bus"] = default_bus
            if volume["bus"] == "scsi":
                volume["scsi_model"] = default_scsi_disk_model
            volume["discard_mode"] = default_discard
            volume["detect_zeroes_mode"] = default_zeroes
            volume["io_mode"] = default_io

            volume_list.append(volume)

            with utils.libvirt_compute_lock(compute):
                conn.create_instance(
                    name=serializer.validated_data["name"],
                    memory=serializer.validated_data["memory"],
                    vcpu=serializer.validated_data["vcpu"],
                    vcpu_mode=serializer.validated_data["vcpu_mode"],
                    uuid=util.randomUUID(),
                    arch=arch,
                    machine=machine,
                    firmware={},
                    volumes=volume_list,
                    networks=serializer.validated_data["networks"],
                    nwfilter=serializer.validated_data["nwfilter"],
                    graphics=serializer.validated_data["graphics"],
                    virtio=serializer.validated_data["virtio"],
                    listener_addr=serializer.validated_data["listener_addr"],
                    video=serializer.validated_data["video"],
                    console_pass=serializer.validated_data["console_pass"],
                    mac=serializer.validated_data["mac"],
                    qemu_ga=serializer.validated_data["qemu_ga"],
                )
                utils.refresh_instance_database(compute)
            msg = f"Instance {serializer.validated_data['name']} is created"
            return Response({"status": msg})
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
