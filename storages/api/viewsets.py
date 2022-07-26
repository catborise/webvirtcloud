from dataclasses import fields

from django.shortcuts import get_object_or_404
from computes.models import Compute
from instances.models import Instance
from rest_framework import status, viewsets
from rest_framework import permissions
from rest_framework.decorators import action

from instances.utils import migrate_instance
from vrtManager.storage import wvmStorages, wvmStorage

from .serializers import StoragesSerializer, VolumeSerializer
from ..models import Volume
from rest_framework.response import Response
from instances.views import poweron, powercycle, poweroff, force_off, suspend, resume, destroy, migrate


class StorageViewSet(viewsets.ViewSet):
    """
    A viewset for listing retrieving storages.
    """
    
    def list(self, request, compute_pk=None):
        
        compute = get_object_or_404(Compute, pk=compute_pk)
    
        conn = wvmStorages(compute.hostname, compute.login, compute.password, compute.type)
        queryset = conn.get_storages_info()

        serializer = StoragesSerializer(queryset, many=True, context={'request': request})        

        return Response(serializer.data)
        
    def retrieve(self, request, pk=None, compute_pk=None):
        compute = get_object_or_404(Compute, pk=compute_pk)

        conn = wvmStorage(compute.hostname, compute.login, compute.password, compute.type, pk)

        storages = conn.get_storages()
        state = conn.is_active()
        size, free = conn.get_size()
        used = size - free
        if state:
            percent = (used * 100) // size
        else:
            percent = 0
        status = conn.get_status()
        path = conn.get_target_path()
        type = conn.get_type()
        autostart = conn.get_autostart()

        if state:
            conn.refresh()
            volumes = conn.update_volumes()
        else:
            volumes = None

    def create(self, request):
        serializer = StorageSerializer(data=request.data)
        if serializer.is_valid():
            instance = serializer.validated_data['instance']
            target_host = serializer.validated_data['target_compute']
            live = serializer.validated_data['live']
            unsafe = serializer.validated_data['unsafe']
            xml_del = serializer.validated_data['xml_del']
            offline = serializer.validated_data['offline']
            autoconverge = serializer.validated_data['autoconverge']
            postcopy  = serializer.validated_data['postcopy']
            compress = serializer.validated_data['compress']

            migrate_instance(target_host, instance, request.user, live, unsafe, xml_del, offline, autoconverge, compress, postcopy)

            return Response({'status': 'instance migrate is started'})
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)    


class VolumeViewSet(viewsets.ViewSet):

    """
    A simple ViewSet for listing or retrieving Storage Volumes.
    """
    def list(self, request):
        q = {}
        
        queryset = Instance.objects.all().prefetch_related("userinstance_set")
        serializer = VolumeSerializer(queryset, many=True, context={'request': request})
        
        return Response(serializer.data)


    def retrieve(self, request, pk=None):
        queryset = get_instance(request.user, pk)
        
        serializer = InstanceSerializer(queryset, context={'request': request})

        return Response(serializer.data)
    

    @action(detail=True, methods=['post'])
    def poweron(self, request, pk=None):
        poweron(request, pk)
        
        return Response({'status': 'poweron command send'})
    
    @action(detail=True, methods=['post'])
    def poweroff(self, request, pk=None):
        poweroff(request, pk)
        
        return Response({'status': 'poweroff command send'})
    
    @action(detail=True, methods=['post'])
    def powercycle(self, request, pk=None):
        powercycle(request, pk)
        
        return Response({'status': 'powercycle command send'})
    
    @action(detail=True, methods=['post'])
    def forceoff(self, request, pk=None):
        force_off(request, pk)
        
        return Response({'status': 'force off command send'})

    @action(detail=True, methods=['post'])
    def suspend(self, request, pk=None):
        suspend(request, pk)
        
        return Response({'status': 'suspend command send'})

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        resume(request, pk)
        
        return Response({'status': 'resume command send'})
    
    @action(detail=True, methods=['post'])
    def instancedestroy(self, request, pk=None):
        destroy(request, pk)
        
        return Response({'status': 'instance destroy command send'})
    

        


