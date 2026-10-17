#!/bin/sh

cd /srv/webvirtcloud || exit 1
exec /sbin/setuser www-data /srv/webvirtcloud/venv/bin/python3 /srv/webvirtcloud/console/novncd >> /var/log/novncd.log 2>&1
