"""Every {% bs_icon %} of the templates is in the committed icon cache
(BS_ICONS_CACHE). A missing one is fetched from the CDN when a page is
rendered, or shown as an error where the server has no internet access."""

import re
import unittest
from pathlib import Path

from django.conf import settings

BASE = Path(settings.BASE_DIR)
CALL = re.compile(r"\{%\s*bs_icon\s+(.*?)\s*%\}")
NAME = re.compile(r"""^['"]([^'"]+)['"]""")
KWARG = re.compile(r"""(\w+)=['"]([^'"]*)['"]""")


class IconCacheTestCase(unittest.TestCase):
    def test_every_template_icon_is_cached(self):
        cache = Path(settings.BS_ICONS_CACHE)
        missing = []
        templates = [*BASE.glob("templates/**/*.html"), *BASE.glob("*/templates/**/*.html")]
        self.assertGreater(len(templates), 50)  # the globs still find the templates
        for template in templates:
            for args in CALL.findall(template.read_text(encoding="utf-8")):
                name = NAME.match(args)
                self.assertIsNotNone(name, f"{template}: icon name is not a literal: {args}")
                kw = dict(KWARG.findall(args))
                # the file name django-bootstrap-icons uses for its cache
                cached = f"{name.group(1)}_{kw.get('size')}_{kw.get('color')}_{kw.get('extra_classes')}.svg".replace(" ", "_")
                if not (cache / cached).exists():
                    missing.append(f"{template.relative_to(BASE)}: {cached}")
        self.assertEqual(missing, [])
