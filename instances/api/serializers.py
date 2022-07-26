
from statistics import mode
from rest_framework import serializers
from instances.models import Instance, MigrateInstance


class InstanceSerializer(serializers.HyperlinkedModelSerializer):
    
    class Meta:
        model = Instance
        fields = ['id', 'compute', 'name', 'uuid', 'is_template', 'created', 'drbd']


class MigrateSerializer(serializers.ModelSerializer):
    instance = Instance.objects.all().prefetch_related("userinstance_set")
    live = serializers.BooleanField(initial=True)
    class Meta:
        model = MigrateInstance
        fields = ['instance', 'target_compute', 'live', 'xml_del', 'offline', 'autoconverge', 'compress', 'postcopy', 'unsafe'] 
