"""Atomic JSON registry with no secret fields."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager

import fcntl

from .validation import ValidationError, normalize_resource, validate_registry

_LOCK = threading.RLock()
EMPTY = {"schema": 1, "resources": []}


@contextmanager
def _file_lock(path, exclusive):
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    lock_path = path + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load_unlocked(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"schema": 1, "resources": []}
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("resources"), list):
        raise ValueError("registry.invalid")
    return {"schema": 1, "resources": validate_registry(data["resources"])}


def _save_unlocked(path, data):
    normalized = {"schema": 1, "resources": validate_registry(data.get("resources", []))}
    parent = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".resources-", suffix=".json", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        dir_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return normalized


def load(path):
    with _LOCK, _file_lock(path, exclusive=False):
        return _load_unlocked(path)


def save(path, data):
    with _LOCK, _file_lock(path, exclusive=True):
        return _save_unlocked(path, data)


def create(path, raw_resource):
    resource = normalize_resource(raw_resource)
    with _LOCK, _file_lock(path, exclusive=True):
        registry = _load_unlocked(path)
        if any(item["id"] == resource["id"] for item in registry["resources"]):
            raise ValidationError("resource.already_exists")
        registry["resources"].append(resource)
        return _save_unlocked(path, registry)


def update(path, raw_resource):
    resource = normalize_resource(raw_resource)
    with _LOCK, _file_lock(path, exclusive=True):
        registry = _load_unlocked(path)
        existing = next(
            (item for item in registry["resources"] if item["id"] == resource["id"]),
            None,
        )
        if existing is None:
            raise ValidationError("resource.not_found")
        if existing["type"] != resource["type"]:
            raise ValidationError("resource.type_change")
        registry["resources"] = [
            resource if item["id"] == resource["id"] else item
            for item in registry["resources"]
        ]
        return _save_unlocked(path, registry)


def remove(path, resource_id):
    with _LOCK, _file_lock(path, exclusive=True):
        registry = _load_unlocked(path)
        remaining = [item for item in registry["resources"] if item["id"] != resource_id]
        if len(remaining) == len(registry["resources"]):
            raise ValidationError("resource.not_found")
        return _save_unlocked(path, {"schema": 1, "resources": remaining})
