"""OTP must be the only interactive login door. The browsable-API login
(/api-auth/login/) is removed and HTTP Basic auth is off, so a session comes
only from the OTP-aware login form and the API cannot be reached with a
password alone, which would skip OTP."""

import base64

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse
from rest_framework.settings import api_settings


class BasicAuthDisabledTestCase(TestCase):
    def test_session_is_the_only_default_authentication(self):
        # exactly SessionAuthentication: not Basic (a password-only door) and
        # not Token/JWT either, which would be another password-free door
        names = [c.__name__ for c in api_settings.DEFAULT_AUTHENTICATION_CLASSES]
        self.assertEqual(names, ["SessionAuthentication"])

    def test_api_rejects_http_basic_credentials(self):
        User = get_user_model()
        User.objects.create_superuser("api-basic", "api-basic@example.com", "pw-123")
        creds = base64.b64encode(b"api-basic:pw-123").decode()
        response = Client().get("/api/v1/instances/", HTTP_AUTHORIZATION=f"Basic {creds}")
        self.assertIn(response.status_code, (401, 403))

    def test_api_works_with_a_session(self):
        User = get_user_model()
        user = User.objects.create_superuser("api-session", "api-session@example.com", "pw-123")
        client = Client()
        client.force_login(user)
        self.assertEqual(client.get("/api/v1/instances/").status_code, 200)


class BrowsableApiLoginRemovedTestCase(TestCase):
    def test_the_browsable_api_login_route_is_gone(self):
        with self.assertRaises(NoReverseMatch):
            reverse("rest_framework:login")
        self.assertEqual(Client().get("/api-auth/login/").status_code, 404)
