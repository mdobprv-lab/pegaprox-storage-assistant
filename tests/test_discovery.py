import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from storage_assistant import discovery


def progress(_phase, _value):
    pass


def not_cancelled():
    return False


class DiscoveryTests(unittest.TestCase):
    def test_pve_scan_matches_normalized_export_on_each_node(self):
        resource = {
            "type": "pve_nfs", "host": "192.0.2.10", "export": "/tech",
            "cluster_id": "pve-a", "nodes": [],
        }
        with (
            patch.object(discovery.compat, "pve_nodes", return_value=[
                {"node": "pve01", "status": "online"},
                {"node": "pve02", "status": "online"},
            ]),
            patch.object(discovery.compat, "pve_storage", return_value=[
                {"type": "nfs", "storage": "nas-tech", "server": "192.0.2.10", "export": "tech/"},
            ]),
            patch.object(discovery.compat, "pve_scan_nfs", return_value=[{"path": "\\tech"}]),
        ):
            result = discovery.discover_pve_nfs(resource, object(), progress, not_cancelled)
        self.assertEqual(result["status"], "matches")
        self.assertEqual(result["summary"]["visible_on_nodes"], 2)
        self.assertEqual(result["summary"]["configured_storage_ids"], ["nas-tech"])
        self.assertTrue(result["read_only"])

    def test_pve_scan_reports_missing_selected_node(self):
        resource = {
            "type": "pve_nfs", "host": "192.0.2.10", "export": "/tech",
            "cluster_id": "pve-a", "nodes": ["pve99"],
        }
        with (
            patch.object(discovery.compat, "pve_nodes", return_value=[]),
            patch.object(discovery.compat, "pve_storage", return_value=[]),
        ):
            result = discovery.discover_pve_nfs(resource, object(), progress, not_cancelled)
        self.assertEqual(result["nodes"][0]["state"], "missing")
        self.assertEqual(result["status"], "partial")

    def test_iscsi_session_parser_extracts_target_lun_and_device(self):
        parsed = discovery.parse_iscsi_sessions("""
Target: iqn.2026-08.invalid:pbs.test
    Current Portal: 192.0.2.20:3260,1
        Lun: 0
            Attached scsi disk sdb State: running
""")
        self.assertEqual(parsed[0]["target_iqn"], "iqn.2026-08.invalid:pbs.test")
        self.assertEqual(parsed[0]["luns"], [{"lun": 0, "device": "sdb"}])

    def test_lsblk_parser_flattens_children_and_mounts(self):
        parsed = discovery.parse_lsblk(json.dumps({"blockdevices": [{
            "name": "sdb", "children": [{
                "name": "sdb1", "fstype": "xfs", "mountpoints": ["/mnt/datastore"]
            }]
        }]}))
        self.assertEqual([item["name"] for item in parsed], ["sdb", "sdb1"])
        self.assertEqual(parsed[1]["mountpoints"], ["/mnt/datastore"])

    def test_pbs_discovery_matches_existing_session_and_wwid(self):
        resource = {
            "type": "pbs_iscsi", "pbs_id": "pbs-a",
            "target_iqn": "iqn.2026-08.invalid:pbs.test", "lun": 0,
            "wwid": "36000000000000000000000000000001", "filesystem": "xfs",
            "datastore": "backup-a",
        }
        outputs = {
            "iscsi_sessions": {"status": 0, "stdout": """
Target: iqn.2026-08.invalid:pbs.test
    Current Portal: 192.0.2.20:3260,1
        Lun: 0
            Attached scsi disk sdb State: running
"""},
            "block_devices": {"status": 0, "stdout": json.dumps({"blockdevices": [{
                "name": "sdb", "kname": "sdb", "path": "/dev/sdb", "type": "disk",
                "size": 1000, "fstype": "xfs", "wwn": "0x36000000000000000000000000000001",
                "serial": "", "mountpoints": ["/mnt/datastore/backup-a"],
            }]})},
            "multipath_maps": {"status": 0, "stdout": "36000000000000000000000000000001|dm-2|2|active\n"},
        }
        with (
            patch.object(discovery.compat, "pbs_disks", return_value=[]),
            patch.object(discovery.compat, "pbs_datastores", return_value=[
                {"store": "backup-a", "path": "/mnt/datastore/backup-a"},
            ]),
            patch.object(
                discovery.compat,
                "pbs_read_only_command",
                side_effect=lambda _manager, name: outputs[name],
            ),
        ):
            result = discovery.discover_pbs_iscsi(resource, object(), progress, not_cancelled)
        self.assertEqual(result["status"], "matches")
        self.assertTrue(result["summary"]["lun_found"])
        self.assertTrue(result["summary"]["wwid_match"])
        self.assertTrue(result["summary"]["datastore_registered"])
        self.assertTrue(result["read_only"])

    def test_pbs_missing_ssh_is_partial_but_api_observations_survive(self):
        resource = {
            "type": "pbs_iscsi", "pbs_id": "pbs-a", "target_iqn": "iqn.2026-08.invalid:test",
            "lun": 1, "wwid": "3600000000000001", "filesystem": "xfs", "datastore": "backup-a",
        }
        with (
            patch.object(discovery.compat, "pbs_disks", return_value=[]),
            patch.object(discovery.compat, "pbs_datastores", return_value=[]),
            patch.object(
                discovery.compat,
                "pbs_read_only_command",
                side_effect=discovery.compat.CompatibilityError("discovery.pbs_ssh_unavailable"),
            ),
        ):
            result = discovery.discover_pbs_iscsi(resource, object(), progress, not_cancelled)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["capability_errors"]), 3)


if __name__ == "__main__":
    unittest.main()
