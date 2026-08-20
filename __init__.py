"""PegaProx Storage Assistant plugin entry point."""

from __future__ import annotations

import logging
import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PLUGIN_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from pegaprox.api.plugins import register_plugin_route  # noqa: E402
from storage_assistant import api  # noqa: E402

PLUGIN_ID = "storage-assistant"
log = logging.getLogger(f"plugin.{PLUGIN_ID}")


def register(app=None):
    api.init(PLUGIN_DIR)
    for path, handler in api.ROUTES.items():
        register_plugin_route(PLUGIN_ID, path, handler)
    log.info("[%s] registered %d routes", PLUGIN_ID, len(api.ROUTES))
