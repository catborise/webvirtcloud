"""Settings read from the environment. Each case imports the settings in a
subprocess, so the environment of one case cannot leak into another."""

import json
import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

# Env vars that change the settings; removed from the inherited environment so
# every case starts from the defaults.
CONFIG_VARS = ["WEBVIRTCLOUD_HTTPS", "ALLOWED_HOSTS"]


def _env(env):
    base = {k: v for k, v in os.environ.items() if k not in CONFIG_VARS}
    return {**base, "DJANGO_SETTINGS_MODULE": "webvirtcloud.settings", **env}


def load_settings(env, names):
    code = (
        "import json, django; django.setup(); from django.conf import settings as s; "
        f"print(json.dumps({{n: getattr(s, n) for n in {names!r}}}, default=str))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=_env(env), cwd=settings.BASE_DIR,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def run_manage(env, *args):
    result = subprocess.run(
        [sys.executable, "manage.py", *args], env=_env(env), cwd=settings.BASE_DIR,
        capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise AssertionError(output)
    return output


# TestCase: the request cases may touch the database (sessions, axes).
class HttpsProfileTestCase(TestCase):
    def test_https_profile_passes_deploy_checks(self):
        output = run_manage({"WEBVIRTCLOUD_HTTPS": "1"}, "check", "--deploy")
        for check_id in ("security.W004", "security.W008", "security.W012", "security.W016"):
            self.assertNotIn(check_id, output)

    def test_plain_http_is_the_default(self):
        names = ["SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "SECURE_SSL_REDIRECT", "SECURE_HSTS_SECONDS"]
        self.assertEqual(load_settings({}, names), dict.fromkeys(names[:3], False) | {"SECURE_HSTS_SECONDS": 0})

    def test_datasource_is_not_redirected(self):
        exempt = load_settings({"WEBVIRTCLOUD_HTTPS": "1"}, ["SECURE_REDIRECT_EXEMPT"])["SECURE_REDIRECT_EXEMPT"]
        with override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=exempt):
            self.assertNotEqual(self.client.get("/datasource/openstack/").status_code, 301)
            response = self.client.get("/accounts/login/")
            self.assertEqual(response.status_code, 301)
            self.assertTrue(response["Location"].startswith("https://"))

    def test_proxied_https_request_is_not_redirected(self):
        # TLS ends at a proxy, which tells Django through X-Forwarded-Proto
        with override_settings(SECURE_SSL_REDIRECT=True):
            response = self.client.get("/accounts/login/", HTTP_X_FORWARDED_PROTO="https")
            self.assertNotEqual(response.status_code, 301)


class AllowedHostsTestCase(SimpleTestCase):
    def hosts(self, env):
        return load_settings(env, ["ALLOWED_HOSTS"])["ALLOWED_HOSTS"]

    def test_allowed_hosts_default_is_not_any_host(self):
        hosts = self.hosts({})
        self.assertNotIn("*", hosts)
        self.assertIn("localhost", hosts)

    def test_allowed_hosts_extended_from_env(self):
        hosts = self.hosts({"ALLOWED_HOSTS": " wvc.example.com, ,192.0.2.10 "})
        self.assertEqual(hosts[-2:], ["wvc.example.com", "192.0.2.10"])

    def test_allowed_hosts_star_escape(self):
        self.assertIn("*", self.hosts({"ALLOWED_HOSTS": "*"}))
