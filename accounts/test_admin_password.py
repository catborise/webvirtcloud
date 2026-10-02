"""ROADMAP O-02: the generated admin password must not be printed to the log."""
import io
import os
import stat
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase, override_settings

from accounts.apps import create_admin


class GeneratedAdminPasswordTestCase(TestCase):
    def _run_create_admin(self, base_dir):
        get_user_model().objects.all().delete()
        migration = MagicMock(app_label="accounts")
        migration.name = "0001_initial"
        out = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_PASSWORD", "ADMIN_USERNAME")}
        with override_settings(BASE_DIR=Path(base_dir)), patch("sys.argv", ["manage.py", "migrate"]), patch.dict(
            os.environ, env, clear=True
        ), redirect_stdout(out):
            create_admin(sender=None, plan=[(migration, False)])
        return out.getvalue()

    def test_password_is_written_to_a_private_file_not_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "data").mkdir()
            output = self._run_create_admin(tmp)
            password_file = Path(tmp) / "data" / "admin_password"

            self.assertTrue(password_file.exists())
            password = password_file.read_text().strip()
            self.assertEqual(stat.S_IMODE(password_file.stat().st_mode), 0o600)
            self.assertNotIn(password, output)
            self.assertIn(str(password_file), output)
            self.assertIsNotNone(authenticate(username="admin", password=password))

    def test_does_not_follow_a_planted_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "data").mkdir()
            victim = Path(tmp) / "victim"
            victim.write_text("original\n")
            (Path(tmp) / "data" / "admin_password").symlink_to(victim)

            self._run_create_admin(tmp)

            password_file = Path(tmp) / "data" / "admin_password"
            self.assertEqual(victim.read_text(), "original\n")
            self.assertFalse(password_file.is_symlink())
            self.assertEqual(stat.S_IMODE(password_file.stat().st_mode), 0o600)
