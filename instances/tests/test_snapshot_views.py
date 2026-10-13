"""The snapshot views call the backend once and show its refusals as
messages; they no longer start, pause or stop the VM themselves."""

from datetime import datetime
from unittest.mock import PropertyMock, patch

import libvirt
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from lxml import html as lxml_html
from vrtManager import util

from computes.models import Compute
from instances.models import Instance


class SnapshotViewsTestCase(TestCase):
    def setUp(self):
        compute = Compute.objects.create(name="snap-compute", hostname="127.0.0.1:1", login="root", password="", type=1)
        self.instance = Instance.objects.create(
            compute=compute, name="snap-vm", uuid="dddddddd-eeee-ffff-0000-222222222222"
        )
        admin = get_user_model().objects.create_superuser("snap_admin", "s@example.com", "pw")
        self.client.force_login(admin)

    def post(self, view, data, error=None, left=()):
        with patch("instances.models.wvmInstance") as mock_wvm:
            proxy = mock_wvm.return_value
            method = {
                "create_external_snapshot": proxy.create_external_snapshot,
                "revert_external_snapshot": proxy.revert_external_snapshot,
                "delete_external_snapshot": proxy.delete_external_snapshot,
                "revert_snapshot": proxy.snapshot_revert,
                "delete_snapshot": proxy.snapshot_delete,
                "snapshot": proxy.create_snapshot,
            }[view]
            method.return_value = list(left)  # files revert and delete left behind
            if error:
                method.side_effect = util.OperationError(error)
            self.client.cookies.pop("messages", None)  # only this request's messages
            response = self.client.post(
                reverse(f"instances:{view}", args=[self.instance.id]),
                data,
                HTTP_REFERER=reverse("instances:instance", args=[self.instance.id]),
            )
        self.assertEqual(response.status_code, 302)
        return proxy, method, [str(m) for m in get_messages(response.wsgi_request)]

    def test_create_passes_name_description_and_memory(self):
        _, method, _ = self.post("create_external_snapshot", {"name": "a", "description": "d", "memory": "on"})
        method.assert_called_once_with("a", "d", True)
        _, method, _ = self.post("create_external_snapshot", {"name": "a", "description": ""})
        method.assert_called_once_with("a", "", False)

    def test_revert_and_delete_call_the_backend_once_and_leave_power_alone(self):
        for view in ("revert_external_snapshot", "delete_external_snapshot"):
            with self.subTest(view):
                proxy, method, _ = self.post(view, {"name": "a"})
                method.assert_called_once_with("a")
                for power in ("start", "force_shutdown", "shutdown"):
                    getattr(proxy, power).assert_not_called()

    def test_files_left_behind_are_named_in_a_warning(self):
        for view in ("revert_external_snapshot", "delete_external_snapshot"):
            with self.subTest(view):
                _, _, messages = self.post(view, {"name": "a"}, left=["/pool/a.one", "/pool/vm.one.mem"])
                warning = [m for m in messages if "/pool/a.one, /pool/vm.one.mem" in m]
                self.assertEqual(len(warning), 1, messages)
                self.assertIn("qemu user", warning[0])
                _, _, messages = self.post(view, {"name": "a"})
                self.assertFalse([m for m in messages if "not removed" in m], messages)

    def test_refusals_become_messages(self):
        for view in ("create_external_snapshot", "revert_external_snapshot", "delete_external_snapshot",
                     "revert_snapshot", "delete_snapshot", "snapshot"):
            with self.subTest(view):
                _, _, messages = self.post(view, {"name": "a"}, error="refused for a reason")
                self.assertTrue(any("refused for a reason" in m for m in messages), messages)


