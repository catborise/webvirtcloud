"""Scripts of the VM create page, rendered on libvirt's test driver and run
in Node against stand-ins."""

import json
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
from instances.forms import NewVMForm
from lxml import html


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

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_custom_form_sends_its_own_macs_and_leaves_the_others_alone(self):
        compute, page = self.page()
        custom = html.fromstring(page).xpath("//form[@aria-label='Create custom instance form']")[0]
        self.assertEqual(custom.xpath(".//input[@name='mac']/@id"), ["custom_mac"])
        function = re.search(r"function populate_mac\(.*?\n    }\n", page, re.S).group(0)
        # the stand-in $ records each write; a MAC field holds what its selector names
        script = "const writes = []; let mac_array = [];\n" \
                 "const $ = sel => ({val: v => v === undefined ? sel.replace(/\\D/g, '') : writes.push([sel, v])});\n" + \
                 function + "populate_mac(0); populate_mac(1); console.log(JSON.stringify(writes));"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [["#custom_mac", "0"], ["#custom_mac", "0,1"]])

    def test_a_mac_per_nic_fits_the_form(self):
        macs = ",".join(f"52:54:00:00:00:{n:02x}" for n in range(8))
        self.assertEqual(NewVMForm.base_fields["mac"].clean(macs), macs)
