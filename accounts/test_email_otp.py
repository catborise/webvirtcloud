"""The "Lost OTP?" page mails a user their OTP QR code without a login. It
exists only while OTP is enabled, mails one exact match at most once per
interval, and never fails with an error page."""

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

URL = reverse("accounts:email_otp")


@override_settings(
    OTP_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "email-otp-tests"}},
)
class EmailOTPTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("otp-user", email="otp.user@example.com", password="x")

    def post(self, email):
        response = self.client.post(URL, {"email": email})
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        return response

    def test_one_mail_to_the_user(self):
        self.post("OTP.User@Example.com")  # addresses match case-insensitively
        self.assertEqual([m.to for m in mail.outbox], [["otp.user@example.com"]])

    def test_repeated_requests_send_one_mail_per_interval(self):
        for _ in range(20):
            self.post("otp.user@example.com")
        self.assertEqual(len(mail.outbox), 1)

    def test_an_address_of_two_users_sends_nothing(self):
        get_user_model().objects.create_user("otp-twin", email="otp.user@example.com", password="x")
        self.post("otp.user@example.com")
        self.assertEqual(mail.outbox, [])

    def test_unknown_and_inactive_addresses_send_nothing(self):
        get_user_model().objects.create_user("otp-gone", email="gone@example.com", password="x", is_active=False)
        self.post("nobody@example.com")
        self.post("gone@example.com")
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="127.0.0.1", EMAIL_PORT=1)
    def test_a_mail_server_that_is_down_is_no_error_page(self):
        with self.assertLogs("accounts.views", level="ERROR"):
            self.post("otp.user@example.com")


@override_settings(OTP_ENABLED=False)
class EmailOTPDisabledTests(TestCase):
    def test_the_page_does_not_exist_without_otp(self):
        get_user_model().objects.create_user("otp-off", email="off@example.com", password="x")
        self.assertEqual(self.client.get(URL).status_code, 404)
        self.assertEqual(self.client.post(URL, {"email": "off@example.com"}).status_code, 404)
        self.assertEqual(mail.outbox, [])
