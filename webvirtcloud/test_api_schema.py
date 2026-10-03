import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient


class APISchemaTestCase(TestCase):
    def test_schema_is_valid_without_warnings(self):
        call_command("spectacular", "--validate", "--fail-on-warn", "--file", os.devnull)

    def test_non_numeric_compute_id_is_not_routed(self):
        admin = get_user_model().objects.create_superuser("schema_admin", "a@example.com", "pw")
        client = APIClient()
        client.force_login(admin)
        self.assertEqual(client.get("/api/v1/computes/abc/instances/").status_code, 404)
        self.assertEqual(client.get("/api/v1/instances/abc/").status_code, 404)
