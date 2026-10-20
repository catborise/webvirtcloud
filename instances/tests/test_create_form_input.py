"""A refused VM create form comes back bound and open, with what was
entered; the other create forms keep their own defaults."""

import json
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from instances.models import Flavor
from instances.tests.create_page import render_create_page
from lxml import html
from operations import claims, jobs
from operations.models import Operation
from vrtManager import util


def valid(**fields):
    """A create submission NewVMForm accepts."""
    return {"name": "vm1", "firmware": "BIOS", "vcpu": "1", "memory": "512", "networks": "default",
            "cache_mode": "default", "add_cdrom": "None", "add_input": "None", "video": "vga",
            "listener_addr": "0.0.0.0", "submission_key": uuid.uuid4().hex, **fields}


class CreateFormInputTests(TestCase):
    def setUp(self):
        self.flavor = Flavor.objects.create(label="wvc-test", memory=512, vcpu=1, disk=10)

    def test_each_create_form_has_its_own_unbound_form(self):
        _, response = render_create_page(self)
        forms = response.context["vm_forms"]
        self.assertEqual(set(forms), {"custom", "template"} | {f"flavor{f.id}" for f in Flavor.objects.all()})
        self.assertFalse(any(form.is_bound for form in forms.values()))
        self.assertEqual(response.context["submitted_form"], "")

    def test_the_refused_form_comes_back_bound(self):
        _, response = render_create_page(self, valid(form_id="custom", name="bad name!", vcpu="2"))
        self.assertEqual(response.context["submitted_form"], "custom")
        self.assertTrue(response.context["custom_form"].is_bound)
        self.assertFalse(response.context["template_form"].is_bound)
        self.assertIn("name", response.context["custom_form"].errors)

    def test_a_refusal_after_validation_keeps_the_form(self):
        for refusal in (claims.Busy("compute 1"), jobs.SubmissionConflict("already submitted"),
                        util.OperationError("A virtual machine with this name already exists")):
            with self.subTest(refusal=type(refusal).__name__), \
                 patch("instances.views.jobs.submit", side_effect=refusal):
                _, response = render_create_page(self, valid(form_id="template"))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["submitted_form"], "template")
                self.assertTrue(response.context["template_form"].is_bound)
                self.assertTrue(list(response.context["messages"]))
        # the view's own refusal: a new disk needs a MAC
        _, response = render_create_page(self, valid(form_id="template", hdd_size="10", mac=""))
        self.assertEqual(response.context["submitted_form"], "template")
        self.assertTrue(response.context["template_form"].is_bound)

    def test_a_stale_form_id_is_no_form(self):
        _, response = render_create_page(self, valid(form_id="flavor999", name="bad name!"))
        self.assertEqual(response.context["submitted_form"], "")
        # no form shows the error: it goes to the messages
        self.assertTrue(any("special characters" in str(m) for m in response.context["messages"]))

    def test_a_valid_post_with_a_stale_form_id_still_queues_the_job(self):
        compute, response = render_create_page(self, valid(
            form_id="flavor999", hdd_size="10", storage="default-pool", mac="52:54:00:00:00:01"))
        self.assertRedirects(response, reverse("instances", args=[compute.id]), fetch_redirect_response=False)
        self.assertEqual(Operation.objects.get().kind, "instance.create")


