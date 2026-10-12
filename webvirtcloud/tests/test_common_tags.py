from django.contrib.auth import get_user_model
from django.test import TestCase


class NotFoundPageTestCase(TestCase):
    def test_unknown_url_renders_404_for_logged_in_user(self):
        user = get_user_model().objects.create_user("tags_user", "t@example.com", "pw")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/no-such-page/").status_code, 404)
