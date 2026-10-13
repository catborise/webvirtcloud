from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _sqlite_wal(sender, connection, **kwargs):
    """The worker and gunicorn write the same SQLite file: WAL lets readers
    go on during a write. Django's SQLite OPTIONS have no init command."""
    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL")


class OperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "operations"

    def ready(self):
        connection_created.connect(_sqlite_wal)
        # every installed app's jobs.py registers its kinds
        from django.utils.module_loading import autodiscover_modules

        autodiscover_modules("jobs")
