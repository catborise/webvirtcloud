"""Tests for the instances list after out-of-band libvirt changes."""

from unittest.mock import PropertyMock, patch

from appsettings.models import AppSettings
from computes.models import Compute
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from instances.models import Instance
from lxml import html


class InstancesIndexTests(TestCase):
    def test_removed_domain_is_not_rendered_from_prefetch_cache(self):
        user = get_user_model().objects.create_superuser(
            username="instances-index-admin", password="password", email="index@example.com"
        )
        self.client.force_login(user)
        compute = Compute.objects.create(
            name="index-compute", hostname="localhost", type=4,
            login="", password="",
        )
        instance = Instance.objects.create(
            compute=compute, name="test4", uuid="cb2a2697-0945-408a-ac6b-c3e07a280851"
        )
        self.assertEqual(Compute.objects.count(), 1)

        def synchronize(_compute):
            Instance.objects.filter(pk=instance.pk).delete()
            self.assertFalse(Instance.objects.filter(pk=instance.pk).exists())

        with patch("instances.views.utils.refr", side_effect=synchronize), patch.object(
            Compute, "status", new_callable=PropertyMock, return_value=True
        ), patch.object(
            Compute, "cpu_count", new_callable=PropertyMock, return_value=1
        ), patch.object(
            Compute, "ram_size", new_callable=PropertyMock, return_value=1024
        ), patch.object(
            Compute, "ram_usage", new_callable=PropertyMock, return_value=0
        ):
            response = self.client.get(reverse("instances:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "test4")
        self.assertEqual(list(response.context["computes"][0].instance_set.all()), [])

    def test_the_grouped_list_without_computes_has_a_table_body(self):
        # sortable.js reads the first tbody's rows: without one it throws and leaves the table unsortable
        AppSettings.objects.filter(key="VIEW_INSTANCES_LIST_STYLE").update(value="grouped")
        self.client.force_login(get_user_model().objects.create_superuser("grouped-admin", "g@example.com", "pw"))
        page = html.fromstring(self.client.get(reverse("instances:index")).content)
        self.assertEqual(len(page.xpath("//table[@data-sortable]/tbody")), 1)
