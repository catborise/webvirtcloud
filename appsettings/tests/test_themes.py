"""The themes in dev/scss/wvc-themes compile with the pinned libsass, and the
shipped wvc-main.min.css is the default theme compiled from these sources."""

from pathlib import Path

import sass
from django.conf import settings
from django.test import SimpleTestCase

SCSS = Path(settings.BASE_DIR, "dev", "scss")


def compile_theme(theme):
    # the imports the settings view compiles
    return sass.compile(
        string=f"@import '{SCSS}/wvc-themes/{theme}/variables';\n"
        f"@import '{SCSS}/bootstrap-overrides.scss';\n"
        f"@import '{SCSS}/wvc-themes/{theme}/bootswatch';",
        output_style="compressed",
    )


class ThemeTests(SimpleTestCase):
    def test_every_theme_compiles(self):
        themes = sorted(p.name for p in (SCSS / "wvc-themes").iterdir() if p.is_dir())
        self.assertIn("zephyr", themes)
        for theme in themes:
            with self.subTest(theme=theme):
                self.assertIn(".btn", compile_theme(theme))

    def test_the_shipped_css_is_zephyr_from_these_sources(self):
        shipped = Path(settings.BASE_DIR, "static", "css", "wvc-main.min.css").read_text()
        self.assertEqual(shipped, compile_theme("zephyr"))
