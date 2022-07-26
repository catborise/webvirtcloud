
from statistics import mode
from rest_framework import serializers
from storages.models import Storages, Volume
from storages.models import Volume

class StoragesSerializer(serializers.ModelSerializer):
        
    class Meta:
        model = Storages
        fields = ['name', 'status', 'type', 'size', 'volumes'] 


class VolumeSerializer(serializers.ModelSerializer):
    instance = Volume.objects.all().prefetch_related("userinstance_set")
    live = serializers.BooleanField(initial=True)
    class Meta:
        model = Volume
        fields = ['instance', 'target_compute', 'live', 'xml_del', 'offline', 'autoconverge', 'compress', 'postcopy', 'unsafe'] 
