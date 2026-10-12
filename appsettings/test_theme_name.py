"""The theme name posted on the settings page must be one of the themes in
SASS_DIR/wvc-themes: it is written into @import paths and the compiled CSS."""

import os
import shutil
import tempfile
from pathlib import Path

from appsettings.models import AppSettings
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
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


class ThemeCssTests(TestCase):
    """A theme change also rewrites the collected wvc-main.min.css: nginx
    serves STATIC_ROOT, not static/."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("tc-admin", "tc@example.com", "pw"))
        project = tempfile.TemporaryDirectory()
        self.addCleanup(project.cleanup)
        self.project = Path(project.name)
        shutil.copytree(Path(settings.BASE_DIR, "dev", "scss"), self.project / "dev" / "scss")
        (self.project / "static" / "css").mkdir(parents=True)
        cwd = os.getcwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, cwd)
        AppSettings.objects.filter(key="SASS_DIR").update(value="dev/scss")

    def change_theme(self, static_root):
        with override_settings(BASE_DIR=self.project, STATIC_ROOT=static_root):
            response = self.client.post(reverse("appsettings"), {"BOOTSTRAP_THEME": "flatly"})
        self.assertIn("Theme is changed. Now: flatly", [str(m) for m in get_messages(response.wsgi_request)])
        self.assertEqual(AppSettings.objects.get(key="BOOTSTRAP_THEME").value, "flatly")
        source = (self.project / "static" / "css" / "wvc-main.min.css").read_text()
        self.assertIn(".btn", source)
        return source

    def test_the_collected_css_is_the_new_theme(self):
        served = self.project / "staticfiles" / "css" / "wvc-main.min.css"
        served.parent.mkdir(parents=True)
        served.write_text("old theme")
        self.assertEqual(served.read_text(), "old theme")
        self.assertEqual(self.change_theme(self.project / "staticfiles"), served.read_text())

    def test_without_collected_css_only_the_source_is_written(self):
        (self.project / "staticfiles").mkdir()
        self.change_theme(self.project / "staticfiles")
        self.assertEqual(list((self.project / "staticfiles").iterdir()), [])
        self.change_theme(None)
