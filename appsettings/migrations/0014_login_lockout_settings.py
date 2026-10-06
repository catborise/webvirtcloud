from django.db import migrations


SETTINGS = [
    ("Login Failure Limit", "LOGIN_FAILURE_LIMIT", "5",
     "Failed logins of a user from one address before it is locked"),
    ("Login Lockout Minutes", "LOGIN_LOCKOUT_MINUTES", "15",
     "How long a locked login stays locked, in minutes"),
]


def add_settings(apps, schema_editor):
    setting = apps.get_model("appsettings", "AppSettings")
    db_alias = schema_editor.connection.alias
    for name, key, value, description in SETTINGS:
        setting.objects.using(db_alias).get_or_create(
            key=key, defaults={"name": name, "value": value, "choices": "", "description": description}
        )


def del_settings(apps, schema_editor):
    setting = apps.get_model("appsettings", "AppSettings")
    db_alias = schema_editor.connection.alias
    setting.objects.using(db_alias).filter(key__in=[key for _, key, _, _ in SETTINGS]).delete()


class Migration(migrations.Migration):
    dependencies = [("appsettings", "0013_remove_qemu_console_default_type")]

    operations = [migrations.RunPython(add_settings, del_settings)]
