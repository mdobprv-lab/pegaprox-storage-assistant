import pathlib
import sys
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from storage_assistant.tasks import TaskRegistry


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.registry = TaskRegistry(max_workers=1, max_tasks=8, ttl_seconds=60)
        self.resource = {
            "id": "45d1ccb7-8d21-469d-9c8f-479e2415c9ef",
            "type": "pve_nfs",
            "cluster_id": "pve-a",
        }

    def tearDown(self):
        self.registry.shutdown()

    def wait(self, task_id, owner="alice"):
        deadline = time.time() + 2
        while time.time() < deadline:
            task = self.registry.get(task_id, owner)
            if task and task["state"] in {"succeeded", "failed", "cancelled"}:
                return task
            time.sleep(0.01)
        self.fail("task did not finish")

    def test_result_is_visible_only_to_owner(self):
        task = self.registry.start(
            "alice", self.resource,
            lambda progress, cancelled: {"read_only": True},
        )
        result = self.wait(task["id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertTrue(result["result"]["read_only"])
        self.assertIsNone(self.registry.get(task["id"], "bob"))

    def test_background_workers_are_bounded_named_daemon_threads(self):
        self.assertEqual(len(self.registry._workers), 1)
        worker = self.registry._workers[0]
        self.assertTrue(worker.daemon)
        self.assertTrue(worker.is_alive())
        self.assertTrue(worker.name.startswith("storage-assistant-discovery-"))

    def test_running_task_can_be_cancelled_cooperatively(self):
        entered = threading.Event()

        def runner(progress, cancelled):
            entered.set()
            while not cancelled():
                time.sleep(0.005)
            progress("discovery.phase.cancelling", 50)
            return {"unexpected": True}

        task = self.registry.start("alice", self.resource, runner)
        self.assertTrue(entered.wait(1))
        cancelled = self.registry.cancel(task["id"], "alice")
        self.assertIn(cancelled["state"], {"cancel_requested", "cancelled"})
        result = self.wait(task["id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["phase"], "discovery.phase.cancelled")
        self.assertIsNone(result["result"])

    def test_cancel_wins_over_late_transport_error(self):
        entered = threading.Event()
        release = threading.Event()

        def runner(_progress, _cancelled):
            entered.set()
            release.wait(1)
            raise TimeoutError("late transport timeout")

        task = self.registry.start("alice", self.resource, runner)
        self.assertTrue(entered.wait(1))
        requested = self.registry.cancel(task["id"], "alice")
        self.assertEqual(requested["state"], "cancel_requested")
        release.set()
        result = self.wait(task["id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["phase"], "discovery.phase.cancelled")
        self.assertEqual(result["error"], "discovery.cancelled")
        self.assertNotIn("late transport timeout", str(result))


if __name__ == "__main__":
    unittest.main()