class CreateFormValuesTests(TestCase):
    """The custom and template forms render their own form's values and errors."""

    def setUp(self):
        Flavor.objects.create(label="wvc-test", memory=512, vcpu=1, disk=10)

    def page(self, **fields):
        _, response = render_create_page(self, valid(**fields))
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content)

    def summary(self, page, form_label):
        return " ".join(page.xpath(f"//form[@aria-label='{form_label}']//*[contains(@class, 'form-errors')]//text()"))

    def test_the_custom_form_keeps_what_was_submitted(self):
        page = self.page(form_id="custom", name="bad name!", vcpu="3", memory="2048", cache_mode="writeback")
        self.assertEqual(page.get_element_by_id("custom_name").get("value"), "bad name!")
        self.assertEqual(page.get_element_by_id("custom_vcpu").get("value"), "3")
        self.assertEqual(page.get_element_by_id("custom_memory").get("value"), "2048")
        self.assertEqual(page.xpath("//select[@id='cache_mode']/option[@selected]/@value"), ["writeback"])
        self.assertIsNone(page.get_element_by_id("custom_qemu_ga").get("checked"))  # not submitted: unchecked
        # the template form keeps its defaults
        self.assertEqual(page.get_element_by_id("template_vcpu").get("value"), "1")
        self.assertIsNotNone(page.get_element_by_id("template_qemu_ga").get("checked"))

    def test_a_blank_number_stays_blank(self):
        page = self.page(form_id="custom", vcpu="")
        self.assertEqual(page.get_element_by_id("custom_vcpu").get("value"), "")
        self.assertTrue(page.get_element_by_id("custom_vcpu_error").text_content().strip())

    def test_the_name_is_escaped(self):
        page = self.page(form_id="custom", name='<b>x</b>"')
        self.assertEqual(page.get_element_by_id("custom_name").get("value"), '<b>x</b>"')
        self.assertEqual(page.xpath("//form[@aria-label='Create custom instance form']//b"), [])

    def test_the_error_sits_beside_its_field(self):
        page = self.page(form_id="custom", name="bad name!")
        field = page.get_element_by_id("custom_name")
        self.assertIn("is-invalid", field.get("class").split())
        self.assertEqual((field.get("aria-invalid"), field.get("aria-describedby")), ("true", "custom_name_error"))
        self.assertIn("special characters", page.get_element_by_id("custom_name_error").text_content())
        # and in the summary at the top of its form
        self.assertIn("Name: The name of the virtual machine", self.summary(page, "Create custom instance form"))

    def test_the_template_form_keeps_its_pool_template_and_storage(self):
        page = self.page(form_id="template", name="bad name!", template_pool="default-pool",
                         template="base.img", storage="default-pool")
        form = page.xpath("//form[@aria-label='Create instance from template form']")[0]
        self.assertEqual((form.get("data-template-pool"), form.get("data-template"), form.get("data-storage")),
                         ("default-pool", "base.img", "default-pool"))
        self.assertEqual(page.get_element_by_id("template_disk").get("name"), "template_pool")

    def test_an_error_of_a_field_the_form_hides_is_in_its_summary(self):
        # the custom form's networks are a hidden input behind a picker
        page = self.page(form_id="custom", networks="")
        self.assertIn("Networks: This field is required.", self.summary(page, "Create custom instance form"))

    def test_each_form_sends_its_own_form_id(self):
        page = self.page(form_id="custom", name="bad name!")
        for label, form_id in (("Create custom instance form", "custom"), ("Create instance from template form", "template")):
            with self.subTest(form=form_id):
                form = page.xpath(f"//form[@aria-label='{label}']")[0]
                self.assertEqual(form.xpath(".//input[@name='form_id']/@value"), [form_id])

    def test_a_firmware_error_without_a_firmware_choice_is_in_the_summary(self):
        # an architecture with no firmware label renders no firmware select
        with patch("vrtManager.connection.wvmConnect.label_for_firmware_path", return_value=None):
            fields = valid(form_id="custom")
            del fields["firmware"]
            _, response = render_create_page(self, fields)
        page = html.fromstring(response.content)
        self.assertEqual(page.xpath("//select[@name='firmware']"), [])
        self.assertIn("Firmware: This field is required.", self.summary(page, "Create custom instance form"))


