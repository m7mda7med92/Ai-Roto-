"""Maya AI Production Toolkit - Maya plugin package.

Layering (imports only flow downward):

    ui/            Qt widgets (PySide2 in Maya 2024, PySide6 in 2025+)
    modules/       per-module UI panels, registered in modules/registry.py
    communication/ engine client, connection + job managers (no Qt, no Maya)
    core/          config, logging, events, environment, app manager (no Qt)
    shared/        wire contracts shared with the engine (stdlib only)

Nothing in this package modifies the Maya scene on import, on UI open, or
while talking to the engine. See docs/ARCHITECTURE.md, "Scene safety".
"""
from shared.version import TOOLKIT_NAME, TOOLKIT_VERSION

__all__ = ["TOOLKIT_NAME", "TOOLKIT_VERSION"]
