from computes.models import Compute
from rest_framework import viewsets
from rest_framework import permissions
from .serializers import ComputeSerializer


class ComputeViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows computes to be viewed or edited.
    """
    queryset = Compute.objects.all().order_by('name')
    serializer_class = ComputeSerializer
    permission_classes = [permissions.IsAuthenticated]

