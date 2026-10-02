"""ROADMAP O-17: the generated admin password must be replaced at first login."""
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

GENERATED = "Generated-Pass-123"


class FirstLoginPasswordChangeTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.password_file = Path(self.tmp.name) / "data" / "admin_password"
        self.password_file.parent.mkdir()
        self.password_file.write_text(GENERATED + "\n")
        settings_override = override_settings(BASE_DIR=Path(self.tmp.name))
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.admin = get_user_model().objects.create_superuser(
            username="firstadmin", email="firstadmin@example.com", password=GENERATED
        )

    def test_login_with_generated_password_redirects_every_page_to_change_form(self):
        self.client.login(username="firstadmin", password=GENERATED)
        for url in (reverse("index"), reverse("instances:index"), reverse("accounts:profile")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(
                    response, reverse("accounts:change_password"), fetch_redirect_response=False
                )
        self.assertEqual(self.client.get(reverse("accounts:change_password")).status_code, 200)

    def test_changing_the_password_lifts_the_redirect_and_deletes_the_file(self):
        self.client.login(username="firstadmin", password=GENERATED)
        self.client.post(
            reverse("accounts:change_password"),
            {
                "old_password": GENERATED,
                "new_password1": "A-new-strong-pass-456",
                "new_password2": "A-new-strong-pass-456",
            },
        )
        self.assertFalse(self.password_file.exists())
        response = self.client.get(reverse("instances:index"))
        self.assertEqual(response.status_code, 200)

    def test_user_with_another_password_is_not_forced(self):
        get_user_model().objects.create_superuser(
            username="otheradmin", email="otheradmin@example.com", password="Other-pass-789"
        )
        self.client.login(username="otheradmin", password="Other-pass-789")
        self.assertEqual(self.client.get(reverse("instances:index")).status_code, 200)
        self.assertTrue(self.password_file.exists())
