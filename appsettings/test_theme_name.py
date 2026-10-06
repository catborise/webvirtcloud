"""The theme name posted on the settings page must be one of the themes in
SASS_DIR/wvc-themes: it is written into @import paths and the compiled CSS."""

import os
import tempfile

from appsettings.models import AppSettings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class ThemeNameTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("th-admin", "th@example.com", "pw"))
        self.sass = tempfile.TemporaryDirectory()
        self.addCleanup(self.sass.cleanup)
        os.makedirs(os.path.join(self.sass.name, "wvc-themes", "flatly"))
        AppSettings.objects.filter(key="SASS_DIR").update(value=self.sass.name)
        self.before = AppSettings.objects.get(key="BOOTSTRAP_THEME").value

    def test_a_name_that_is_not_a_theme_is_refused(self):
        for theme in ("../../etc", "flatly';@import '/etc/passwd", "nope", ""):
            with self.subTest(theme=theme):
                self.client.post(reverse("appsettings"), {"BOOTSTRAP_THEME": theme})
                self.assertFalse(os.path.exists(os.path.join(self.sass.name, "wvc-main.scss")))
                self.assertEqual(AppSettings.objects.get(key="BOOTSTRAP_THEME").value, self.before)


class SassDirTests(TestCase):
    """SASS_DIR must be a directory inside the project: the page lists it and
    writes wvc-main.scss into it."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("sd-admin", "sd@example.com", "pw"))
        self.before = AppSettings.objects.get(key="SASS_DIR").value

    def test_a_directory_outside_the_project_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        for value in (outside.name, "/etc", "../", "dev/scss/../../..", "no/such/dir"):
            with self.subTest(value=value):
                self.client.post(reverse("appsettings"), {"SASS_DIR": value})
                self.assertEqual(AppSettings.objects.get(key="SASS_DIR").value, self.before)

    def test_the_project_scss_directory_is_accepted(self):
        self.client.post(reverse("appsettings"), {"SASS_DIR": "dev/scss"})
        self.assertEqual(AppSettings.objects.get(key="SASS_DIR").value, "dev/scss")

    def test_a_stored_directory_outside_the_project_is_not_used(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        os.makedirs(os.path.join(outside.name, "wvc-themes", "flatly"))
        AppSettings.objects.filter(key="SASS_DIR").update(value=outside.name)
        self.client.post(reverse("appsettings"), {"BOOTSTRAP_THEME": "flatly"})
        self.assertFalse(os.path.exists(os.path.join(outside.name, "wvc-main.scss")))
