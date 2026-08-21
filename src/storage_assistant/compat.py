"""Narrow compatibility boundary for PegaProx-managed connections.

Only this module may touch PegaProx connection helpers or private SSH/API seams.
Discovery code receives already-authorized manager objects and never reads global
manager dictionaries directly.
"""

from __future__ import annotations

import json
from urllib.parse import quote


class CompatibilityError(RuntimeError):
    """Controlled error that is safe to return as a translation key."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def check_pve_access(cluster_id):
    """Run PegaProx's object-level PVE access gate."""
    from pegaprox.api.helpers import check_cluster_access

    allowed, error = check_cluster_access(cluster_id)
    return None if allowed else error


def resolve_pve(cluster_id):
    """Authorize first, then resolve a connected PVE manager."""
    if (error := check_pve_access(cluster_id)):
        return None, error
    from pegaprox.api.helpers import get_connected_manager

    return get_connected_manager(cluster_id)


def check_pbs_access_only(pbs_id):
    """Run PegaProx's object-level PBS access gate."""
    from pegaprox.api.helpers import check_pbs_access

    allowed, error = check_pbs_access(pbs_id)
    return None if allowed else error


def resolve_pbs(pbs_id):
    """Authorize first, then resolve a connected PBS manager.

    The order is intentional: callers must never index ``pbs_managers`` before
    ``check_pbs_access`` has accepted the request.
    """
    if (error := check_pbs_access_only(pbs_id)):
        return None, error

    from flask import jsonify
    from pegaprox.globals import pbs_managers

    manager = pbs_managers.get(pbs_id)
    if manager is None:
        return None, (jsonify({"error": "discovery.pbs_not_found"}), 404)
    if not bool(getattr(manager, "connected", False)):
        return None, (jsonify({"error": "discovery.pbs_offline"}), 503)
    return manager, None


def _response_data(response):
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        if response.get("error"):
            raise CompatibilityError("discovery.remote_api_failed")
        return response.get("data", response)
    status = int(getattr(response, "status_code", 500))
    if status < 200 or status >= 300:
        raise CompatibilityError("discovery.remote_api_failed")
    try:
        body = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CompatibilityError("discovery.remote_response_invalid") from exc
    if not isinstance(body, dict):
        raise CompatibilityError("discovery.remote_response_invalid")
    return body.get("data")


def pve_get(manager, path, params=None):
    """Read a PVE API endpoint through the smallest available seam."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise CompatibilityError("discovery.compatibility_error")

    try:
        public = getattr(manager, "api_get", None)
        if callable(public):
            try:
                return _response_data(public(path, params=params or {}))
            except TypeError:
                return _response_data(public(path))

        private = getattr(manager, "_api_get", None)
        if not callable(private):
            raise CompatibilityError("discovery.pve_api_unavailable")
        host = str(getattr(manager, "host", "")).strip()
        port = int(getattr(manager, "api_port", 8006))
        if not host:
            raise CompatibilityError("discovery.pve_api_unavailable")
        url_builder = getattr(manager, "_get_api_url", None)
        if callable(url_builder):
            url = url_builder(path)
        else:
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            url = f"https://{host}:{port}/api2/json{path}"
        return _response_data(private(url, params=params or {}))
    except CompatibilityError:
        raise
    except Exception as exc:
        # The manager may surface requests/urllib3 exceptions directly. Keep
        # transport details out of task responses and let discovery classify
        # the affected node as a controlled scan failure.
        raise CompatibilityError("discovery.remote_api_failed") from exc


def pve_nodes(manager):
    return pve_get(manager, "/nodes")


def pve_storage(manager):
    return pve_get(manager, "/storage")


def pve_scan_nfs(manager, node, server):
    node = quote(str(node), safe="")
    return pve_get(manager, f"/nodes/{node}/scan/nfs", {"server": server})


def pbs_disks(manager):
    method = getattr(manager, "get_disks", None)
    if not callable(method):
        raise CompatibilityError("discovery.pbs_disks_api_unavailable")
    return _response_data(method())


def pbs_datastores(manager):
    method = getattr(manager, "get_datastores", None)
    if not callable(method):
        raise CompatibilityError("discovery.pbs_datastores_api_unavailable")
    return _response_data(method())


_READ_ONLY_COMMANDS = {
    "iscsi_sessions": "LC_ALL=C iscsiadm -m session -P 3 2>/dev/null",
    "block_devices": (
        "LC_ALL=C lsblk --bytes --json "
        "-o NAME,KNAME,PKNAME,PATH,TYPE,SIZE,FSTYPE,UUID,WWN,SERIAL,MOUNTPOINTS 2>/dev/null"
    ),
    "multipath_maps": (
        "LC_ALL=C multipathd show maps raw format '%w|%d|%N|%t' 2>/dev/null"
    ),
}


def pbs_read_only_command(manager, command_name, timeout=20, output_limit=1024 * 1024):
    """Execute one hard-coded, read-only inspection command through PBS SSH.

    No resource value is interpolated into the command. Missing private SSH
    support is reported as a controlled compatibility condition.
    """
    command = _READ_ONLY_COMMANDS.get(command_name)
    if command is None:
        raise CompatibilityError("discovery.command_not_allowed")

    connector = getattr(manager, "ssh_connect", None)
    if not callable(connector):
        connector = getattr(manager, "_ssh_connect", None)
    if not callable(connector):
        raise CompatibilityError("discovery.pbs_ssh_unavailable")

    client = None
    try:
        connected = connector()
        if isinstance(connected, tuple):
            client, error = connected
        else:
            client, error = connected, None
        if client is None:
            raise CompatibilityError("discovery.pbs_ssh_unavailable") from None
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read(output_limit + 1)
        err = stderr.read(4097)
        if len(out) > output_limit or len(err) > 4096:
            raise CompatibilityError("discovery.remote_output_too_large")
        channel = getattr(stdout, "channel", None)
        status = channel.recv_exit_status() if channel is not None else 0
        return {
            "status": int(status),
            "stdout": out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out),
        }
    except CompatibilityError:
        raise
    except Exception as exc:
        raise CompatibilityError("discovery.pbs_ssh_failed") from exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def broadcast_progress(task_id, state, progress, cluster_id=None):
    """Publish minimal progress only when PegaProx can scope it to a PVE cluster.

    PegaProx 1.0.2 has no PBS- or user-scoped SSE target. Passing ``None`` to
    ``broadcast_sse`` would make the event global, so PBS deliberately remains
    on the owner-scoped REST polling fallback.
    """
    if not cluster_id:
        return
    try:
        from pegaprox.utils.realtime import broadcast_sse

        broadcast_sse(
            "storage_assistant_discovery",
            {"task_id": task_id, "state": state, "progress": int(progress)},
            cluster_id=cluster_id,
        )
    except Exception:
        pass
