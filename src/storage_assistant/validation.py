"""Pure validation and normalization; safe to import outside PegaProx."""

from __future__ import annotations

import ipaddress
import re
import uuid

RESOURCE_TYPES = {"pve_nfs", "pbs_iscsi"}
NFS_CONTENT = {"iso", "vztmpl", "snippets", "import"}
FILESYSTEMS = {"ext4", "xfs"}
FILESYSTEM_MODES = {"reuse", "create"}
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_IQN_RE = re.compile(r"^iqn\.\d{4}-(0[1-9]|1[0-2])\.[a-z0-9.-]+(?::[^\s\x00-\x1f\x7f]+)?$")
_EUI_RE = re.compile(r"^eui\.[0-9a-fA-F]{16}$")
_NAA_RE = re.compile(r"^naa\.[0-9a-fA-F]{16}(?:[0-9a-fA-F]{16})?$")


class ValidationError(ValueError):
    pass


def _text(value, field, maximum=255):
    value = str(value or "").strip()
    if not value:
        raise ValidationError(f"{field}.required")
    if len(value) > maximum:
        raise ValidationError(f"{field}.too_long")
    if any(ord(char) < 0x20 or ord(char) == 0x7f for char in value):
        raise ValidationError(f"{field}.invalid")
    return value


def _integer(value, field):
    if isinstance(value, bool):
        raise ValidationError(f"{field}.invalid")
    if isinstance(value, float) and not value.is_integer():
        raise ValidationError(f"{field}.invalid")
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field}.invalid") from exc


def _boolean(value, field):
    if not isinstance(value, bool):
        raise ValidationError(f"{field}.invalid")
    return value


def _host(value):
    value = _text(value, "host")
    try:
        return str(ipaddress.ip_address(value.strip("[]")))
    except ValueError:
        labels = value.split(".")
        if (len(value) > 253 or not labels or
                any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in labels)):
            raise ValidationError("host.invalid")
        return value.lower()


def _target_name(value):
    value = _text(value, "target_iqn", 223)
    if not (_IQN_RE.fullmatch(value) or _EUI_RE.fullmatch(value) or _NAA_RE.fullmatch(value)):
        raise ValidationError("target_iqn.invalid")
    return value.lower()


def _portal(value, default_port=3260):
    value = _text(value, "portals")
    host, port = value, default_port
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise ValidationError("portals.invalid")
        host = value[1:end]
        suffix = value[end + 1:]
        if suffix:
            if not suffix.startswith(":"):
                raise ValidationError("portals.invalid")
            try:
                port = int(suffix[1:])
            except ValueError as exc:
                raise ValidationError("portals.invalid") from exc
    elif value.count(":") == 1:
        candidate_host, candidate_port = value.rsplit(":", 1)
        if candidate_port.isdigit():
            host, port = candidate_host, int(candidate_port)
    elif value.count(":") > 1:
        host = value
    try:
        host = _host(host)
    except ValidationError as exc:
        raise ValidationError("portals.invalid") from exc
    if port < 1 or port > 65535:
        raise ValidationError("portals.invalid")
    try:
        is_v6 = ipaddress.ip_address(host).version == 6
    except ValueError:
        is_v6 = False
    return f"[{host}]:{port}" if is_v6 else f"{host}:{port}"


