"""Scripts of the VM create page, rendered on libvirt's test driver and run
in Node against stand-ins."""

import json
import re
import shutil
import subprocess
import unittest

from django.test import TestCase
from django.urls import reverse
from instances.forms import NewVMForm
from instances.tests.create_page import render_create_page
from lxml import html


class CreatePageScriptTests(TestCase):
    def page(self):
        compute, response = render_create_page(self)
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

    def test_the_selects_without_a_label_of_their_own_are_named(self):
        # TomSelect points the label of a select it enhances at its own
        # input; the template select sits beside the template pool's label
        _, page = self.page()
        page = html.fromstring(page)
        for select in ("image-control", "network-control", "template"):
            with self.subTest(select=select):
                self.assertTrue(page.get_element_by_id(select).get("aria-label"))

    def test_a_mac_per_nic_fits_the_form(self):
        macs = ",".join(f"52:54:00:00:00:{n:02x}" for n in range(8))
        self.assertEqual(NewVMForm.base_fields["mac"].clean(macs), macs)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_page_reopens_the_submitted_form(self):
        _, page = self.page()
        functions = re.search(r"function reopen_submitted_form\(.*?\n    \$\(reopen_submitted_form\);", page, re.S).group(0)
        harness = """
const shown = [];
// the forms' holders: two tab panes and a flavor's dialog, each with an
// "Advanced" section that holds a refused field; a dialog's "shown"
// listeners run when it is shown, as in Bootstrap
const holder = (id, kind) => ({id, listeners: [], classList: {contains: c => c === kind},
  querySelectorAll: () => [{name: "#" + id + " .collapse", querySelector: () => ({})}],
  addEventListener(event, f) { this.listeners.push(f); }});
const holders = {custom: holder("addCustom", "tab-pane"), template: holder("add-from-temp", "tab-pane"),
                 flavor7: holder("addVMflavor7", "modal")};
const document = {body: {dataset: {submittedForm: %s}}, querySelector: sel => {
  const id = (sel.match(/data-form-id="([^"]*)"/) || [])[1];
  if (id !== undefined) return holders[id] || null;
  return sel.startsWith("form[") ? null : {name: sel};  // no refused template form here
}};
const bootstrap = {Tab: {getOrCreateInstance: e => ({show: () => shown.push("tab " + e.name)})},
  Modal: {getOrCreateInstance: e => ({show: () => { shown.push("modal #" + e.id); e.listeners.forEach(f => f()); }})},
  Collapse: {getOrCreateInstance: e => ({show: () => shown.push("collapse " + e.name)})}};
const $ = Object.assign(() => ({val() {}}), {when: () => ({then: f => f()})});
const focusFirstError = () => shown.push("focus");
%s
reopen_submitted_form(); console.log(JSON.stringify(shown));
"""
        for submitted, expected in (
            ("custom", ["collapse #addCustom .collapse", 'tab [data-bs-target="#addCustom"]', "focus"]),
            ("template", ["collapse #add-from-temp .collapse", 'tab [data-bs-target="#add-from-temp"]', "focus"]),
            ("flavor7", ["collapse #addVMflavor7 .collapse", "modal #addVMflavor7", "focus"]),
            ("", ["focus"]),
        ):
            with self.subTest(submitted=submitted):
                result = subprocess.run(["node", "-e", harness % (json.dumps(submitted), functions.replace("$(reopen_submitted_form);", ""))],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)
