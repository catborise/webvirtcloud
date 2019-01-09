from django.db import models
from computes.models import Compute


class Instance(models.Model):
    compute = models.ForeignKey(Compute, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    uuid = models.CharField(max_length=36)
    is_template = models.BooleanField(default=False)
    created = models.DateField(auto_now_add=True)

    def __unicode__(self):
        return self.name


class Disk(models.Model):
    instance = models.ForeignKey(Instance, on_delete=models.CASCADE)
    source = models.CharField(max_length=250, help_text="volume file path/source")
    dev = models.CharField(max_length=5, help_text="device")
    bus = models.CharField(max_length=10, help_text="volume bus")
    format = models.CharField(max_length=10, help_text="volume format")

    def __unicode__(self):
        return self.name