def normalize_resource(raw):
    if not isinstance(raw, dict):
        raise ValidationError("resource.invalid")
    kind = _text(raw.get("type"), "type")
    if kind not in RESOURCE_TYPES:
        raise ValidationError("type.invalid")
    resource_id = str(raw.get("id") or uuid.uuid4())
    try:
        resource_id = str(uuid.UUID(resource_id))
    except ValueError as exc:
        raise ValidationError("id.invalid") from exc

    common = {
        "id": resource_id,
        "type": kind,
        "name": _text(raw.get("name"), "name", 64),
        "location": _text(raw.get("location"), "location", 64),
        "host": _host(raw.get("host")),
        "enabled": _boolean(raw.get("enabled", True), "enabled"),
        "description": str(raw.get("description") or "").strip()[:500],
    }
    if not _ID_RE.fullmatch(common["name"]):
        raise ValidationError("name.invalid")

    if kind == "pve_nfs":
        export = _text(raw.get("export"), "export")
        if (not export.startswith("/") or "//" in export or
                any(segment in {".", ".."} for segment in export.split("/"))):
            raise ValidationError("export.invalid")
        version = str(raw.get("nfs_version") or "4.2")
        if version not in {"3", "4", "4.1", "4.2"}:
            raise ValidationError("nfs_version.invalid")
        content = raw.get("content")
        if content is None:
            content = ["iso", "vztmpl", "snippets", "import"]
        if (not isinstance(content, list) or not content or
                any(not isinstance(item, str) or item not in NFS_CONTENT for item in content)):
            raise ValidationError("content.invalid")
        nodes = raw.get("nodes") or []
        if not isinstance(nodes, list) or any(not isinstance(item, str) for item in nodes):
            raise ValidationError("nodes.invalid")
        common.update({
            "cluster_id": _text(raw.get("cluster_id"), "cluster_id", 128),
            "export": export,
            "nfs_version": version,
            "content": sorted(set(content)),
            "nodes": sorted(set(_host(x) for x in nodes if str(x).strip())),
        })
    else:
        raw_port = raw.get("port")
        port = 3260 if raw_port is None or raw_port == "" else _integer(raw_port, "port")
        if port < 1 or port > 65535:
            raise ValidationError("port.invalid")
        iqn = _target_name(raw.get("target_iqn"))
        filesystem = str(raw.get("filesystem") or "xfs")
        if filesystem not in FILESYSTEMS:
            raise ValidationError("filesystem.invalid")
        filesystem_mode = str(raw.get("filesystem_mode") or "reuse")
        if filesystem_mode not in FILESYSTEM_MODES:
            raise ValidationError("filesystem_mode.invalid")
        portals = raw.get("portals") or []
        if not isinstance(portals, list) or any(not isinstance(item, str) for item in portals):
            raise ValidationError("portals.invalid")
        normalized_portals = sorted(set(_portal(x, port) for x in portals if str(x).strip()))
        primary_portal = _portal(f"[{common['host']}]" if ":" in common["host"] else common["host"], port)
        normalized_portals = [item for item in normalized_portals if item != primary_portal]
        wwid = re.sub(r"\s+", "", str(raw.get("wwid") or "")).lower()
        if wwid and not re.fullmatch(r"[a-zA-Z0-9._:-]{8,256}", wwid):
            raise ValidationError("wwid.invalid")
        common.update({
            "pbs_id": _text(raw.get("pbs_id"), "pbs_id", 128),
            "port": port,
            "target_iqn": iqn,
            "lun": _integer(raw.get("lun", 0), "lun"),
            "wwid": wwid,
            "filesystem": filesystem,
            "filesystem_mode": filesystem_mode,
            "datastore": _text(raw.get("datastore"), "datastore", 64),
            "multipath": _boolean(raw.get("multipath", False), "multipath"),
            "portals": normalized_portals,
        })
        if common["lun"] < 0:
            raise ValidationError("lun.invalid")
        if not _ID_RE.fullmatch(common["datastore"]):
            raise ValidationError("datastore.invalid")
        if common["multipath"] and not common["wwid"]:
            raise ValidationError("multipath.wwid_required")
        if common["multipath"] and not common["portals"]:
            raise ValidationError("multipath.portals_required")
    return common


def validate_registry(resources):
    """Enforce ownership and uniqueness invariants across all definitions."""
    normalized = [normalize_resource(item) for item in resources]
    ids = set()
    nfs_names = set()
    nfs_exports = set()
    pbs_datastores = set()
    iscsi_paths = set()
    wwids = set()
    for item in normalized:
        if item["id"] in ids:
            raise ValidationError("registry.id_duplicate")
        ids.add(item["id"])
        if item["type"] == "pve_nfs":
            name_key = (item["cluster_id"], item["name"])
            export_key = (item["cluster_id"], item["host"], item["export"])
            if name_key in nfs_names:
                raise ValidationError("registry.pve_name_duplicate")
            if export_key in nfs_exports:
                raise ValidationError("registry.nfs_export_duplicate")
            nfs_names.add(name_key)
            nfs_exports.add(export_key)
            continue
        datastore_key = (item["pbs_id"], item["datastore"])
        # A target name is globally unique by design. Treat the same target/LUN
        # reached through another portal as the same device, not another resource.
        path_key = (item["target_iqn"], item["lun"])
        if datastore_key in pbs_datastores:
            raise ValidationError("registry.pbs_datastore_duplicate")
        if path_key in iscsi_paths:
            raise ValidationError("registry.iscsi_lun_duplicate")
        if item["wwid"] and item["wwid"] in wwids:
            raise ValidationError("registry.wwid_duplicate")
        pbs_datastores.add(datastore_key)
        iscsi_paths.add(path_key)
        if item["wwid"]:
            wwids.add(item["wwid"])
    return normalized


def deployment_plan(resource):
    """Return an explicit non-executing plan used by the review wizard."""
    resource = normalize_resource(resource)
    if resource["type"] == "pve_nfs":
        return {
            "destructive": False,
            "scope": "pve_cluster",
            "checks": ["dns", "tcp_2049", "exports", "mount_each_node", "write_read"],
            "actions": ["pve.storage.create.nfs"],
            "content": resource["content"],
        }
    create_filesystem = resource["filesystem_mode"] == "create"
    ready = bool(resource["wwid"])
    return {
        "destructive": create_filesystem,
        "ready": ready,
        "scope": "single_pbs",
        "checks": ["dns", "tcp_3260", "iscsi_discovery", "wwid", "signatures", "mount_state"],
        "actions": ["iscsi.login", "multipath.configure" if resource["multipath"] else "path.configure",
                    "filesystem.create" if create_filesystem else "filesystem.verify",
                    "pbs.datastore.create"],
        "confirmation": (["target_iqn", "lun", "wwid", "filesystem", "datastore"]
                         if create_filesystem else []),
        "blockers": [] if ready else ["wwid.required_before_apply"],
    }
