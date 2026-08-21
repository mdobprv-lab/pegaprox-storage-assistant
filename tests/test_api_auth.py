import json
import pathlib
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))


class FakeRequest:
    method = "GET"
    args = {}
    body = None
    session = {"user": "viewer"}

    def get_json(self, silent=True):
        return self.body


request = FakeRequest()
flask = types.ModuleType("flask")
flask.request = request
flask.jsonify = lambda value=None, **kwargs: value if value is not None else kwargs
flask.send_file = lambda path, mimetype=None: {"path": path, "mimetype": mimetype}
sys.modules.setdefault("flask", flask)

users = {
    "viewer": {"permissions": ["storage.view"]},
    "manager": {
        "permissions": [
            "storage.view", "plugins.manage", "pbs.view",
            "pbs.disks.view", "pbs.datastore.view",
        ]
    },
    "pbs_viewer": {
        "permissions": ["pbs.view", "pbs.disks.view", "pbs.datastore.view"]
    },
}
audits = []
authz_calls = []
access_calls = []

pegaprox = types.ModuleType("pegaprox")
constants = types.ModuleType("pegaprox.constants")
auth = types.ModuleType("pegaprox.utils.auth")
rbac = types.ModuleType("pegaprox.utils.rbac")
audit = types.ModuleType("pegaprox.utils.audit")
auth.load_users = lambda: users
def build_authz_user(username, session):
    authz_calls.append((username, dict(session)))
    result = dict(users.get(username, {}))
    if session.get("api_token"):
        result["permissions"] = list(session.get("token_permissions", []))
        result["effective_role"] = session.get("role", "viewer")
    return result
auth.build_authz_user = build_authz_user
rbac.has_permission = lambda user, permission: permission in user.get("permissions", [])
audit.log_audit = lambda **entry: audits.append(entry)
helpers = types.ModuleType("pegaprox.api.helpers")
helpers.check_cluster_access = lambda cluster_id: (access_calls.append(("pve", cluster_id)) or (True, None))
helpers.check_pbs_access = lambda pbs_id: (access_calls.append(("pbs", pbs_id)) or (True, None))
helpers.get_connected_manager = lambda cluster_id: (types.SimpleNamespace(is_connected=True), None)
globals_module = types.ModuleType("pegaprox.globals")
globals_module.pbs_managers = {
    "pbs": types.SimpleNamespace(connected=True),
    "test-pbs": types.SimpleNamespace(connected=True),
}
sys.modules.setdefault("pegaprox", pegaprox)
sys.modules.setdefault("pegaprox.constants", constants)
sys.modules.setdefault("pegaprox.api", types.ModuleType("pegaprox.api"))
sys.modules.setdefault("pegaprox.api.helpers", helpers)
sys.modules.setdefault("pegaprox.globals", globals_module)
sys.modules.setdefault("pegaprox.utils", types.ModuleType("pegaprox.utils"))
sys.modules.setdefault("pegaprox.utils.auth", auth)
sys.modules.setdefault("pegaprox.utils.rbac", rbac)
sys.modules.setdefault("pegaprox.utils.audit", audit)

from storage_assistant import api
from storage_assistant.tasks import TaskRegistry


class ApiAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        constants.CONFIG_DIR = self.directory.name
        api.init(str(ROOT))
        request.method = "GET"
        request.args = {}
        request.body = None
        request.session = {"user": "viewer"}
        audits.clear()
        authz_calls.clear()
        access_calls.clear()

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def resource():
        return {
            "type": "pve_nfs", "name": "nas-tech", "location": "hq",
            "host": "10.0.0.10", "cluster_id": "pve", "export": "/tech"
        }

    def test_viewer_can_read_but_cannot_write(self):
        status = api.status()
        self.assertFalse(status["can_manage"])
        request.method = "POST"
        request.body = self.resource()
        response, code = api.resources()
        self.assertEqual(code, 403)
        self.assertEqual(response["required"], "plugins.manage")

    def test_token_scoped_user_is_used_for_permission_checks(self):
        request.session = {
            "user": "manager",
            "api_token": "token-id",
            "role": "viewer",
            "token_permissions": ["storage.view"],
        }
        self.assertFalse(api.status()["can_manage"])
        self.assertEqual(authz_calls[-1][0], "manager")
        self.assertEqual(authz_calls[-1][1]["api_token"], "token-id")

    def test_manager_write_and_delete_are_audited(self):
        request.session = {"user": "manager"}
        request.method = "POST"
        request.body = self.resource()
        saved = api.resources()
        resource_id = saved["resources"][0]["id"]
        self.assertEqual(audits[-1]["action"], "storage_assistant.resource_created")

        request.body = {**saved["resources"][0], "description": "duplicate create"}
        duplicate, code = api.resources()
        self.assertEqual(code, 400)
        self.assertEqual(duplicate["error"], "resource.already_exists")

        request.method = "PUT"
        request.body = {**saved["resources"][0], "description": "updated"}
        updated = api.resources()
        self.assertEqual(updated["resources"][0]["description"], "updated")
        self.assertEqual(audits[-1]["action"], "storage_assistant.resource_updated")

        request.method = "DELETE"
        request.args = {"id": resource_id}
        deleted = api.resources()
        self.assertEqual(deleted["resources"], [])
        self.assertEqual(audits[-1]["action"], "storage_assistant.resource_deleted")

    def test_settings_are_allowlisted_and_invalid_values_fall_back(self):
        plugin_dir = pathlib.Path(self.directory.name) / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "config.json").write_text(json.dumps({
            "default_language": "de",
            "theme_override": "javascript:alert(1)",
            "secret": "must-not-be-returned",
        }), encoding="utf-8")
        api.init(str(plugin_dir))
        self.assertEqual(api.settings(), {
            "default_language": "auto",
            "theme_override": "auto",
        })

        (plugin_dir / "config.json").write_text(json.dumps({
            "default_language": "pl",
            "theme_override": "cloud-light",
        }), encoding="utf-8")
        self.assertEqual(api.settings(), {
            "default_language": "pl",
            "theme_override": "cloud-light",
        })

    def test_object_access_is_checked_before_definition_write(self):
        request.session = {"user": "manager"}
        request.method = "POST"
        request.body = self.resource()
        api.resources()
        self.assertIn(("pve", "pve"), access_calls)

    def test_read_only_discovery_task_is_owner_scoped_and_audited(self):
        request.session = {"user": "manager"}
        request.method = "POST"
        request.body = self.resource()
        saved = api.resources()["resources"][0]
        registry = TaskRegistry(max_workers=1, max_tasks=8, ttl_seconds=60)
        previous = api.TASKS
        api.TASKS = registry
        try:
            request.body = {"resource_id": saved["id"]}
            with patch.object(api, "discover", return_value={
                "kind": "pve_nfs", "read_only": True, "status": "matches",
            }):
                started, code = api.discovery()
                self.assertEqual(code, 202)
                deadline = time.time() + 2
                task = started
                while task["state"] not in {"succeeded", "failed", "cancelled"}:
                    self.assertLess(time.time(), deadline)
                    time.sleep(0.01)
                    request.method = "GET"
                    request.args = {"id": started["id"]}
                    task = api.discovery()
            self.assertEqual(task["state"], "succeeded")
            self.assertTrue(task["result"]["read_only"])
            self.assertTrue(any(
                item["action"] == "storage_assistant.discovery_started" for item in audits
            ))
            request.session = {"user": "viewer"}
            hidden, hidden_code = api.discovery()
            self.assertEqual(hidden_code, 404)
            self.assertEqual(hidden["error"], "discovery.task_not_found")
        finally:
            registry.shutdown()
            api.TASKS = previous

    def test_pbs_discovery_requires_all_read_permissions(self):
        request.session = {"user": "manager"}
        request.method = "POST"
        request.body = {
            "type": "pbs_iscsi", "name": "pbs-test", "location": "lab",
            "host": "192.0.2.20", "pbs_id": "pbs", "target_iqn": "iqn.2026-08.invalid:test",
            "lun": 0, "wwid": "3600000000000001", "filesystem": "xfs",
            "filesystem_mode": "reuse", "datastore": "backup-test", "multipath": False,
        }
        saved = api.resources()["resources"][0]
        request.session = {"user": "viewer"}
        request.body = {"resource_id": saved["id"]}
        denied, code = api.discovery()
        self.assertEqual(code, 403)
        self.assertEqual(denied["required"], "pbs.view")

    def test_pbs_only_viewer_sees_pbs_definitions_without_storage_view(self):
        request.session = {"user": "manager"}
        request.method = "POST"
        request.body = self.resource()
        api.resources()
        request.body = {
            "type": "pbs_iscsi", "name": "pbs-test", "location": "lab",
            "host": "192.0.2.20", "pbs_id": "pbs", "target_iqn": "iqn.2026-08.invalid:test",
            "lun": 0, "wwid": "3600000000000001", "filesystem": "xfs",
            "filesystem_mode": "reuse", "datastore": "backup-test", "multipath": False,
        }
        api.resources()
        request.session = {"user": "pbs_viewer"}
        request.method = "GET"
        visible = api.resources()["resources"]
        self.assertEqual([item["type"] for item in visible], ["pbs_iscsi"])
        status = api.status()
        self.assertFalse(status["can_discover_pve"])
        self.assertTrue(status["can_discover_pbs"])

    def test_update_authorizes_existing_and_new_object_scope(self):
        request.session = {"user": "manager"}
        request.method = "POST"
        request.body = self.resource()
        saved = api.resources()["resources"][0]
        original = helpers.check_cluster_access
        denied = ({"error": "old-scope-denied"}, 403)
        helpers.check_cluster_access = lambda cluster_id: (
            (False, denied) if cluster_id == "pve" else (True, None)
        )
        try:
            request.method = "PUT"
            request.body = {**saved, "cluster_id": "pve-new", "description": "changed"}
            response, code = api.resources()
            self.assertEqual(code, 403)
            self.assertEqual(response["error"], "old-scope-denied")
            self.assertEqual(api._find_resource(saved["id"])["description"], "")
        finally:
            helpers.check_cluster_access = original


if __name__ == "__main__":
    unittest.main()
