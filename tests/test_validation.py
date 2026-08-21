import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))

from storage_assistant.validation import (
    ValidationError,
    deployment_plan,
    normalize_resource,
    validate_registry,
)


class ValidationTests(unittest.TestCase):
    def test_pve_nfs_defaults_are_technical_content_only(self):
        resource = normalize_resource({
            "type": "pve_nfs", "name": "nas-tech", "location": "hq",
            "host": "nas.example.lan", "cluster_id": "pve-prod", "export": "/pve/tech"
        })
        self.assertEqual(resource["content"], ["import", "iso", "snippets", "vztmpl"])
        self.assertNotIn("images", resource["content"])
        plan = deployment_plan(resource)
        self.assertFalse(plan["destructive"])
        self.assertEqual(plan["resource"], resource)

    def test_normalizes_user_friendly_nfs_export_paths(self):
        base = {
            "type": "pve_nfs", "name": "nas-tech", "location": "hq",
            "host": "nas.example.lan", "cluster_id": "pve-prod",
        }
        for entered in (
                "test/technical", "technical", r"\test\technical",
                r"test\technical", "//test//technical/"):
            resource = normalize_resource({**base, "export": entered})
            expected = "/technical" if entered == "technical" else "/test/technical"
            self.assertEqual(resource["export"], expected)

    def test_rejects_pve_content_outside_allowlist(self):
        with self.assertRaisesRegex(ValidationError, "content.invalid"):
            normalize_resource({
                "type": "pve_nfs", "name": "nas-tech", "location": "hq",
                "host": "10.0.0.10", "cluster_id": "pve", "export": "/tech",
                "content": ["iso", "images"]
            })

    def test_rejects_empty_content_and_non_list_nodes(self):
        base = {
            "type": "pve_nfs", "name": "nas-tech", "location": "hq",
            "host": "10.0.0.10", "cluster_id": "pve", "export": "/tech"
        }
        with self.assertRaisesRegex(ValidationError, "content.invalid"):
            normalize_resource({**base, "content": []})
        with self.assertRaisesRegex(ValidationError, "nodes.invalid"):
            normalize_resource({**base, "nodes": "pve01,pve02"})

    def test_rejects_explicit_zero_iscsi_port(self):
        with self.assertRaisesRegex(ValidationError, "port.invalid"):
            normalize_resource({
                "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
                "host": "10.0.0.20", "pbs_id": "pbs01", "port": 0,
                "target_iqn": "iqn.2026-08.example:pbs.backup", "lun": 0,
                "filesystem": "xfs", "datastore": "nas-backup"
            })

    def test_rejects_non_integer_port_and_lun_with_validation_errors(self):
        base = {
            "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
            "host": "10.0.0.20", "pbs_id": "pbs01",
            "target_iqn": "iqn.2026-08.example:pbs.backup",
            "filesystem": "xfs", "datastore": "nas-backup"
        }
        with self.assertRaisesRegex(ValidationError, "port.invalid"):
            normalize_resource({**base, "port": "not-a-port"})
        with self.assertRaisesRegex(ValidationError, "lun.invalid"):
            normalize_resource({**base, "lun": 1.5})

    def test_rejects_ambiguous_json_types(self):
        nfs = {
            "type": "pve_nfs", "name": "nas-tech", "location": "hq",
            "host": "10.0.0.10", "cluster_id": "pve", "export": "/tech"
        }
        with self.assertRaisesRegex(ValidationError, "content.invalid"):
            normalize_resource({**nfs, "content": [["iso"]]})
        with self.assertRaisesRegex(ValidationError, "nodes.invalid"):
            normalize_resource({**nfs, "nodes": [123]})
        with self.assertRaisesRegex(ValidationError, "enabled.invalid"):
            normalize_resource({**nfs, "enabled": "false"})

        iscsi = {
            "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
            "host": "10.0.0.20", "pbs_id": "pbs01",
            "target_iqn": "iqn.2026-08.example:pbs.backup", "lun": 0,
            "filesystem": "xfs", "datastore": "nas-backup"
        }
        with self.assertRaisesRegex(ValidationError, "portals.invalid"):
            normalize_resource({**iscsi, "portals": [3260]})
        with self.assertRaisesRegex(ValidationError, "multipath.invalid"):
            normalize_resource({**iscsi, "multipath": "false"})

    def test_pbs_iscsi_is_single_pbs_scope(self):
        resource = normalize_resource({
            "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
            "host": "10.0.0.20", "pbs_id": "pbs01",
            "target_iqn": "iqn.2026-08.example:pbs.backup", "lun": 1,
            "filesystem": "xfs", "datastore": "nas-backup"
        })
        plan = deployment_plan(resource)
        self.assertEqual(plan["scope"], "single_pbs")
        self.assertIn("wwid", plan["checks"])
        self.assertFalse(plan["destructive"])
        self.assertFalse(plan["ready"])

    def test_accepts_and_preserves_real_pbs_datastore_id(self):
        resource = normalize_resource({
            "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
            "host": "10.0.0.20", "pbs_id": "pbs01",
            "target_iqn": "iqn.2026-08.example:pbs.backup", "lun": 0,
            "filesystem": "xfs", "datastore": "PBS-MAIN"
        })
        self.assertEqual(resource["datastore"], "PBS-MAIN")

    def test_rejects_unsafe_pbs_datastore_ids(self):
        base = {
            "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
            "host": "10.0.0.20", "pbs_id": "pbs01",
            "target_iqn": "iqn.2026-08.example:pbs.backup", "lun": 0,
            "filesystem": "xfs",
        }
        for datastore in ("PBS MAIN", "../PBS-MAIN", "/mnt/datastore/pbs-main"):
            with self.subTest(datastore=datastore):
                with self.assertRaisesRegex(ValidationError, "datastore.invalid"):
                    normalize_resource({**base, "datastore": datastore})

    def test_formatting_is_destructive_even_with_known_wwid(self):
        resource = normalize_resource({
            "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
            "host": "10.0.0.20", "pbs_id": "pbs01",
            "target_iqn": "iqn.2026-08.example:pbs.backup", "lun": 1,
            "wwid": "3600deadbeef", "filesystem": "xfs",
            "filesystem_mode": "create", "datastore": "nas-backup"
        })
        plan = deployment_plan(resource)
        self.assertTrue(plan["destructive"])
        self.assertTrue(plan["ready"])
        self.assertIn("filesystem.create", plan["actions"])

    def test_multipath_requires_wwid_and_additional_portal(self):
        base = {
            "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
            "host": "10.0.0.20", "pbs_id": "pbs01", "multipath": True,
            "target_iqn": "iqn.2026-08.example:pbs.backup", "lun": 1,
            "filesystem": "xfs", "datastore": "nas-backup"
        }
        with self.assertRaisesRegex(ValidationError, "multipath.wwid_required"):
            normalize_resource(base)
        base["wwid"] = "3600deadbeef"
        with self.assertRaisesRegex(ValidationError, "multipath.portals_required"):
            normalize_resource(base)
        base["portals"] = ["10.0.1.20", "[2001:db8::20]:3261"]
        resource = normalize_resource(base)
        self.assertEqual(resource["portals"], ["10.0.1.20:3260", "[2001:db8::20]:3261"])

    def test_accepts_standard_iscsi_name_types(self):
        for target in ("eui.0123456789ABCDEF", "naa.0123456789abcdef"):
            resource = normalize_resource({
                "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
                "host": "nas.example.lan", "pbs_id": "pbs01", "target_iqn": target,
                "lun": 0, "filesystem": "ext4", "datastore": "nas-backup"
            })
            self.assertEqual(resource["target_iqn"], target.lower())

    def test_registry_rejects_duplicate_wwid(self):
        one = normalize_resource({
            "type": "pbs_iscsi", "name": "pbs-nas-a", "location": "hq",
            "host": "10.0.0.20", "pbs_id": "pbs01",
            "target_iqn": "iqn.2026-08.example:pbs.one", "lun": 0,
            "wwid": "3600deadbeef", "filesystem": "xfs", "datastore": "backup-one"
        })
        two = normalize_resource({
            "type": "pbs_iscsi", "name": "pbs-nas-b", "location": "dr",
            "host": "10.1.0.20", "pbs_id": "pbs02",
            "target_iqn": "iqn.2026-08.example:pbs.two", "lun": 1,
            "wwid": "3600deadbeef", "filesystem": "xfs", "datastore": "backup-two"
        })
        with self.assertRaisesRegex(ValidationError, "registry.wwid_duplicate"):
            validate_registry([one, two])

    def test_registry_treats_another_portal_as_the_same_target_lun(self):
        base = {
            "type": "pbs_iscsi", "location": "hq", "pbs_id": "pbs01",
            "target_iqn": "iqn.2026-08.example:pbs.shared", "lun": 7,
            "filesystem": "xfs"
        }
        one = normalize_resource({
            **base, "name": "pbs-path-a", "host": "10.0.0.20",
            "datastore": "backup-a"
        })
        two = normalize_resource({
            **base, "name": "pbs-path-b", "host": "10.0.1.20",
            "datastore": "backup-b"
        })
        with self.assertRaisesRegex(ValidationError, "registry.iscsi_lun_duplicate"):
            validate_registry([one, two])

    def test_registry_rejects_duplicate_destination_names(self):
        first = normalize_resource({
            "type": "pve_nfs", "name": "nas-tech", "location": "hq",
            "host": "10.0.0.10", "cluster_id": "pve", "export": "/tech"
        })
        second = normalize_resource({
            "type": "pve_nfs", "name": "nas-tech", "location": "dr",
            "host": "10.1.0.10", "cluster_id": "pve", "export": "/other"
        })
        with self.assertRaisesRegex(ValidationError, "registry.pve_name_duplicate"):
            validate_registry([first, second])

    def test_rejects_invalid_hostname_labels(self):
        with self.assertRaisesRegex(ValidationError, "host.invalid"):
            normalize_resource({
                "type": "pve_nfs", "name": "nas-tech", "location": "hq",
                "host": "nas..example", "cluster_id": "pve", "export": "/tech"
            })

    def test_rejects_traversal_in_nfs_export(self):
        for entered in ("/safe/../etc", r"safe\..\etc", "safe/./etc"):
            with self.assertRaisesRegex(ValidationError, "export.invalid"):
                normalize_resource({
                    "type": "pve_nfs", "name": "nas-tech", "location": "hq",
                    "host": "10.0.0.10", "cluster_id": "pve", "export": entered
                })


if __name__ == "__main__":
    unittest.main()
