from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from computes.models import Compute


class NWFiltersTestCase(TestCase):
    def setUp(self):
        self.compute = Compute.objects.create(
            name="nwfilter-test-compute",
            hostname="127.0.0.1",
            login="root",
            password="",
            type=1,
        )
        self.regular_user = User.objects.create_user(
            username="regular_nwfilter", password="pwd"
        )

    def test_nwfilters_non_superuser_forbidden(self):
        self.client.force_login(self.regular_user)
        response = self.client.get(reverse("nwfilters", args=[self.compute.id]))
        self.assertEqual(response.status_code, 403)

    def test_nwfilter_detail_non_superuser_forbidden(self):
        self.client.force_login(self.regular_user)
        response = self.client.get(
            reverse("nwfilter", args=[self.compute.id, "clean-traffic"])
        )
        self.assertEqual(response.status_code, 403)
