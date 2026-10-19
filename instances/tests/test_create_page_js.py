"""Scripts of the VM create page, rendered on libvirt's test driver and run
in Node against stand-ins."""

import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

import libvirt
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class CreatePageScriptTests(TestCase):
    def page(self):
        conn = libvirt.open("test:///default")
        self.addCleanup(conn.close)
        compute = Compute.objects.create(name="testdrv", hostname="localhost", login="", password="", type=4)
        self.client.force_login(get_user_model().objects.create_superuser("js", "js@example.com", "pw"))
        with patch("vrtManager.connection.connection_manager.get_connection", return_value=conn), \
             patch("vrtManager.connection.wvmConnect.get_machine_types", return_value=["pc"]), \
             patch("vrtManager.connection.wvmConnect.get_nwfilters", return_value=[]):
            response = self.client.get(reverse("instances:create_instance", args=[compute.id, "i686", "pc"]))
        self.assertEqual(response.status_code, 200)
        return compute, response.content.decode()

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_disk_bus_list_is_asked_for_the_page_architecture_and_machine(self):
        compute, page = self.page()
        function = re.search(r"function get_disk_bus_choices\(.*?\n    }\n", page, re.S).group(0)
        script = "let asked; const $ = Object.assign(() => ({find: () => ({remove() {}}), append() {}, val() {}}), " \
                 "{getJSON: url => { asked = url; }, each() {}});\n" + function + \
                 f"get_disk_bus_choices({compute.id}, 0, 'cdrom'); console.log(asked);"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), reverse("buses", args=[compute.id, "i686", "pc", "cdrom"]))
