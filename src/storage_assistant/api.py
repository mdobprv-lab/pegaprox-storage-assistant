"""PegaProx route handlers. Remote mutations are intentionally absent."""

from __future__ import annotations

import json
import os
import uuid

from flask import jsonify, request, send_file

from . import __version__
from . import compat
from .discovery import discover
from .store import create, load, remove, update
from .tasks import TaskError, TaskRegistry
from .validation import ValidationError, deployment_plan, normalize_resource

PLUGIN_ID = "storage-assistant"
PLUGIN_DIR = None
REGISTRY_PATH = None
DEFAULT_SETTINGS = {
    "default_language": "auto",
    "theme_override": "auto",
}
ALLOWED_LANGUAGES = {"auto", "en", "pl"}
ALLOWED_THEMES = {
    "auto",
    "modern-dark",
    "corporate-dark",
    "corporate-light",
    "cloud-dark",
    "cloud-light",
}
TASKS = TaskRegistry()


def init(plugin_dir):
    global PLUGIN_DIR, REGISTRY_PATH
    PLUGIN_DIR = plugin_dir
    from pegaprox.constants import CONFIG_DIR
    REGISTRY_PATH = os.path.join(CONFIG_DIR, PLUGIN_ID, "resources.json")


def _current_user():
    from pegaprox.utils.auth import build_authz_user

    username = request.session.get("user", "")
    return build_authz_user(username, request.session)


def _has(perm, user=None):
    from pegaprox.utils.rbac import has_permission
    return bool(has_permission(user or _current_user(), perm))


def _require(perm):
    if not _has(perm):
        return jsonify({"error": "permission.denied", "required": perm}), 403
    return None


def _require_many(permissions):
    for permission in permissions:
        if (error := _require(permission)):
            return error
    return None


def _has_many(permissions, user=None):
    user = user or _current_user()
    return all(_has(permission, user) for permission in permissions)


def _require_plugin_view():
    user = _current_user()
    if _has("storage.view", user) or _has_many(
        _discovery_permissions("pbs_iscsi"), user
    ):
        return None
    return jsonify({
        "error": "permission.denied",
        "required_any": ["storage.view", list(_discovery_permissions("pbs_iscsi"))],
    }), 403


def _audit(action, details):
    from pegaprox.utils.audit import log_audit
    log_audit(user=request.session.get("user", "system"), action=action, details=details)


def _audit_as(username, action, details):
    try:
        from pegaprox.utils.audit import log_audit

        log_audit(user=username or "system", action=action, details=details)
    except Exception:
        pass


def _authorize_resource(resource):
    if resource["type"] == "pve_nfs":
        return compat.check_pve_access(resource["cluster_id"])
    return compat.check_pbs_access_only(resource["pbs_id"])


def _visible_resources(resources):
    visible = []
    user = _current_user()
    for resource in resources:
        try:
            if not _has_many(_discovery_permissions(resource["type"]), user):
                continue
            if _authorize_resource(resource) is None:
                visible.append(resource)
        except Exception:
            continue
    return visible


def _find_resource(resource_id):
    return next(
        (item for item in load(REGISTRY_PATH)["resources"] if item["id"] == resource_id),
        None,
    )


def _discovery_permissions(resource_type):
    if resource_type == "pve_nfs":
        return ("storage.view",)
    return ("pbs.view", "pbs.disks.view", "pbs.datastore.view")


