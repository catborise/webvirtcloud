#!/bin/bash
FILEPATH=$(readlink -f "$0");
SCRIPTPATH=$(dirname "$FILEPATH");
cd "$SCRIPTPATH"
cp webvirt-* /lib/systemd/system/
cp webvirt /etc/default/
# webvirt-socketiod (serial console) is not enabled by default: it does not
# authenticate connections yet and refuses to start unless
# SERIAL_CONSOLE_ENABLED = True is set in settings.py.
echo Run to start services \"systemctl daemon-reload\; systemctl enable --now $(ls webvirt-* | grep -v socketiod | tr "\n" " ")\"
