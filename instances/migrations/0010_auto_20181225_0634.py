# -*- coding: utf-8 -*-
# Generated by Django 1.11.14 on 2018-12-25 06:34
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('instances', '0009_auto_20181224_1241'),
    ]

    operations = [
        migrations.RenameField(
            model_name='snapshotdisk',
            old_name='filename',
            new_name='source',
        ),
    ]
