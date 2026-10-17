"""Every install type runs the worker as the web server's user (www-data, or
nginx where the installer and README substitute it), with its own log file
and a stop timeout above the worker's 60 s grace."""

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

    def test_systemd(self):
        unit = (BASE / "conf/systemd/webvirt-worker.service").read_text()
        for text in ("manage.py run_worker", "User=www-data", "webvirtcloud-worker.log", "KillMode=mixed",
                     "EnvironmentFile=-/etc/default/webvirtcloud", "Restart=on-failure"):
            self.assertIn(text, unit)
        self.assertRegex(unit, r"TimeoutStopSec=9\d")
