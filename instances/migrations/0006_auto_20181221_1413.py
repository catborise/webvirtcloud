# -*- coding: utf-8 -*-
# Generated by Django 1.11.14 on 2018-12-21 14:13
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('instances', '0005_snapshot_snapshotdisk'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='snapshotdisk',
            name='bus',
        ),
        migrations.AddField(
            model_name='snapshotdisk',
            name='snap_type',
            field=models.CharField(blank=True, max_length=15, verbose_name=b'snapshot type'),
        ),
        migrations.AddField(
            model_name='snapshotdisk',
            name='type',
            field=models.CharField(blank=True, max_length=5, verbose_name=b'disk type'),
        ),
        migrations.AlterField(
            model_name='snapshot',
            name='current',
            field=models.CharField(max_length=5, verbose_name=b'current snapshot id'),
        ),
        migrations.AlterField(
            model_name='snapshot',
            name='description',
            field=models.CharField(blank=True, max_length=200, verbose_name=b'snapshot description'),
        ),
        migrations.AlterField(
            model_name='snapshot',
            name='display_name',
            field=models.CharField(max_length=25, unique=True, verbose_name=b'snapshot name'),
        ),
    ]
