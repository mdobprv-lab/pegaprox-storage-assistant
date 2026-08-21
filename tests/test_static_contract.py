import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class StaticContractTests(unittest.TestCase):
    def test_generic_editor_config_is_valid_and_installed_without_overwrite(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config, {
            "default_language": "auto",
            "theme_override": "auto",
        })
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"$SRC/config.json"', installer)
        self.assertIn('[[ ! -f "$DEST/config.json" ]]', installer)

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

    def test_review_includes_safety_critical_resource_fields(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        for key in (
            "field.location", "field.host", "field.cluster", "field.export",
            "field.nfs_version", "field.content", "field.nodes", "field.pbs",
            "review.primary_portal", "field.target_iqn", "field.lun", "field.wwid",
            "field.filesystem", "field.filesystem_mode", "field.datastore",
            "field.multipath", "field.portals", "field.description",
        ):
            self.assertIn(f"t('{key}')", html)
        self.assertIn("$('form-error').textContent=''", html)
        self.assertIn("reviewedResource=currentPlan.resource||resource", html)
        self.assertIn("JSON.stringify(reviewedResource)", html)
        self.assertIn("form.elements.id.value=''", html)
        self.assertIn("editingResourceId?'PUT':'POST'", html)

    def test_runtime_plugin_id_is_consistent(self):
        expected = "storage-assistant"
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], expected)
        self.assertIn(f'PLUGIN_ID = "{expected}"', (ROOT / "__init__.py").read_text())
        self.assertIn(f'PLUGIN_ID="{expected}"', (ROOT / "install.sh").read_text())
        self.assertIn(f"/api/plugins/{expected}/api", (ROOT / "src/ui/plugin.html").read_text())

    def test_manifest_and_package_versions_match(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        package = (ROOT / "src/storage_assistant/__init__.py").read_text(encoding="utf-8")
        self.assertIn(f'__version__ = "{manifest["version"]}"', package)

    def test_discovery_strings_have_english_and_polish_translations(self):
        english = json.loads((ROOT / "locales/en.json").read_text(encoding="utf-8"))
        polish = json.loads((ROOT / "locales/pl.json").read_text(encoding="utf-8"))
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src/storage_assistant").glob("*.py")
        )
        keys = set(re.findall(r'["\'](discovery\.[a-z0-9_.]+)["\']', sources))
        self.assertEqual(sorted(keys - set(english)), [])
        self.assertEqual(sorted(keys - set(polish)), [])

    def test_private_pegaprox_seams_are_isolated_in_compat_adapter(self):
        modules = {
            path.name: path.read_text(encoding="utf-8")
            for path in (ROOT / "src/storage_assistant").glob("*.py")
        }
        for name, source in modules.items():
            if name == "compat.py":
                continue
            for private in ("_api_get", "_ssh_connect", "pbs_managers", "cluster_managers"):
                self.assertNotIn(private, source, f"{private} escaped into {name}")

    def test_discovery_commands_are_read_only_and_allowlisted(self):
        compat = (ROOT / "src/storage_assistant/compat.py").read_text(encoding="utf-8")
        commands = compat.split("_READ_ONLY_COMMANDS =", 1)[1].split(
            "def pbs_read_only_command", 1
        )[0]
        for forbidden in (
            "-m discovery", "--login", "--logout", "mkfs", "wipefs",
            "mount ", "umount ", "proxmox-backup-manager datastore create",
        ):
            self.assertNotIn(forbidden, commands)
        self.assertIn('"discovery": discovery', (
            ROOT / "src/storage_assistant/api.py"
        ).read_text(encoding="utf-8"))

    def test_cancel_button_is_disabled_after_cancellation_is_requested(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        match = re.search(
            r"\$\('discovery-cancel'\)\.disabled=([^;]+);", html
        )
        self.assertIsNotNone(match)
        self.assertIn("'queued','running'", match.group(1))
        self.assertNotIn("cancel_requested", match.group(1))

    def test_confirmation_dialogs_are_plugin_native_and_theme_aware(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        self.assertNotRegex(html, r"\bconfirm\(")
        self.assertIn('id="confirm-modal"', html)
        self.assertIn("confirm-backdrop", html)
        self.assertIn("askConfirmation(", html)
        self.assertIn("resolveConfirmation(false)", html)

    def test_cancel_confirmation_pauses_and_can_resume_polling(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        self.assertIn(
            "pollingPaused=true;stopPolling();const accepted=await askConfirmation",
            html,
        )
        self.assertIn(
            "if(!accepted){pollingPaused=false;"
            "if(currentTask?.id===taskId)pollTimer=setTimeout(pollTask,0);return}",
            html,
        )
        self.assertIn("if(pollingPaused)return;renderTask(task)", html)

    def test_confirmed_cancel_has_one_terminal_ui_for_all_workflows(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        cancel_function = html.split("async function cancelDiscovery()", 1)[1].split(
            "function closeDiscovery()", 1
        )[0]
        self.assertIn("renderCancelledByUser(task)", cancel_function)
        self.assertNotIn("task.state===", cancel_function)
        self.assertNotIn("renderTask(task)", cancel_function)
        self.assertNotIn("pollTimer=setTimeout(pollTask,250)", cancel_function)
        self.assertNotIn("const latest=await api", cancel_function)
        self.assertIn("state:'cancelled'", html)
        self.assertIn("phase:'discovery.phase.cancelled'", html)
        self.assertIn("progress:100", html)
        self.assertIn("result:null", html)
        self.assertIn(".progress-bar.cancelled", html)
        self.assertIn(
            "classList.toggle('cancelled',task.state==='cancelled')",
            html,
        )
        self.assertNotIn("finished_before_cancel", html)

    def test_review_marks_execution_steps_as_future_only(self):
        english = json.loads((ROOT / "locales/en.json").read_text(encoding="utf-8"))
        polish = json.loads((ROOT / "locales/pl.json").read_text(encoding="utf-8"))
        self.assertIn("not executed in 0.2.0", english["review.operations"])
        self.assertIn("niewykonywane w 0.2.0", polish["review.operations"])
        self.assertTrue(english["plan.iscsi.login"].startswith("Would "))
        self.assertTrue(polish["plan.iscsi.login"].startswith("W przyszłości:"))

    def test_cancelled_task_is_not_rendered_as_an_error(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        self.assertIn("task.state!=='cancelled'", html)
        self.assertIn(".pill.cancelled", html)

    def test_language_is_inherited_from_pegaprox(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        self.assertNotIn('id="lang"', html)
        self.assertNotIn("storage-assistant.lang", html)
        self.assertIn("/api/user/preferences", html)
        self.assertIn("pegaprox-language", html)
        self.assertIn("window.addEventListener('storage'", html)
        self.assertIn("normalizeLanguage", html)

    def test_read_only_discovery_permissions_match_pegaprox_guidance(self):
        api = (ROOT / "src/storage_assistant/api.py").read_text(encoding="utf-8")
        for permission in (
            "storage.view", "pbs.view", "pbs.disks.view", "pbs.datastore.view",
        ):
            self.assertIn(permission, api)
        for forbidden in ("pbs.config", "pbs.datastore.create", "pbs.disks.smart"):
            self.assertNotIn(forbidden, api)

    def test_pve_progress_uses_pegaprox_sse_with_polling_fallback(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        compat = (ROOT / "src/storage_assistant/compat.py").read_text(encoding="utf-8")
        self.assertIn("/api/sse/token", html)
        self.assertIn("new EventSource", html)
        self.assertIn("message.type!=='storage_assistant_discovery'", html)
        self.assertIn("message.data?.task_id!==task.id", html)
        self.assertIn("task.resource_type!=='pve_nfs'", html)
        self.assertIn("taskEventConnected?5000:750", html)
        self.assertIn('broadcast_sse(', compat)
        self.assertIn('cluster_id=cluster_id', compat)

    def test_pbs_progress_does_not_use_unscoped_sse(self):
        html = (ROOT / "src/ui/plugin.html").read_text(encoding="utf-8")
        compat = (ROOT / "src/storage_assistant/compat.py").read_text(encoding="utf-8")
        self.assertIn("if not cluster_id", compat)
        self.assertIn("task.resource_type!=='pve_nfs'", html)
        self.assertIn("pollTask", html)
