import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from storage_assistant import compat


def _install_pegaprox_stubs():
    flask = sys.modules.setdefault("flask", types.ModuleType("flask"))
    flask.jsonify = getattr(flask, "jsonify", lambda value=None, **kwargs: value or kwargs)
    pegaprox = sys.modules.setdefault("pegaprox", types.ModuleType("pegaprox"))
    api_package = sys.modules.setdefault("pegaprox.api", types.ModuleType("pegaprox.api"))
    helpers = sys.modules.setdefault(
        "pegaprox.api.helpers", types.ModuleType("pegaprox.api.helpers")
    )
    helpers.check_cluster_access = getattr(
        helpers, "check_cluster_access", lambda _cluster_id: (True, None)
    )
    helpers.check_pbs_access = getattr(
        helpers, "check_pbs_access", lambda _pbs_id: (True, None)
    )
    helpers.get_connected_manager = getattr(
        helpers, "get_connected_manager", lambda _cluster_id: (object(), None)
    )
    globals_module = sys.modules.setdefault(
        "pegaprox.globals", types.ModuleType("pegaprox.globals")
    )
    globals_module.pbs_managers = getattr(globals_module, "pbs_managers", {})
    pegaprox.api = api_package
    api_package.helpers = helpers
    return helpers, globals_module


