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


class Snapshot(models.Model):
    id = models.AutoField(primary_key=True)
    instance = models.ForeignKey(Instance, on_delete=models.CASCADE)
    display_name = models.CharField("snapshot name", unique=True, max_length=25)
    num_disk = models.IntegerField("number of disks")
    date = models.DateTimeField("creation Time")
    description = models.CharField("snapshot description", blank=True, null=True, max_length=200)
    deleted = models.BooleanField("has it deletable disks", default=False)
    current = models.BooleanField("is it current snapshot")
    parent = models.CharField("snapshot parent", max_length=25, null=True, blank=True)

    def __unicode__(self):
        return self.display_name


class SnapshotDisk(models.Model):
    snapshot = models.ForeignKey(Snapshot, on_delete=models.CASCADE)
    snap_type = models.CharField("snapshot type", blank=True, max_length=15)
    source = models.FilePathField("disk filename with path")
    dev = models.CharField("disk dev name", max_length=5)
    driver = models.CharField("disk driver type", blank=True, max_length=5)
    parent = models.CharField("disk backing file", blank=True, max_length=200)
    type = models.CharField("disk type", blank=True, max_length=5)