class SnapshotTabTestCase(TestCase):
    setUp = SnapshotViewsTestCase.setUp  # same VM and superuser, without rerunning the view tests

    def page(self, external=(), internal=(), jobs=(), status=1, nvram=None):
        with patch("instances.models.wvmInstance") as mock_wvm, patch.object(
            Compute, "status", new_callable=PropertyMock, return_value=True
        ):
            proxy = mock_wvm.return_value
            for getter in ("get_networks", "get_ifaces", "get_storages", "get_disk_devices",
                           "get_media_devices", "get_net_devices"):
                getattr(proxy, getter).return_value = []
            proxy.get_snapshot.side_effect = lambda flag: {
                libvirt.VIR_DOMAIN_SNAPSHOT_LIST_INTERNAL: list(internal),
                libvirt.VIR_DOMAIN_SNAPSHOT_LIST_EXTERNAL: list(external),
            }[flag]
            if isinstance(jobs, Exception):
                proxy.get_snapshot_jobs.side_effect = jobs
            else:
                proxy.get_snapshot_jobs.return_value = list(jobs)
            proxy.get_status.return_value = status
            proxy.get_nvram.return_value = nvram
            proxy.get_memory.return_value = proxy.get_cur_memory.return_value = 1024
            proxy.get_max_memory.return_value = 4096
            proxy.get_vcpu.return_value = proxy.get_cur_vcpu.return_value = 1
            proxy.get_max_cpus.return_value = [1]
            proxy.get_vcpus.return_value = {}
            proxy.get_console_type.return_value = "vnc"
            response = self.client.get(reverse("instances:instance", args=[self.instance.id]))
        self.assertEqual(response.status_code, 200)
        self.proxy = proxy
        return lxml_html.fromstring(response.content)

    def snap(self, name, **kw):
        return {"name": name, "date": datetime(2026, 1, 1), "description": "", "memory": False,
                "current": False, "detaches": [], **kw}

    def test_revert_confirm_names_detached_disks_and_nvram(self):
        doc = self.page(external=[self.snap("e", detaches=[{"dev": "vdb", "path": "/pool/later.qcow2"}])],
                        nvram="/var/lib/libvirt/qemu/nvram/x_VARS.fd")
        [button] = doc.xpath("//button[@name='revert_external_snapshot']")
        text = button.get("data-confirm")
        self.assertIn("vdb (/pool/later.qcow2)", text)
        self.assertIn("NVRAM", text)

    def test_each_kind_is_listed_once_per_page(self):
        doc = self.page(internal=[self.snap("i")], external=[self.snap("e")])
        flags = sorted(c.args[0] for c in self.proxy.get_snapshot.call_args_list)
        self.assertEqual(
            flags, sorted([libvirt.VIR_DOMAIN_SNAPSHOT_LIST_INTERNAL, libvirt.VIR_DOMAIN_SNAPSHOT_LIST_EXTERNAL])
        )
        self.assertEqual(doc.xpath("//i[contains(@title, 'snapshot(s)')]/@title"), ["There are 2 snapshot(s)"])

    def test_a_failed_job_query_still_renders_the_tab(self):
        # the actions themselves check for jobs again and refuse if that fails
        doc = self.page(external=[self.snap("e")], jobs=libvirt.libvirtError("no job info"))
        self.assertEqual(len(doc.xpath("//button[@name='revert_external_snapshot']")), 1)

    def test_revert_confirm_says_a_disk_only_revert_restarts_the_guest(self):
        for status, memory, restarts in ((1, False, True), (3, False, True), (5, False, False), (1, True, False)):
            with self.subTest(status=status, memory=memory):
                doc = self.page(external=[self.snap("e", memory=memory)], status=status)
                [button] = doc.xpath("//button[@name='revert_external_snapshot']")
                self.assertEqual("powered off" in button.get("data-confirm"), restarts)

    def test_memory_checkbox_only_for_a_running_vm(self):
        self.assertFalse(self.page(status=1).xpath("//input[@name='memory']")[0].get("disabled"))
        self.assertIsNotNone(self.page(status=5).xpath("//input[@name='memory']")[0].get("disabled"))

    def test_a_running_job_shows_progress_and_disables_actions(self):
        doc = self.page(external=[self.snap("e")], jobs=[{"dev": "vda", "cur": 1, "end": 4}])
        self.assertIn("vda 25%", doc.text_content())
        self.assertTrue(all(b.get("disabled") is not None
                            for b in doc.xpath("//div[@id='snapshots']//button[@type='submit']")))

    def test_lists_are_separate(self):
        doc = self.page(internal=[self.snap("i")], external=[self.snap("e")])
        self.assertEqual(doc.xpath("//table[@id='internal-snapshots']//strong/text()"), ["i"])
        self.assertEqual(doc.xpath("//table[@id='external-snapshots']//strong/text()"), ["e"])
