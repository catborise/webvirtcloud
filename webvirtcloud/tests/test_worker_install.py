"""Every install type runs the worker as www-data, with its own log file and
a stop timeout above the worker's 60 s grace."""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)


class WorkerInstallTestCase(SimpleTestCase):
    def test_docker(self):
        dockerfile = (BASE / "Dockerfile").read_text()
        self.assertIn("conf/runit/webvirtcloud-worker.sh", dockerfile)
        self.assertIn("/etc/service/webvirtcloud-worker/run", dockerfile)
        script = (BASE / "conf/runit/webvirtcloud-worker.sh").read_text()
        for text in ("/sbin/setuser www-data", "manage.py run_worker",
                     "WEBVIRTCLOUD_LOG_FILE=/srv/webvirtcloud/data/webvirtcloud-worker.log"):
            self.assertIn(text, script)
        self.assertIn("ENV KILL_PROCESS_TIMEOUT=80", dockerfile)
        compose = (BASE / "docker-compose.yml").read_text()
        self.assertRegex(compose, r"stop_grace_period:\s*9\ds")
        # the worker's host identity lives in the data volume, not in a fixed hostname
        self.assertIn("webvirtcloud-data:/srv/webvirtcloud/data", compose)
        self.assertNotIn("hostname:", compose)

    def test_supervisor(self):
        conf = (BASE / "conf/supervisor/webvirtcloud.conf").read_text()
        block = re.search(r"\[program:webvirt-worker\](.*?)(\n\[|\Z)", conf, re.S).group(1)
        for text in ("manage.py run_worker", "user=www-data", "webvirtcloud-worker.log", "autorestart=true"):
            self.assertIn(text, block)
        self.assertRegex(block, r"stopwaitsecs=9\d")

    def test_systemd_is_an_alternative(self):
        unit = (BASE / "conf/systemd/webvirt-worker.service").read_text()
        self.assertIn("run_worker", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertRegex(unit, r"TimeoutStopSec=9\d")
        self.assertIn("one of the two", (BASE / "conf/systemd/README.md").read_text())
