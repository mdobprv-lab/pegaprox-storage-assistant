"""Read-only PVE/NFS and PBS/iSCSI discovery.

The module never logs in to targets, mounts devices, writes signatures, formats a
filesystem or registers storage. It only inspects state already visible through
the PegaProx-managed connection.
"""

from __future__ import annotations

import json
import re

from . import compat


class DiscoveryCancelled(RuntimeError):
    pass


def _cancelled(cancel):
    if cancel():
        raise DiscoveryCancelled("discovery.cancelled")


def _list(value):
    return value if isinstance(value, list) else []


def _export_path(value):
    value = str(value or "").replace("\\", "/")
    value = re.sub(r"/+", "/", "/" + value.lstrip("/"))
    return value.rstrip("/") or "/"


def _identity(value):
    value = re.sub(r"\s+", "", str(value or "")).lower()
    return value[2:] if value.startswith("0x") else value


def discover_pve_nfs(resource, manager, progress, cancel):
    """Inspect cluster nodes, configured storage and NFS export visibility."""
    progress("discovery.phase.pve_nodes", 8)
    nodes_raw = _list(compat.pve_nodes(manager))
    _cancelled(cancel)

    known_nodes = {
        str(item.get("node")): item
        for item in nodes_raw
        if isinstance(item, dict) and item.get("node")
    }
    selected = resource.get("nodes") or sorted(known_nodes)

    progress("discovery.phase.pve_storage", 20)
    storage_raw = _list(compat.pve_storage(manager))
    expected_export = _export_path(resource["export"])
    configured = []
    for item in storage_raw:
        if not isinstance(item, dict) or item.get("type") != "nfs":
            continue
        if str(item.get("server") or "").lower() != resource["host"].lower():
            continue
        if _export_path(item.get("export")) != expected_export:
            continue
        configured.append(str(item.get("storage") or ""))

    results = []
    count = max(len(selected), 1)
    for index, node in enumerate(selected):
        _cancelled(cancel)
        node_info = known_nodes.get(node)
        if node_info is None:
            results.append({"node": node, "state": "missing", "export_visible": False})
            continue
        online = str(node_info.get("status") or "").lower() == "online"
        if not online:
            results.append({"node": node, "state": "offline", "export_visible": False})
            continue
        progress("discovery.phase.nfs_exports", 25 + round(65 * index / count))
        try:
            exports = _list(compat.pve_scan_nfs(manager, node, resource["host"]))
            visible = any(
                isinstance(item, dict) and _export_path(item.get("path")) == expected_export
                for item in exports
            )
            results.append({
                "node": node,
                "state": "online",
                "export_visible": visible,
                "exports_seen": len(exports),
            })
        except compat.CompatibilityError as exc:
            results.append({
                "node": node,
                "state": "scan_failed",
                "export_visible": False,
                "error": exc.code,
            })

    _cancelled(cancel)
    visible = sum(bool(item.get("export_visible")) for item in results)
    failures = sum(item["state"] != "online" for item in results)
    status = "matches" if results and visible == len(results) else "attention"
    if not results or failures == len(results):
        status = "partial"
    progress("discovery.phase.complete", 100)
    return {
        "kind": "pve_nfs",
        "read_only": True,
        "status": status,
        "expected": {
            "host": resource["host"],
            "export": expected_export,
            "cluster_id": resource["cluster_id"],
        },
        "summary": {
            "selected_nodes": len(selected),
            "visible_on_nodes": visible,
            "configured_storage": bool(configured),
            "configured_storage_ids": sorted(item for item in configured if item),
        },
        "nodes": results,
        "limitations": [
            "discovery.limit.read_only",
            "discovery.limit.nfs_no_mount",
            "discovery.limit.nfs_no_write_test",
        ],
    }


def parse_iscsi_sessions(text):
    """Parse the stable identity fields from ``iscsiadm -m session -P 3``."""
    sessions = []
    current = None
    current_lun = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        target = re.match(r"Target:\s*(\S+)", line, re.I)
        if target:
            current = {"target_iqn": target.group(1).lower(), "portals": [], "luns": []}
            sessions.append(current)
            current_lun = None
            continue
        if current is None:
            continue
        portal = re.match(r"(?:Current|Persistent) Portal:\s*([^,\s]+)", line, re.I)
        if portal:
            value = portal.group(1)
            if value not in current["portals"]:
                current["portals"].append(value)
            continue
        lun = re.match(r"Lun:\s*(\d+)", line, re.I)
        if lun:
            current_lun = {"lun": int(lun.group(1)), "device": ""}
            current["luns"].append(current_lun)
            continue
        disk = re.search(r"Attached scsi disk\s+(\S+)", line, re.I)
        if disk and current_lun is not None:
            current_lun["device"] = disk.group(1)
    return sessions


