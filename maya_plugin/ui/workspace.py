"""Hosting the toolkit inside Maya as a dockable workspace control.

Uses Maya's ``MayaQWidgetDockableMixin`` (workspaceControl-based docking,
available since Maya 2017). Only UI commands are used here (workspaceControl,
deleteUI); nothing touches scene nodes.
"""
from __future__ import annotations

from typing import Any, Optional

from maya_plugin.core.logger import get_logger
from maya_plugin.ui.main_window import ToolkitMainWidget
from maya_plugin.ui.qt import QtCore, QtWidgets, is_valid, wrap_instance
from shared.version import TOOLKIT_NAME, TOOLKIT_VERSION

log = get_logger("workspace")

WINDOW_OBJECT_NAME = "MayaAIToolkit"
WORKSPACE_CONTROL_NAME = WINDOW_OBJECT_NAME + "WorkspaceControl"

_window: Optional[QtWidgets.QWidget] = None


def maya_main_window() -> Optional[QtWidgets.QWidget]:
    try:
        from maya import OpenMayaUI as omui  # type: ignore
    except ImportError:
        return None
    ptr = omui.MQtUtil.mainWindow()
    return wrap_instance(ptr, QtWidgets.QWidget) if ptr else None


def _dockable_mixin() -> Any:
    try:
        from maya.app.general.mayaMixin import MayaQWidgetDockableMixin  # type: ignore
        return MayaQWidgetDockableMixin
    except ImportError:
        return None


def _delete_existing_control() -> None:
    """Remove a previous instance of *our own* workspace control (UI only)."""
    try:
        from maya import cmds  # type: ignore
    except ImportError:
        return
    if cmds.workspaceControl(WORKSPACE_CONTROL_NAME, query=True, exists=True):
        cmds.workspaceControl(WORKSPACE_CONTROL_NAME, edit=True, close=True)
        cmds.deleteUI(WORKSPACE_CONTROL_NAME, control=True)


def _build_window_class() -> type:
    mixin = _dockable_mixin()
    bases = (mixin, QtWidgets.QWidget) if mixin else (QtWidgets.QWidget,)

    class ToolkitWindow(*bases):  # type: ignore[misc]
        """Thin host: a (dockable) container around :class:`ToolkitMainWidget`."""

        def __init__(self, app: Any, parent: Optional[QtWidgets.QWidget] = None) -> None:
            super().__init__(parent=parent)
            self.setObjectName(WINDOW_OBJECT_NAME)
            self.setWindowTitle("%s %s" % (TOOLKIT_NAME, TOOLKIT_VERSION))
            self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self.main = ToolkitMainWidget(app, self)
            layout.addWidget(self.main)

        def closeEvent(self, event: Any) -> None:  # noqa: N802
            self.main.cleanup()
            super().closeEvent(event)

        # Called by MayaQWidgetDockableMixin when the workspace control closes.
        def dockCloseEventTriggered(self) -> None:  # noqa: N802
            self.main.cleanup()

    return ToolkitWindow


def show_toolkit(app: Any) -> QtWidgets.QWidget:
    """Create (or re-create) and show the toolkit window. Returns the window."""
    global _window
    close_toolkit()
    _delete_existing_control()
    window_cls = _build_window_class()
    width = int(app.config.get("ui.window_width", 1500))
    height = int(app.config.get("ui.window_height", 900))
    if _dockable_mixin() is not None:
        window = window_cls(app)
        # retain=False: the control is rebuilt from the shelf each time, avoiding stale
        # workspace restores when Maya starts without the toolkit loaded.
        window.show(dockable=True, floating=True, retain=False, width=width, height=height)
    else:
        window = window_cls(app, parent=maya_main_window())
        window.setWindowFlags(window.windowFlags() | QtCore.Qt.Window)
        window.resize(width, height)
        window.show()
    _window = window
    log.info("Toolkit UI opened")
    return window


def close_toolkit() -> None:
    global _window
    if _window is not None and is_valid(_window):
        try:
            _window.main.cleanup()
            _window.close()
        except RuntimeError:
            pass
    _window = None


def current_window() -> Optional[QtWidgets.QWidget]:
    return _window if _window is not None and is_valid(_window) else None
