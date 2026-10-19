"""Every response carries the Content-Security-Policy directives that need no
change to the templates' inline scripts and styles."""

from django.test import TestCase
from django.urls import reverse


class ContentSecurityPolicyTests(TestCase):
    def test_pages_and_redirects_carry_the_policy(self):
        for url in (reverse("accounts:login"), reverse("index")):  # a page, and a redirect to the login
            with self.subTest(url=url):
                policy = self.client.get(url)["Content-Security-Policy"]
                self.assertEqual(
                    policy, "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
                )
