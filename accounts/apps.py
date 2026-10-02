import os

from django.apps import AppConfig
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_migrate


def admin_password_path():
    from django.conf import settings

    return os.path.join(str(settings.BASE_DIR), "data", "admin_password")


def flag_generated_password(sender, request, user, **kwargs):
    """
    While a user still has the generated admin password, make them set a new
    one before using the panel (ROADMAP O-17; see ForcePasswordChangeMiddleware).
    """
    try:
        with open(admin_password_path()) as f:
            generated = f.read().strip()
    except OSError:
        return
    if generated and user.check_password(generated):
        request.session["must_change_password"] = True


def apply_change_password(sender, **kwargs):
    """
    Apply new change_password permission for all users
    Depending on settings SHOW_PROFILE_EDIT_PASSWORD
    """
    from django.conf import settings
    from django.contrib.auth.models import Permission, User

    if hasattr(settings, "SHOW_PROFILE_EDIT_PASSWORD"):
        print("\033[1m! \033[92mSHOW_PROFILE_EDIT_PASSWORD is found inside settings.py\033[0m")
        print("\033[1m* \033[92mApplying permission can_change_password for all users\033[0m")
        users = User.objects.all()
        permission = Permission.objects.get(codename="change_password")
        if settings.SHOW_PROFILE_EDIT_PASSWORD:
            print("\033[1m! \033[91mWarning!!! Setting to True for all users\033[0m")
            for user in users:
                user.user_permissions.add(permission)
        else:
            print("\033[1m* \033[91mWarning!!! Setting to False for all users\033[0m")
            for user in users:
                user.user_permissions.remove(permission)
        print("\033[1m! Don`t forget to remove the option from settings.py\033[0m")


def _store_generated_password(password):
    """
    Write the generated admin password to data/admin_password (mode 0600)
    instead of printing it, so it does not end up in install logs or
    `docker logs` (ROADMAP O-02). Falls back to printing if the file cannot
    be written, so the admin is never locked out.
    """
    path = admin_password_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Never write through a pre-existing file or symlink (this runs as
        # root in Docker, while data/ belongs to www-data).
        if os.path.lexists(path):
            os.unlink(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password + "\n")
        print(f"\033[1m* \033[93mGenerated admin password written to {path}\033[0m")
    except OSError:
        print(f"\033[1m* \033[93mGenerated random admin password: {password}\033[0m")


def create_admin(sender, **kwargs):
    """
    Create initial admin user
    """
    import os
    import secrets
    import sys
    from django.contrib.auth.models import User

    from accounts.models import UserAttributes

    plan = kwargs.get("plan", [])
    for migration, rolled_back in plan:
        if (
            migration.app_label == "accounts"
            and migration.name == "0001_initial"
            and not rolled_back
        ):
            if User.objects.count() == 0:
                is_testing = "test" in sys.argv
                admin_user = os.environ.get("ADMIN_USERNAME", "admin")
                admin_pass = os.environ.get("ADMIN_PASSWORD")

                if not admin_pass:
                    if is_testing:
                        admin_pass = "admin"
                    else:
                        admin_pass = secrets.token_urlsafe(16)
                        _store_generated_password(admin_pass)

                print(f"\033[1m* \033[92mCreating default admin user '{admin_user}'\033[0m")
                admin = User.objects.create_superuser(admin_user, None, admin_pass)
                UserAttributes(
                    user=admin,
                    max_instances=-1,
                    max_cpus=-1,
                    max_memory=-1,
                    max_disk_size=-1,
                ).save()
            break


class AccountsConfig(AppConfig):
    name = "accounts"
    verbose_name = "Accounts"

    def ready(self):
        post_migrate.connect(create_admin, sender=self)
        post_migrate.connect(apply_change_password, sender=self)
        user_logged_in.connect(flag_generated_password)
