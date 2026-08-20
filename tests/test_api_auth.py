import pathlib
import sys
import tempfile
import types
import unittest

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
    "manager": {"permissions": ["storage.view", "plugins.manage"]},
}
audits = []

pegaprox = types.ModuleType("pegaprox")
constants = types.ModuleType("pegaprox.constants")
auth = types.ModuleType("pegaprox.utils.auth")
rbac = types.ModuleType("pegaprox.utils.rbac")
audit = types.ModuleType("pegaprox.utils.audit")
auth.load_users = lambda: users
rbac.has_permission = lambda user, permission: permission in user.get("permissions", [])
audit.log_audit = lambda **entry: audits.append(entry)
sys.modules.setdefault("pegaprox", pegaprox)
sys.modules.setdefault("pegaprox.constants", constants)
sys.modules.setdefault("pegaprox.utils", types.ModuleType("pegaprox.utils"))
sys.modules.setdefault("pegaprox.utils.auth", auth)
sys.modules.setdefault("pegaprox.utils.rbac", rbac)
sys.modules.setdefault("pegaprox.utils.audit", audit)

from storage_assistant import api


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

    def test_manager_write_and_delete_are_audited(self):
        request.session = {"user": "manager"}
        request.method = "POST"
        request.body = self.resource()
        saved = api.resources()
        resource_id = saved["resources"][0]["id"]
        self.assertEqual(audits[-1]["action"], "storage_assistant.resource_saved")
        request.method = "DELETE"
        request.args = {"id": resource_id}
        deleted = api.resources()
        self.assertEqual(deleted["resources"], [])
        self.assertEqual(audits[-1]["action"], "storage_assistant.resource_deleted")


if __name__ == "__main__":
    unittest.main()
