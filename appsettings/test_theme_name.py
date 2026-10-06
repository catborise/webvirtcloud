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