class FlavorFormTests(TestCase):
    """Each flavor's modal form renders its own form; the refused one comes back."""

    def setUp(self):
        self.first = Flavor.objects.create(label="wvc-test-1", memory=512, vcpu=1, disk=10)
        self.second = Flavor.objects.create(label="wvc-test-2", memory=1024, vcpu=2, disk=20)

    def page(self, **fields):
        _, response = render_create_page(self, valid(**fields))
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content)

    def test_only_the_refused_flavor_comes_back(self):
        page = self.page(form_id=f"flavor{self.second.id}", name="bad name!", vcpu="2", memory="1024", hdd_size="20")
        self.assertEqual(page.get_element_by_id(f"flavor{self.second.id}_name").get("value"), "bad name!")
        self.assertEqual(page.get_element_by_id(f"flavor{self.first.id}_name").get("value"), "")
        self.assertTrue(page.get_element_by_id(f"flavor{self.first.id}_mac").get("value"))

    def test_a_flavor_field_points_at_its_error(self):
        page = self.page(form_id=f"flavor{self.second.id}", name="bad name!", vcpu="2", memory="1024", hdd_size="20")
        field = page.get_element_by_id(f"flavor{self.second.id}_name")
        error = page.get_element_by_id(field.get("aria-describedby"))
        self.assertIn("special characters", error.text_content())

    def test_a_reopened_flavor_holds_the_message(self):
        with patch("instances.views.jobs.submit",
                   side_effect=util.OperationError("A virtual machine with this name already exists")):
            page = self.page(form_id=f"flavor{self.second.id}", vcpu="2", memory="1024", hdd_size="20",
                             storage="default-pool", mac="52:54:00:00:00:01")
        messages = page.xpath(f"//*[@id='addVMflavor{self.second.id}']//*[contains(@class, 'modal-messages')]")
        self.assertIn("A virtual machine with this name already exists", messages[0].text_content())

    def test_the_flavor_form_sends_one_cache_mode(self):
        page = self.page(form_id="custom", name="bad name!")
        form = page.get_element_by_id(f"addVMflavor{self.first.id}").xpath(".//form")[0]
        self.assertEqual(len(form.xpath(".//*[@name='cache_mode']")), 1)
        self.assertEqual(form.xpath(".//input[@name='form_id']/@value"), [f"flavor{self.first.id}"])


class CustomListsTests(TestCase):
    """The custom form's disks and networks come back with a refusal."""

    def custom_lists(self, response):
        """The custom form's disks and networks the page restores, or None."""
        element = html.fromstring(response.content).get_element_by_id("custom-lists", None)
        return None if element is None else json.loads(element.text)

    def test_a_refused_custom_form_sends_back_its_lists(self):
        _, response = render_create_page(self, valid(
            form_id="custom", name="bad name!", **{"storage-control": "default"}, images="a.qcow2,b.qcow2",
            device0="cdrom", bus0="sata", device1="disk", bus1="virtio",
            networks="default,isolated", mac="52:54:00:00:00:01"))
        self.assertEqual(self.custom_lists(response), {
            "pool": "default",
            "disks": [{"volume": "a.qcow2", "device": "cdrom", "bus": "sata"},
                      {"volume": "b.qcow2", "device": "disk", "bus": "virtio"}],
            "networks": [{"name": "default", "mac": "52:54:00:00:00:01"}, {"name": "isolated", "mac": ""}],
        })

    def test_a_hostile_volume_name_round_trips_through_the_lists(self):
        name = "x'\"><img src=x>"
        _, response = render_create_page(self, valid(form_id="custom", name="bad name!", images=name))
        self.assertEqual(self.custom_lists(response)["disks"][0]["volume"], name)
        self.assertNotIn(b"<img src=x>", response.content)

    def test_only_a_refused_custom_form_sends_lists(self):
        _, response = render_create_page(self)
        self.assertIsNone(self.custom_lists(response))
        _, response = render_create_page(self, valid(form_id="template", name="bad name!", images="a.qcow2"))
        self.assertIsNone(self.custom_lists(response))
