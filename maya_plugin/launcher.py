"""Entry points used by the Maya shelf button and the plugin command.

Shelf button command::

    import maya_plugin.launcher; maya_plugin.launcher.show()

Everything here is defensive: a failure is reported in Maya's script editor and
never propagates into Maya. Opening the UI does not touch the Maya scene and
does not start the engine (unless the user clicks *Start Engine*).

Standalone preview (outside Maya, for UI development)::

    python -m maya_plugin.launcher --standalone
"""
from __future__ import annotations

import sys
import traceback
from typing import Any, Optional


def show() -> Optional[Any]:
    """Open (or re-open) the toolkit UI. Returns the window, or None on failure."""
    try:
        from maya_plugin.core.app_manager import get_app_manager
        from maya_plugin.ui.workspace import show_toolkit

        return show_toolkit(get_app_manager())
    except Exception:
        _report("Maya AI Toolkit failed to open:\n" + traceback.format_exc())
        return None


def close() -> None:
    try:
        from maya_plugin.ui.workspace import close_toolkit
        close_toolkit()
    except Exception:
        _report("Maya AI Toolkit failed to close:\n" + traceback.format_exc())


def shutdown() -> None:
    """Close the UI and stop services (used by plugin unload)."""
    close()
    try:
        from maya_plugin.core.app_manager import reset_app_manager
        reset_app_manager()
    except Exception:
        _report("Maya AI Toolkit shutdown error:\n" + traceback.format_exc())


def reload_toolkit() -> Optional[Any]:
    """Developer helper: shut down, purge toolkit modules from ``sys.modules`` and reopen."""
    shutdown()
    for name in [m for m in sys.modules if m.split(".")[0] in ("maya_plugin", "shared")]:
        del sys.modules[name]
    import maya_plugin.launcher as fresh  # noqa: F811
    return fresh.show()


def _report(message: str) -> None:
    try:
        from maya import cmds  # type: ignore
        cmds.warning(message.splitlines()[0])
        sys.stderr.write(message + "\n")
    except Exception:
        sys.stderr.write(message + "\n")


def _standalone(argv: list) -> int:  # pragma: no cover - interactive
    from maya_plugin.core.app_manager import get_app_manager, reset_app_manager
    from maya_plugin.ui.qt import QtWidgets
    from maya_plugin.ui.workspace import show_toolkit

    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv)
    show_toolkit(get_app_manager())
    code = qt_app.exec_() if hasattr(qt_app, "exec_") else qt_app.exec()
    reset_app_manager()
    return int(code)


if __name__ == "__main__":  # pragma: no cover
    if "--standalone" in sys.argv:
        sys.exit(_standalone(sys.argv))
    print(__doc__)
