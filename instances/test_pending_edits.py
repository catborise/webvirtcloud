"""On an active VM an edit changes the persistent definition and is pending
until the next start. The edit forms show that definition, so a save does
not post the running VM's value back over a pending change."""

from unittest.mock import PropertyMock, patch

from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.models import Instance
from lxml import html


def disk(dev, cache, serial):
    return {
        "dev": dev, "bus": "virtio", "image": f"{dev}.qcow2", "storage": "default", "path": f"/pool/{dev}.qcow2",
        "format": "qcow2", "backing_file": None, "size": 1 << 30, "used": 1 << 20, "cache": cache, "io": "default",
        "discard": "default", "detect_zeroes": "default", "readonly": False, "shareable": False, "serial": serial,
    }


class PendingEditsTestCase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("pe-admin", "pe@example.com", "x"))
        compute = Compute.objects.create(name="pe", hostname="127.0.0.1:1", login="root", password="", type=1)
        self.instance = Instance.objects.create(compute=compute, name="pe-vm", uuid="aaaaaaaa-1111-2222-3333-444444444444")

    def page(self, live=(), config=(), qos=({}, {})):  # disks and QoS of the live and the persistent definition
        with patch("instances.models.wvmInstance") as wvm, patch.object(
            Compute, "status", new_callable=PropertyMock, return_value=True
        ):
            proxy = wvm.return_value
            proxy.get_status.return_value = 1
            proxy.get_memory.return_value = proxy.get_cur_memory.return_value = 1024
            proxy.get_max_memory.return_value = 4096
            proxy.get_vcpu.return_value = proxy.get_cur_vcpu.return_value = 1
            proxy.get_max_cpus.return_value = [1, 2]
            proxy.get_vcpus.return_value = {}
            for getter in ("get_networks", "get_ifaces", "get_storages", "get_media_devices", "get_net_devices",
                           "get_snapshot", "get_external_snapshots"):
                getattr(proxy, getter).return_value = []
            proxy.get_disk_devices.side_effect = lambda **kw: config if kw.get("config") else live
            proxy.get_all_qos.side_effect = lambda **kw: qos[1] if kw.get("config") else qos[0]
            proxy.get_console_type.return_value = "vnc"
            proxy.get_cache_modes.return_value = {"default": "Default", "none": "Disabled"}
            response = self.client.get(reverse("instances:instance", args=[self.instance.id]))
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content)

    def test_the_disk_edit_form_shows_the_pending_definition(self):
        # running: cache=none and a new disk vdb pending, vda's serial live only
        doc = self.page(
            live=[disk("vda", "default", "live-serial")],
            config=[disk("vda", "none", ""), disk("vdb", "default", "")],
        )
        forms = doc.xpath("//form[@action=$a]", a=reverse("instances:edit_volume", args=[self.instance.id]))
        self.assertEqual([f.xpath("string(.//input[@name='dev']/@value)") for f in forms], ["vda", "vdb"])
        vda = forms[0]
        self.assertEqual(vda.xpath(".//select[@name='vol_cache']/option[@selected]/@value"), ["none"])
        self.assertEqual(vda.xpath("string(.//input[@name='vol_serial']/@value)"), "")

    def test_the_qos_form_shows_the_pending_definition(self):
        mac = "52:54:10:00:00:01"
        inbound = {"direction": "inbound", "average": "500", "peak": "0", "burst": "0", "floor": None}
        doc = self.page(qos=({mac: [inbound]}, {mac: [{**inbound, "average": "1000"}, {**inbound, "direction": "outbound"}]}))
        self.assertEqual(doc.xpath("//input[@name='qos_average']/@value"), ["1000", "500"])
        # the pending outbound has a row
        self.assertEqual([" ".join(t.split()) for t in doc.xpath("//label[@class='col-form-label']/text()")],
                         [f"{mac} Inbound", f"{mac} Outbound"])

    def test_a_disk_with_a_pending_source_is_not_deleted(self):
        # running: vda is A, its definition for the next start uses B
        with patch("instances.models.wvmInstance") as wvm, patch("instances.views.wvmStorage") as storage:
            proxy = wvm.return_value
            live, pending = disk("vda", "default", ""), {**disk("vda", "default", ""), "path": "/pool/b.qcow2"}
            proxy.get_disk_devices.side_effect = lambda **kw: [pending] if kw.get("config") else [live]
            self.client.post(reverse("instances:delete_vol", args=[self.instance.id]), {"dev": "vda"})
        proxy.detach_disk.assert_not_called()
        storage.return_value.del_volume.assert_not_called()
