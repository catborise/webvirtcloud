from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.functional import cached_property
from computes.models import Compute

from vrtManager.storage import wvmStorage, wvmStorages

# Create your models here.
class Storages(models.Model):
    name = models.CharField(_('name'), max_length=20, error_messages={'required': _('No pool name has been entered')})
    status = models.CharField(_('status'), max_length=12)
    type = models.CharField(_('type'), max_length=100)
    size = models.IntegerField(_('size'))
    volumes = models.IntegerField(_('volumes'))

    #objects = StoragesManager()

    def __str__(self):
        return f'{self.compute}/{self.name}'




class Storage(models.Model):
    state = models.CharField(_('state'), max_length=128)
    size = models.IntegerField(_('size'), max_length=128)
    free = models.IntegerField(_('free'), max_length=12, choices=(('qcow2', 'qcow2 (recommended)'), ('qcow', 'qcow'), ('raw', 'raw')))
    used = models.IntegerField(_('used'))
    percent = models.IntegerField(_('percent'), blank=True)
    status = models.IntegerField(_('status'), max_length=128)
    path = models.CharField(_('path'), max_length=128)
    type = models.CharField(_('type'), max_length=128)
    autostart = models.CharField(_('autostart'), max_length=128)
    

    #objects = VolumeManager()

    def __str__(self):
        return f'{self.storage}/{self.name}'

    class Meta:
        managed = False



class Volume(models.Model):
    storage = models.ForeignKey(Storage, on_delete=models.DO_NOTHING)
    name = models.CharField(_('name'), max_length=128)
    format = models.CharField(_('format'), max_length=12, choices=(('qcow2', 'qcow2 (recommended)'), ('qcow', 'qcow'), ('raw', 'raw')))
    size = models.IntegerField(_('size'))
    meta_prealloc = models.BooleanField(_('meta preallocation'), blank=True)

    #objects = VolumeManager()

    def __str__(self):
        return f'{self.storage}/{self.name}'

    class Meta:
        managed = False

    @cached_property
    def proxy(self):
        return wvmStorage(
            self.compute.hostname,
            self.compute.login,
            self.compute.password,
            self.compute.type,
            self.storage,
        )