"""PegaProx route handlers. Remote mutations are intentionally absent in v0.1."""

from __future__ import annotations

import json
import os
import uuid

from flask import jsonify, request, send_file

from . import __version__
from .store import create, load, remove, update
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


def init(plugin_dir):
    global PLUGIN_DIR, REGISTRY_PATH
    PLUGIN_DIR = plugin_dir
    from pegaprox.constants import CONFIG_DIR
    REGISTRY_PATH = os.path.join(CONFIG_DIR, PLUGIN_ID, "resources.json")


def _current_user():
    from pegaprox.utils.auth import load_users
    users = load_users()
    return users.get(request.session.get("user"), {})


def _has(perm):
    from pegaprox.utils.rbac import has_permission
    return bool(has_permission(_current_user(), perm))


def _require(perm):
    if not _has(perm):
        return jsonify({"error": "permission.denied", "required": perm}), 403
    return None


def _audit(action, details):
    from pegaprox.utils.audit import log_audit
    log_audit(user=request.session.get("user", "system"), action=action, details=details)


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
    if (error := _require("storage.view")):
        return error
    return jsonify(_settings())


def status():
    if (error := _require("storage.view")):
        return error
    resources = load(REGISTRY_PATH)["resources"]
    return jsonify({
        "plugin": PLUGIN_ID,
        "version": __version__,
        "phase": "foundation",
        "execution_enabled": False,
        "resources": len(resources),
        "pve_nfs": sum(x["type"] == "pve_nfs" for x in resources),
        "pbs_iscsi": sum(x["type"] == "pbs_iscsi" for x in resources),
        "can_manage": _has("plugins.manage"),
    })


def resources():
    if request.method == "GET":
        if (error := _require("storage.view")):
            return error
        return jsonify(load(REGISTRY_PATH))
    if request.method == "DELETE":
        if (error := _require("plugins.manage")):
            return error
        resource_id = str(request.args.get("id") or "").strip()
        try:
            resource_id = str(uuid.UUID(resource_id))
            result = remove(REGISTRY_PATH, resource_id)
            _audit("storage_assistant.resource_deleted", f"resource_id={resource_id}")
            return jsonify(result)
        except (ValidationError, ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
    if (error := _require("plugins.manage")):
        return error
    body = request.get_json(silent=True)
    try:
        resource = normalize_resource(body)
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
    if (error := _require("storage.view")):
        return error
    try:
        return jsonify(deployment_plan(request.get_json(silent=True)))
    except (ValidationError, ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


ROUTES = {
    "ui": ui,
    "locale": locale,
    "settings": settings,
    "status": status,
    "resources": resources,
    "plan": plan,
}