def parse_lsblk(text):
    try:
        body = json.loads(text or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    flattened = []

    def visit(item):
        if not isinstance(item, dict):
            return
        copy = {key: value for key, value in item.items() if key != "children"}
        mountpoints = copy.get("mountpoints")
        if mountpoints is None and copy.get("mountpoint") is not None:
            mountpoints = [copy.get("mountpoint")]
        copy["mountpoints"] = [str(x) for x in (mountpoints or []) if x]
        flattened.append(copy)
        for child in item.get("children") or []:
            visit(child)

    for device in body.get("blockdevices") or []:
        visit(device)
    return flattened


def parse_multipath_maps(text):
    maps = []
    for raw in str(text or "").splitlines():
        parts = raw.strip().split("|", 3)
        if len(parts) != 4 or not parts[0]:
            continue
        maps.append({
            "wwid": _identity(parts[0]),
            "device": parts[1],
            "paths": parts[2],
            "state": parts[3],
        })
    return maps


def _optional_command(manager, name, parser):
    try:
        result = compat.pbs_read_only_command(manager, name)
        if result["status"] != 0 and not (
            name == "iscsi_sessions" and result["status"] == 21
        ):
            return [], "discovery.command_unavailable"
        return parser(result["stdout"]), None
    except compat.CompatibilityError as exc:
        return [], exc.code


def discover_pbs_iscsi(resource, manager, progress, cancel):
    """Inspect PBS API and already-established iSCSI state without changing it."""
    progress("discovery.phase.pbs_disks", 10)
    disks = _list(compat.pbs_disks(manager))
    _cancelled(cancel)

    progress("discovery.phase.pbs_datastores", 25)
    datastores = _list(compat.pbs_datastores(manager))
    _cancelled(cancel)

    progress("discovery.phase.iscsi_sessions", 45)
    sessions, session_error = _optional_command(manager, "iscsi_sessions", parse_iscsi_sessions)
    _cancelled(cancel)
    progress("discovery.phase.block_devices", 62)
    block_devices, block_error = _optional_command(manager, "block_devices", parse_lsblk)
    _cancelled(cancel)
    progress("discovery.phase.multipath", 78)
    maps, multipath_error = _optional_command(manager, "multipath_maps", parse_multipath_maps)

    target = resource["target_iqn"].lower()
    expected_wwid = _identity(resource.get("wwid"))
    matching_sessions = [item for item in sessions if item.get("target_iqn") == target]
    matching_luns = [
        lun
        for session in matching_sessions
        for lun in session.get("luns", [])
        if lun.get("lun") == resource["lun"]
    ]
    attached_names = {str(item.get("device") or "") for item in matching_luns}

    matching_maps = [item for item in maps if expected_wwid and item["wwid"] == expected_wwid]
    map_names = {str(item.get("device") or "") for item in matching_maps}
    matching_blocks = []
    for item in block_devices:
        names = {
            str(item.get("name") or ""),
            str(item.get("kname") or ""),
            str(item.get("path") or "").removeprefix("/dev/"),
        }
        identities = {_identity(item.get("wwn")), _identity(item.get("serial"))}
        parent = str(item.get("pkname") or "")
        if ((attached_names & names) or parent in attached_names or (map_names & names)
                or (expected_wwid and expected_wwid in identities)):
            matching_blocks.append({
                "name": str(item.get("name") or item.get("kname") or ""),
                "path": str(item.get("path") or ""),
                "type": str(item.get("type") or ""),
                "size": (int(item.get("size")) if str(item.get("size") or "").isdigit() else 0),
                "filesystem": str(item.get("fstype") or ""),
                "wwid_match": bool(expected_wwid and expected_wwid in identities),
                "mounted": bool(item.get("mountpoints")),
                "mountpoints": item.get("mountpoints") or [],
            })

    datastore = next(
        (
            item for item in datastores
            if isinstance(item, dict)
            and str(item.get("store") or item.get("name") or "") == resource["datastore"]
        ),
        None,
    )
    disk_api_match = any(
        isinstance(item, dict)
        and expected_wwid
        and expected_wwid in {
            _identity(item.get("wwn")),
            _identity(item.get("serial")),
            _identity(item.get("wwid")),
        }
        for item in disks
    )
    filesystem_match = any(
        item["filesystem"] == resource["filesystem"] for item in matching_blocks
    )
    wwid_match = any(item["wwid_match"] for item in matching_blocks) or bool(matching_maps) or disk_api_match
    observations = [session_error, block_error, multipath_error]
    status = "matches" if matching_luns and (not expected_wwid or wwid_match) else "attention"
    if all(observations):
        status = "partial"

    _cancelled(cancel)
    progress("discovery.phase.complete", 100)
    return {
        "kind": "pbs_iscsi",
        "read_only": True,
        "status": status,
        "expected": {
            "pbs_id": resource["pbs_id"],
            "target_iqn": target,
            "lun": resource["lun"],
            "wwid": expected_wwid,
            "filesystem": resource["filesystem"],
            "datastore": resource["datastore"],
        },
        "summary": {
            "target_session_found": bool(matching_sessions),
            "lun_found": bool(matching_luns),
            "wwid_match": wwid_match,
            "filesystem_match": filesystem_match,
            "datastore_registered": datastore is not None,
            "multipath_map_found": bool(matching_maps),
        },
        "devices": matching_blocks,
        "datastore": ({
            "name": str(datastore.get("store") or datastore.get("name") or ""),
            "path": str(datastore.get("path") or ""),
        } if datastore else None),
        "capabilities": {
            "iscsi_sessions": session_error is None,
            "block_devices": block_error is None,
            "multipath_maps": multipath_error is None,
        },
        "capability_errors": [item for item in observations if item],
        "limitations": [
            "discovery.limit.read_only",
            "discovery.limit.iscsi_no_discovery",
            "discovery.limit.iscsi_no_login",
            "discovery.limit.iscsi_no_mount",
            "discovery.limit.iscsi_no_format",
            "discovery.limit.iscsi_no_registration",
        ],
    }


def discover(resource, manager, progress, cancel):
    if resource["type"] == "pve_nfs":
        return discover_pve_nfs(resource, manager, progress, cancel)
    if resource["type"] == "pbs_iscsi":
        return discover_pbs_iscsi(resource, manager, progress, cancel)
    raise compat.CompatibilityError("type.invalid")
