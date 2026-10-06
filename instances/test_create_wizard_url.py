"""The create wizard's first page sends the browser to the next page for the
chosen architecture and machine type; the URL is built from placeholders."""

import re
import subprocess
import shutil
import unittest

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase


class CreateWizardUrlTests(TestCase):
    def render_script(self):
        compute = Compute.objects.create(name="cw", hostname="10.0.0.6", login="u", password="p", type=1)
        request = RequestFactory().get("/")
        request.user = get_user_model().objects.create_superuser("cw-admin", "cw@example.com", "pw")
        page = render_to_string("create_instance_w1.html", {"compute": compute}, request=request)
        # the statement that builds the URL from arch and machine
        return re.search(r"\burl = (.*?);", page, re.S).group(1)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_arch_and_machine_names_land_in_their_segments(self):
        script = self.render_script()
        cases = {
            ("x86_64", "q35"): "x86_64/q35/",
            ("ppc64le", "pseries"): "ppc64le/pseries/",  # contains "pc"
            ("aarch64", "virt"): "aarch64/virt/",
        }
        for (arch, machine), tail in cases.items():
            js = "const arch = %r, machine = %r; console.log(%s);" % (arch, machine, script)
            out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
            with self.subTest(arch=arch):
                self.assertTrue(out.stdout.strip().endswith(f"/{tail}"), out.stdout + out.stderr)
