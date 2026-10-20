"""Scripts of the VM create page, rendered on libvirt's test driver and run
in Node against stand-ins."""

import json
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from instances.forms import NewVMForm
from instances.tests.create_page import render_create_page
from lxml import html


# Just enough DOM for the custom form's rows: elements reach their parts by
# selector, a row template clones a fresh row, and writing HTML throws (rows
# are built from text and nodes only). A random-MAC request waits in replies.
FAKE_DOM = """
class El {
  constructor(parts = {}) {
    Object.assign(this, {parts, dataset: {}, value: "", name: "", textContent: "", children: [], listeners: {},
                         classes: new Set(), options: []});
    this.classList = {toggle: (c, on) => on ? this.classes.add(c) : this.classes.delete(c)};
  }
  set innerHTML(v) { throw new Error("HTML written: " + v); }
  part(sel) { return this.parts[sel]; }
  querySelector(sel) { return this.parts[sel]; }
  addEventListener(event, f) { this.listeners[event] = f; }
  append(child) { child.parent = this; this.children.push(child); }
  add(option) { this.options.push(option); }
  remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
}
const row_template = names => ({content: {firstElementChild: {cloneNode: () =>
  new El(Object.fromEntries(names.map(n => [n, new El()])))}}});
const templates = {"disk-row": row_template([".row-label", ".disk-device", ".disk-bus", ".row-item", ".row-remove"]),
                   "network-row": row_template(["input", ".row-label", ".row-item", ".row-remove"])};
const fields = {};
const field = sel => fields[sel] ??= new El();
const rows = list => field(list).children;
const document = {getElementById: id => templates[id], querySelector: field};
const replies = [];
const $ = Object.assign(() => ({ready: f => f()}), {getJSON: (url, f) => { replies.push(f); return {}; }});
const Option = function (text, value) { return {text, value}; };
// TomSelect: records its added options and items, and calls the page's
// callbacks as TomSelect does (a silent addItem still calls onItemAdd)
class TomSelect {
  constructor(sel, settings) { this.settings = settings; this.added = []; }
  addOption(o) { this.added.push("option " + o.value); }
  addItem(v) { this.added.push("item " + v); this.settings.onItemAdd(v); }
  removeItem(v) { this.settings.onItemRemove(v); }
}
"""


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
                 f"get_disk_bus_choices({compute.id}, {{}}, 'cdrom'); console.log(asked);"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), reverse("buses", args=[compute.id, "i686", "pc", "cdrom"]))

    def test_the_custom_form_has_its_own_mac_field(self):
        _, page = self.page()
        custom = html.fromstring(page).xpath("//form[@aria-label='Create custom instance form']")[0]
        self.assertEqual(custom.xpath(".//input[@name='mac']/@id"), ["custom_mac"])

    def test_the_row_templates_hold_what_the_rows_use(self):
        # the test driver reports no disk devices or buses
        with patch("vrtManager.connection.wvmConnect.get_disk_device_types", return_value=["disk", "cdrom"]), \
             patch("vrtManager.connection.wvmConnect.get_disk_bus_types", return_value=["sata", "virtio"]):
            _, page = self.page()
        disk = html.fromstring(page).get_element_by_id("disk-row")
        self.assertEqual(disk.xpath(".//select[contains(@class, 'disk-device')]/option/@value"), ["disk", "cdrom"])
        self.assertEqual(disk.xpath(".//select[contains(@class, 'disk-bus')]/option[@selected]/@value"), ["virtio"])
        network = html.fromstring(page).get_element_by_id("network-row")
        self.assertTrue(network.xpath(".//input[@required]"))
        for row in (disk, network):
            for part in ("row-label", "row-item", "row-remove"):
                self.assertTrue(row.xpath(f".//*[contains(@class, '{part}')]"), part)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_adding_a_disk_keeps_the_others(self):
        out = self.run_rows("""
images_control.addItem("a");
const a = rows("#img-list")[0];
a.part(".disk-device").value = "cdrom"; a.part(".disk-bus").value = "sata";
images_control.addItem("b");
console.log(JSON.stringify([a.part(".disk-device").value, a.part(".disk-bus").value, field("#images").value,
  rows("#img-list").flatMap(r => [r.part(".disk-device").name, r.part(".disk-bus").name])]));""")
        self.assertEqual(out, ["cdrom", "sata", "a,b", ["device0", "bus0", "device1", "bus1"]])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_removing_a_disk_renumbers_the_rest(self):
        out = self.run_rows("""
images_control.addItem("a"); images_control.addItem("b"); images_control.addItem("c");
images_control.removeItem("b");
const c = rows("#img-list")[1];
console.log(JSON.stringify([field("#images").value, c.part(".disk-device").name, c.part(".disk-bus").name,
  c.part(".row-label").textContent]));""")
        self.assertEqual(out, ["a,c", "device1", "bus1", "hdd1"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_adding_a_network_keeps_typed_macs(self):
        out = self.run_rows("""
networks_control.addItem("default");
const first = rows("#net-list")[0];
first.part("input").value = "52:54:00:00:00:01"; first.part("input").listeners.input();
networks_control.addItem("isolated");
replies[0]({mac: "52:54:00:aa:aa:aa"}); replies[1]({mac: "52:54:00:bb:bb:bb"});
console.log(JSON.stringify([field("#networks").value, field("#custom_mac").value]));""")
        self.assertEqual(out, ["default,isolated", "52:54:00:00:00:01,52:54:00:bb:bb:bb"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_a_hostile_volume_name_is_text(self):
        name = "x'\"><img src=x>"
        out = self.run_rows(f"console.log(JSON.stringify(add_disk_row({json.dumps(name)}).part('.row-item').textContent));")
        self.assertEqual(out, name)

    def run_rows(self, test):
        """Run the custom form's row script against a fake DOM, then test;
        returns what test printed, parsed as JSON."""
        _, page = self.page()
        functions = re.search(r"    var images_control, networks_control;.*?\n    function random_mac\(.*?\n    }\n",
                              page, re.S).group(0)
        result = subprocess.run(["node", "-e", FAKE_DOM + functions + test], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

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
const document = {body: {dataset: {submittedForm: %s}}, getElementById: () => null, querySelector: sel => {
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

    LISTS = {"pool": "default",
             "disks": [{"volume": "a.qcow2", "device": "cdrom", "bus": "sata"},
                       {"volume": "b.qcow2", "device": "disk", "bus": "virtio"}],
             "networks": [{"name": "default", "mac": "52:54:00:00:00:01"}, {"name": "isolated", "mac": ""}]}

    def run_restore(self, test):
        """Run restore_custom_lists on LISTS against the fake DOM, with
        loaders that answer when the test calls answer(); returns what the
        test printed, parsed as JSON."""
        _, page = self.page()
        rows = re.search(r"    var images_control, networks_control;.*?\n    function random_mac\(.*?\n    }\n", page, re.S)
        restore = re.search(r"    // The custom form's lists as submitted.*?\n    function select_value\(.*?\n    }\n", page, re.S)
        harness = FAKE_DOM + rows.group(0) + restore.group(0) + """
templates["custom-lists"] = {textContent: %s};
$.when = (...loading) => Promise.all(loading);
const log = [], answers = [];
const later = name => { log.push(name); return new Promise(done => answers.push(() => { log.push("loaded " + name); done(); })); };
const get_cust_vols = (compute, pool) => later("volumes " + pool);
const get_disk_bus_choices = (compute, bus, device) => later("buses " + device);
const settle = () => new Promise(done => setTimeout(done));
const answer = async () => { await settle(); answers.shift()(); await settle(); };
const disks = () => rows("#img-list").map(r => [r.dataset.item, r.part(".disk-device").value, r.part(".disk-bus").value]);
""" % json.dumps(json.dumps(self.LISTS))
        result = subprocess.run(["node", "-e", harness + "(async () => {" + test + "})();"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_restoring_the_custom_form_rebuilds_its_rows(self):
        out = self.run_restore("""
const restored = restore_custom_lists().then(() => log.push("restored"));
await answer();
const before = disks();
await answer(); await answer(); await restored;
const typed_before_reply = field("#custom_mac").value;
replies[0]({mac: "52:54:00:00:00:bb"});
console.log(JSON.stringify({log, before, after: disks(), networks: field("#networks").value,
  typed_before_reply, macs: field("#custom_mac").value, random_requests: replies.length}));""")
        self.assertEqual(out["log"], ["volumes default", "loaded volumes default", "buses cdrom", "buses disk",
                                      "loaded buses cdrom", "loaded buses disk", "restored"])
        self.assertEqual(out["before"], [["a.qcow2", "cdrom", ""], ["b.qcow2", "disk", ""]])
        self.assertEqual(out["after"], [["a.qcow2", "cdrom", "sata"], ["b.qcow2", "disk", "virtio"]])
        self.assertEqual(out["networks"], "default,isolated")
        self.assertEqual(out["typed_before_reply"], "52:54:00:00:00:01,")
        self.assertEqual(out["macs"], "52:54:00:00:00:01,52:54:00:00:00:bb")
        self.assertEqual(out["random_requests"], 1)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_a_value_gone_from_the_lists_is_kept(self):
        out = self.run_restore("""
const restored = restore_custom_lists();
await answer();
const bus = rows("#img-list")[0].part(".disk-bus");
bus.options.push({value: "virtio"});  // the loaded list has no sata
await answer(); await answer(); await restored;
console.log(JSON.stringify({volumes: images_control.added, bus: bus.value,
  options: bus.options.map(o => o.value)}));""")
        self.assertEqual(out["volumes"], ["option a.qcow2", "item a.qcow2", "option b.qcow2", "item b.qcow2"])
        self.assertEqual(out["bus"], "sata")
        self.assertEqual(out["options"], ["virtio", "sata"])
