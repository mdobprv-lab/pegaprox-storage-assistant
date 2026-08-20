import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class StaticContractTests(unittest.TestCase):
    def test_locales_have_parity_and_cover_literal_ui_keys(self):
        english = json.loads((ROOT / "locales/en.json").read_text(encoding="utf-8"))
        polish = json.loads((ROOT / "locales/pl.json").read_text(encoding="utf-8"))
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        used = set(re.findall(r'data-i18n="([^"]+)"', html))
        used.update(re.findall(r'data-i18n-aria="([^"]+)"', html))
        used.update(re.findall(r"\bt\('([^']+)'\)", html))
        used.update({"type.pve_nfs", "type.pbs_iscsi"})
        self.assertEqual(set(english), set(polish))
        self.assertEqual(sorted(used - set(english)), [])

    def test_locales_cover_backend_validation_keys(self):
        english = json.loads((ROOT / "locales/en.json").read_text(encoding="utf-8"))
        validation = (ROOT / "src/storage_assistant/validation.py").read_text(encoding="utf-8")
        literal = set(re.findall(r'ValidationError\("([^"]+)"\)', validation))
        text_fields = set(re.findall(r'_text\([^\n]+, "([^"]+)"', validation))
        integer_fields = set(re.findall(r'_integer\([^\n]+, "([^"]+)"', validation))
        boolean_fields = set(re.findall(r'_boolean\([^\n]+, "([^"]+)"', validation))
        reachable = literal
        reachable.update(f"{field}.{suffix}" for field in text_fields
                         for suffix in ("required", "too_long", "invalid"))
        reachable.update(f"{field}.invalid" for field in integer_fields | boolean_fields)
        self.assertEqual(sorted(reachable - set(english)), [])

    def test_all_pegaprox_layout_variants_are_present(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        for theme in ("modern-dark", "corporate-dark", "corporate-light", "cloud-dark", "cloud-light"):
            self.assertIn(theme, html)
        self.assertIn("dataCloudTheme", html.replace(".dataset.cloudTheme", ".dataCloudTheme"))

    def test_runtime_plugin_id_is_consistent(self):
        expected = "storage-assistant"
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], expected)
        self.assertIn(f'PLUGIN_ID = "{expected}"', (ROOT / "__init__.py").read_text())
        self.assertIn(f'PLUGIN_ID="{expected}"', (ROOT / "install.sh").read_text())
        self.assertIn(f"/api/plugins/{expected}/api", (ROOT / "src/ui/plugin.html").read_text())