class TrackingManagers(dict):
    def __init__(self, *args, events, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = events

    def get(self, key, default=None):
        self.events.append(("lookup", key))
        return super().get(key, default)


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return {"data": self.data}


class CompatTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        helpers, globals_module = _install_pegaprox_stubs()
        self.old_check_pve = helpers.check_cluster_access
        self.old_check_pbs = helpers.check_pbs_access
        self.old_get_pve = helpers.get_connected_manager
        self.old_pbs = globals_module.pbs_managers

    def tearDown(self):
        helpers = sys.modules["pegaprox.api.helpers"]
        helpers.check_cluster_access = self.old_check_pve
        helpers.check_pbs_access = self.old_check_pbs
        helpers.get_connected_manager = self.old_get_pve
        sys.modules["pegaprox.globals"].pbs_managers = self.old_pbs

    def test_pve_is_authorized_before_manager_resolution(self):
        helpers = sys.modules["pegaprox.api.helpers"]
        helpers.check_cluster_access = lambda value: (
            self.events.append(("check", value)) or (True, None)
        )
        manager = object()
        helpers.get_connected_manager = lambda value: (
            self.events.append(("resolve", value)) or (manager, None)
        )
        resolved, error = compat.resolve_pve("cluster-a")
        self.assertIs(resolved, manager)
        self.assertIsNone(error)
        self.assertEqual(self.events, [("check", "cluster-a"), ("resolve", "cluster-a")])

    def test_denied_pbs_does_not_resolve_manager(self):
        helpers = sys.modules["pegaprox.api.helpers"]
        denied = ({"error": "denied"}, 403)
        helpers.check_pbs_access = lambda value: (
            self.events.append(("check", value)) or (False, denied)
        )
        sys.modules["pegaprox.globals"].pbs_managers = TrackingManagers(
            {"pbs-a": object()}, events=self.events
        )
        manager, error = compat.resolve_pbs("pbs-a")
        self.assertIsNone(manager)
        self.assertIs(error, denied)
        self.assertEqual(self.events, [("check", "pbs-a")])

    def test_allowed_pbs_is_checked_before_lookup(self):
        helpers = sys.modules["pegaprox.api.helpers"]
        manager = types.SimpleNamespace(connected=True)
        helpers.check_pbs_access = lambda value: (
            self.events.append(("check", value)) or (True, None)
        )
        sys.modules["pegaprox.globals"].pbs_managers = TrackingManagers(
            {"pbs-a": manager}, events=self.events
        )
        resolved, error = compat.resolve_pbs("pbs-a")
        self.assertIs(resolved, manager)
        self.assertIsNone(error)
        self.assertEqual(self.events, [("check", "pbs-a"), ("lookup", "pbs-a")])

    def test_private_pve_api_is_feature_detected(self):
        calls = []
        manager = types.SimpleNamespace(
            host="pve.invalid",
            api_port=8006,
            _api_get=lambda url, **kwargs: (
                calls.append((url, kwargs)) or FakeResponse([{"node": "pve01"}])
            ),
        )
        data = compat.pve_nodes(manager)
        self.assertEqual(data, [{"node": "pve01"}])
        self.assertEqual(calls[0][0], "https://pve.invalid:8006/api2/json/nodes")

    def test_future_public_pve_api_may_return_a_list_directly(self):
        manager = types.SimpleNamespace(
            api_get=lambda path, params=None: [{"node": "pve01"}]
        )
        self.assertEqual(compat.pve_nodes(manager), [{"node": "pve01"}])

    def test_missing_pve_api_seam_is_controlled(self):
        with self.assertRaises(compat.CompatibilityError) as raised:
            compat.pve_nodes(types.SimpleNamespace(host="pve.invalid", api_port=8006))
        self.assertEqual(raised.exception.code, "discovery.pve_api_unavailable")

    def test_pve_transport_timeout_is_controlled(self):
        def timeout(_url, **_kwargs):
            raise TimeoutError("private remote detail")

        manager = types.SimpleNamespace(
            host="pve.invalid", api_port=8006, _api_get=timeout
        )
        with self.assertRaises(compat.CompatibilityError) as raised:
            compat.pve_nodes(manager)
        self.assertEqual(raised.exception.code, "discovery.remote_api_failed")
        self.assertNotIn("private remote detail", str(raised.exception))

    def test_pbs_commands_are_hard_coded(self):
        with self.assertRaises(compat.CompatibilityError) as raised:
            compat.pbs_read_only_command(object(), "echo-user-input")
        self.assertEqual(raised.exception.code, "discovery.command_not_allowed")

    def test_pve_progress_uses_cluster_scoped_pegaprox_sse(self):
        calls = []
        utils = sys.modules.setdefault(
            "pegaprox.utils", types.ModuleType("pegaprox.utils")
        )
        previous = sys.modules.get("pegaprox.utils.realtime")
        previous_attr = getattr(utils, "realtime", None)
        realtime = types.ModuleType("pegaprox.utils.realtime")
        realtime.broadcast_sse = lambda *args, **kwargs: calls.append((args, kwargs))
        sys.modules["pegaprox.utils.realtime"] = realtime
        utils.realtime = realtime
        try:
            compat.broadcast_progress("task-a", "running", 45, cluster_id="pve-a")
        finally:
            if previous is None:
                sys.modules.pop("pegaprox.utils.realtime", None)
            else:
                sys.modules["pegaprox.utils.realtime"] = previous
            if previous_attr is None:
                try:
                    del utils.realtime
                except AttributeError:
                    pass
            else:
                utils.realtime = previous_attr

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], "storage_assistant_discovery")
        self.assertEqual(
            args[1], {"task_id": "task-a", "state": "running", "progress": 45}
        )
        self.assertEqual(kwargs, {"cluster_id": "pve-a"})

    def test_pbs_progress_is_not_broadcast_as_a_global_sse_event(self):
        calls = []
        utils = sys.modules.setdefault(
            "pegaprox.utils", types.ModuleType("pegaprox.utils")
        )
        previous = sys.modules.get("pegaprox.utils.realtime")
        previous_attr = getattr(utils, "realtime", None)
        realtime = types.ModuleType("pegaprox.utils.realtime")
        realtime.broadcast_sse = lambda *args, **kwargs: calls.append((args, kwargs))
        sys.modules["pegaprox.utils.realtime"] = realtime
        utils.realtime = realtime
        try:
            compat.broadcast_progress("task-b", "running", 45, cluster_id=None)
        finally:
            if previous is None:
                sys.modules.pop("pegaprox.utils.realtime", None)
            else:
                sys.modules["pegaprox.utils.realtime"] = previous
            if previous_attr is None:
                try:
                    del utils.realtime
                except AttributeError:
                    pass
            else:
                utils.realtime = previous_attr

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
