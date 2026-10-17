#!/bin/sh
# The serial console daemon; it refuses connections while SERIAL_CONSOLE_ENABLED is off.

cd /srv/webvirtcloud || exit 1
exec /sbin/setuser www-data /srv/webvirtcloud/venv/bin/python3 /srv/webvirtcloud/console/socketiod >> /var/log/socketiod.log 2>&1
