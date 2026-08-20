import os
import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from storage_assistant.store import load, remove, upsert
from storage_assistant.validation import ValidationError


def nfs(index):
    return {
        "type": "pve_nfs", "name": f"nas-tech-{index}", "location": "hq",
        "host": "10.0.0.10", "cluster_id": "pve", "export": f"/tech/{index}"
    }


class StoreTests(unittest.TestCase):
    def test_concurrent_upserts_do_not_lose_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "resources.json")
            threads = [threading.Thread(target=upsert, args=(path, nfs(index))) for index in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(load(path)["resources"]), 12)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_upsert_enforces_cross_resource_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "resources.json")
            one = upsert(path, {
                "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
                "host": "10.0.0.20", "pbs_id": "pbs01",
                "target_iqn": "iqn.2026-08.example:pbs.one", "lun": 0,
                "wwid": "3600deadbeef", "filesystem": "xfs", "datastore": "backup-one"
            })["resources"][0]
            with self.assertRaisesRegex(ValidationError, "registry.wwid_duplicate"):
                upsert(path, {
                    "type": "pbs_iscsi", "name": "pbs-nas-b", "location": "dr",
                    "host": "10.1.0.20", "pbs_id": "pbs02",
                    "target_iqn": "iqn.2026-08.example:pbs.two", "lun": 1,
                    "wwid": "3600deadbeef", "filesystem": "xfs", "datastore": "backup-two"
                })
            self.assertEqual(len(load(path)["resources"]), 1)
            self.assertEqual(len(remove(path, one["id"])["resources"]), 0)
