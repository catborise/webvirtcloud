import os
from pathlib import Path

import sass
from admin.decorators import superuser_only
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_noop as _
from logs.views import addlogmsg

from appsettings.models import AppSettings


def sass_dir_in_project(value):
    """True if the SASS_DIR value is an existing directory inside the project:
    the page lists its themes and writes wvc-main.scss into it."""
    path = (Path(settings.BASE_DIR) / value).resolve()
    return path.is_dir() and path.is_relative_to(Path(settings.BASE_DIR).resolve())


@superuser_only
def appsettings(request):
    """
    :param request:
    :return:
    """
    main_css = "wvc-main.min.css"
    sass_dir = AppSettings.objects.get(key="SASS_DIR")
    bootstrap_theme = AppSettings.objects.get(key="BOOTSTRAP_THEME")
    themes_list = []
    if not sass_dir_in_project(sass_dir.value):
        messages.error(request, _("SASS directory path must be a directory inside the project: %(dir)s") % {"dir": sass_dir.value})
    else:
        try:
            themes_list = os.listdir(sass_dir.value + "/wvc-themes")
        except FileNotFoundError as err:
            messages.error(request, err)
            addlogmsg(request.user.username, "-", "", err)

    # Bootstrap settings related with filesystems, because of that they are excluded from other settings
    appsettings = AppSettings.objects.exclude(
        description__startswith="Bootstrap"
    ).order_by("name")

    if request.method == "POST":
        if "SASS_DIR" in request.POST:
            if not sass_dir_in_project(request.POST.get("SASS_DIR", "")):
                messages.error(
                    request,
                    _("SASS directory path must be a directory inside the project: %(dir)s")
                    % {"dir": request.POST.get("SASS_DIR", "")},
                )
                return HttpResponseRedirect(request.get_full_path())
            try:
                sass_dir.value = request.POST.get("SASS_DIR", "")
                sass_dir.save()

                msg = _("SASS directory path is changed. Now: %(dir)s") % {
                    "dir": sass_dir.value
                }
                messages.success(request, msg)
            except Exception as err:
                msg = err
                messages.error(request, msg)

            addlogmsg(request.user.username, "-", "", msg)
            return HttpResponseRedirect(request.get_full_path())

        if "BOOTSTRAP_THEME" in request.POST:
            theme = request.POST.get("BOOTSTRAP_THEME", "")
            # the name goes into @import paths: only the themes in SASS_DIR
            if theme not in themes_list:
                messages.error(request, _("Unknown theme: %(theme)s") % {"theme": theme})
                return HttpResponseRedirect(request.get_full_path())
            scss_var = f"@import '{sass_dir.value}/wvc-themes/{theme}/variables';"
            # scss_boot = f"@import '{sass_dir.value}/bootstrap/bootstrap.scss';"
            scss_boot = f"@import '{sass_dir.value}/bootstrap-overrides.scss';"
            scss_bootswatch = (
                f"@import '{sass_dir.value}/wvc-themes/{theme}/bootswatch';"
            )

            try:
                with open(sass_dir.value + "/wvc-main.scss", "w") as main:
                    main.write(
                        scss_var + "\n" + scss_boot + "\n" + scss_bootswatch + "\n"
                    )

                css_compressed = sass.compile(
                    string=scss_var + "\n" + scss_boot + "\n" + scss_bootswatch,
                    output_style="compressed",
                )
                with open("static/css/" + main_css, "w") as css:
                    css.write(css_compressed)

                bootstrap_theme.value = theme
                bootstrap_theme.save()

                msg = _("Theme is changed. Now: %(theme)s") % {"theme": theme}
                messages.success(request, msg)
            except Exception as err:
                msg = err
                messages.error(request, msg)

            addlogmsg(request.user.username, "-", "", msg)
            return HttpResponseRedirect(request.get_full_path())

        for setting in appsettings:
            if setting.key in request.POST:
                try:
                    setting.value = request.POST.get(setting.key, "")
                    setting.save()

                    msg = _("%(setting)s is changed. Now: %(value)s") % {
                        "setting": setting.name,
                        "value": setting.value,
                    }
                    messages.success(request, msg)
                except Exception as err:
                    msg = err
                    messages.error(request, msg)

                addlogmsg(request.user.username, "-", "", msg)
                return HttpResponseRedirect(request.get_full_path())

    return render(request, "appsettings.html", locals())