def _settings():
    """Return only supported, non-sensitive settings with safe fallbacks."""
    values = dict(DEFAULT_SETTINGS)
    try:
        with open(os.path.join(PLUGIN_DIR, "config.json"), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            if raw.get("default_language") in ALLOWED_LANGUAGES:
                values["default_language"] = raw["default_language"]
            if raw.get("theme_override") in ALLOWED_THEMES:
                values["theme_override"] = raw["theme_override"]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return values


def ui():
    return send_file(os.path.join(PLUGIN_DIR, "src", "ui", "plugin.html"), mimetype="text/html")


def locale():
    lang = str(request.args.get("lang") or "en").lower().split("-")[0]
    if lang not in {"en", "pl"}:
        lang = "en"
    path = os.path.join(PLUGIN_DIR, "locales", f"{lang}.json")
    with open(path, "r", encoding="utf-8") as handle:
        return jsonify(json.load(handle))


def settings():
    if (error := _require_plugin_view()):
        return error
    return jsonify(_settings())


def status():
    if (error := _require_plugin_view()):
        return error
    user = _current_user()
    can_discover_pve = _has_many(_discovery_permissions("pve_nfs"), user)
    can_discover_pbs = _has_many(_discovery_permissions("pbs_iscsi"), user)
    resources = _visible_resources(load(REGISTRY_PATH)["resources"])
    return jsonify({
        "plugin": PLUGIN_ID,
        "version": __version__,
        "phase": "read_only_discovery",
        "discovery_enabled": True,
        "execution_enabled": False,
        "resources": len(resources),
        "pve_nfs": sum(x["type"] == "pve_nfs" for x in resources),
        "pbs_iscsi": sum(x["type"] == "pbs_iscsi" for x in resources),
        "can_manage": _has("plugins.manage", user),
        "can_discover_pve": can_discover_pve,
        "can_discover_pbs": can_discover_pbs,
    })


def resources():
    if request.method == "GET":
        if (error := _require_plugin_view()):
            return error
        registry = load(REGISTRY_PATH)
        registry["resources"] = _visible_resources(registry["resources"])
        return jsonify(registry)
    if request.method == "DELETE":
        if (error := _require("plugins.manage")):
            return error
        resource_id = str(request.args.get("id") or "").strip()
        try:
            resource_id = str(uuid.UUID(resource_id))
            existing = _find_resource(resource_id)
            if existing is None:
                raise ValidationError("resource.not_found")
            if (error := _require_many(_discovery_permissions(existing["type"]))):
                return error
            if (error := _authorize_resource(existing)):
                return error
            result = remove(REGISTRY_PATH, resource_id)
            _audit("storage_assistant.resource_deleted", f"resource_id={resource_id}")
            return jsonify(result)
        except (ValidationError, ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
    if request.method not in {"POST", "PUT"}:
        return jsonify({"error": "request.method_not_allowed"}), 405
    if (error := _require("plugins.manage")):
        return error
    body = request.get_json(silent=True)
    try:
        resource = normalize_resource(body)
        if (error := _require_many(_discovery_permissions(resource["type"]))):
            return error
        if request.method == "PUT":
            existing = _find_resource(resource["id"])
            if existing is None:
                raise ValidationError("resource.not_found")
            if (error := _authorize_resource(existing)):
                return error
        if (error := _authorize_resource(resource)):
            return error
        if request.method == "PUT":
            result = update(REGISTRY_PATH, resource)
            action = "storage_assistant.resource_updated"
        else:
            result = create(REGISTRY_PATH, resource)
            action = "storage_assistant.resource_created"
        _audit(action,
               f"resource_id={resource['id']} type={resource['type']} name={resource['name']}")
        return jsonify(result)
    except (ValidationError, ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


def plan():
    try:
        result = deployment_plan(request.get_json(silent=True))
        if (error := _require_many(_discovery_permissions(result["resource"]["type"]))):
            return error
        if (error := _authorize_resource(result["resource"])):
            return error
        return jsonify(result)
    except (ValidationError, ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


def _task_response(task_id):
    username = request.session.get("user", "")
    task = TASKS.get(task_id, username)
    if task is None:
        return None, (jsonify({"error": "discovery.task_not_found"}), 404)
    if (error := _require_many(_discovery_permissions(task["resource_type"]))):
        return None, error
    target = {
        "type": task["resource_type"],
        ("cluster_id" if task["resource_type"] == "pve_nfs" else "pbs_id"): task["target_id"],
    }
    if (error := _authorize_resource(target)):
        return None, error
    return task, None


def discovery():
    """Start, inspect or cancel one read-only discovery task."""
    username = request.session.get("user", "")
    if request.method in {"GET", "DELETE"}:
        task_id = str(request.args.get("id") or "").strip()
        try:
            task_id = str(uuid.UUID(task_id))
        except (TypeError, ValueError):
            return jsonify({"error": "discovery.task_not_found"}), 404
        task, error = _task_response(task_id)
        if error:
            return error
        if request.method == "DELETE":
            task = TASKS.cancel(task_id, username)
            _audit("storage_assistant.discovery_cancel_requested", f"task_id={task_id}")
        return jsonify(task)

    if request.method != "POST":
        return jsonify({"error": "request.method_not_allowed"}), 405
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "resource.invalid"}), 400
    try:
        resource_id = str(uuid.UUID(str(body.get("resource_id") or "")))
    except (TypeError, ValueError):
        return jsonify({"error": "resource.not_found"}), 404
    resource = _find_resource(resource_id)
    if resource is None:
        return jsonify({"error": "resource.not_found"}), 404
    if (error := _require_many(_discovery_permissions(resource["type"]))):
        return error

    if resource["type"] == "pve_nfs":
        manager, error = compat.resolve_pve(resource["cluster_id"])
        cluster_id = resource["cluster_id"]
    else:
        manager, error = compat.resolve_pbs(resource["pbs_id"])
        cluster_id = None
    if error:
        return error

    def runner(progress, cancelled):
        return discover(resource, manager, progress, cancelled)

    def notify(task):
        compat.broadcast_progress(
            task["id"], task["state"], task["progress"], cluster_id=cluster_id
        )

    def finished(state, task_error):
        details = f"resource_id={resource_id} type={resource['type']} state={state}"
        if task_error:
            details += f" error={task_error}"
        _audit_as(username, "storage_assistant.discovery_finished", details)

    try:
        task = TASKS.start(username, resource, runner, notify=notify, finished=finished)
    except TaskError as exc:
        return jsonify({"error": str(exc)}), 429
    _audit(
        "storage_assistant.discovery_started",
        f"task_id={task['id']} resource_id={resource_id} type={resource['type']}",
    )
    return jsonify(task), 202


ROUTES = {
    "ui": ui,
    "locale": locale,
    "settings": settings,
    "status": status,
    "resources": resources,
    "plan": plan,
    "discovery": discovery,
}
