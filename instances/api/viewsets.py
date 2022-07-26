from dataclasses import fields

from django.shortcuts import get_object_or_404
from computes.models import Compute
from computes import utils
from instances.models import Instance
from rest_framework import status, viewsets
from rest_framework import permissions
from rest_framework.decorators import action

from instances.utils import migrate_instance
from vrtManager import util

from .serializers import InstanceSerializer, MigrateSerializer
from instances.views import get_instance
from rest_framework.response import Response
from instances.views import poweron, powercycle, poweroff, force_off, suspend, resume, destroy, migrate


class InstanceViewSet(viewsets.ViewSet):

    """
    A simple ViewSet for listing or retrieving ALL/Compute Instances.
    """
    def list(self, request):

        if request.user.is_superuser or request.user.has_perm("instances.view_instances"):
            queryset = Instance.objects.all().prefetch_related("userinstance_set")
        else:
            queryset = Instance.objects.filter(userinstance__user=request.user).prefetch_related("userinstance_set")
        serializer = InstanceSerializer(queryset, many=True, context={'request': request})

        return Response(serializer.data)

 
    def list(self, request, compute_pk=None):
        compute = get_object_or_404(Compute, pk=compute_pk)

        utils.refresh_instance_database(compute)
        
        queryset = Instance.objects.filter(compute=compute).prefetch_related("userinstance_set")
        serializer = InstanceSerializer(queryset, many=True, context={'request': request})
        
        return Response(serializer.data)


    def retrieve(self, request, pk=None, compute_pk=None):
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
    

class MigrateViewSet(viewsets.ViewSet):
    """
    A viewset for migrating instances.
    """
    serializer_class = MigrateSerializer
    queryset = ""

    def create(self, request):
        serializer = MigrateSerializer(data=request.data)
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
        


