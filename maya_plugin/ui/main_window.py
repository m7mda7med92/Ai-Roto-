"""Main toolkit widget: top bar, sidebar, module workspace, right and bottom panels.

This widget is host-agnostic: :mod:`maya_plugin.ui.workspace` embeds it in a
Maya workspace control, and the standalone preview embeds it in a plain window.
"""
from __future__ import annotations

import webbrowser
from typing import Any, Dict, Optional

from maya_plugin.core.logger import get_logger
from maya_plugin.modules.base import ErrorPanel
from maya_plugin.modules.registry import MODULES, get_module
from maya_plugin.ui.bridge import UiBridge
from maya_plugin.ui.components.sidebar import Sidebar
from maya_plugin.ui.components.top_bar import TopBar
from maya_plugin.ui.dialogs.settings_dialog import SettingsDialog
from maya_plugin.ui.panels.bottom_panel import BottomPanel
from maya_plugin.ui.panels.right_panel import RightPanel
from maya_plugin.ui.qt import QtCore, QtWidgets, exec_dialog
from maya_plugin.ui.styles import stylesheet
from shared.constants.engine_constants import ConnectionState
from shared.paths import toolkit_root
from shared.version import TOOLKIT_NAME, TOOLKIT_VERSION

log = get_logger("ui")

# name -> (right panel visible, bottom panel visible, module to show or None)
WORKSPACE_PRESETS = {
    "Default": (True, True, None),
    "Tracking": (True, True, "camera_tracking"),
    "Roto & Matte": (True, False, "roto_studio"),
    "Pipeline": (False, True, "pipeline_manager"),
    "Minimal": (False, False, None),
}


class ToolkitMainWidget(QtWidgets.QWidget):
    def __init__(self, app: Any, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.app = app
        self.setObjectName("MAITRoot")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)  # paint the QSS background on a plain QWidget
        self.setStyleSheet(stylesheet())
        self.setMinimumSize(900, 560)
        self.bridge = UiBridge(app.events, self)
        self.panels: Dict[str, QtWidgets.QWidget] = {}
        self.current_module: Optional[str] = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.top_bar = TopBar()
        root.addWidget(self.top_bar)

        self.sidebar = Sidebar(MODULES)
        self.stack = QtWidgets.QStackedWidget()
        self.right_panel = RightPanel(app, self.bridge)
        self.bottom_panel = BottomPanel(app, self.bridge)

        self.h_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.h_split.addWidget(self.sidebar)
        self.h_split.addWidget(self.stack)
        self.h_split.addWidget(self.right_panel)
        self.h_split.setStretchFactor(1, 1)
        self.h_split.setSizes([190, 900, 280])
        for index in range(3):
            self.h_split.setCollapsible(index, False)  # hide/show via the top bar toggles instead
        self.v_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.v_split.addWidget(self.h_split)
        self.v_split.addWidget(self.bottom_panel)
        self.v_split.setStretchFactor(0, 1)
        self.v_split.setSizes([620, 240])
        self.v_split.setCollapsible(0, False)
        self.v_split.setCollapsible(1, False)
        self._sized = False
        root.addWidget(self.v_split, 1)

        self._wire()
        self._refresh_project()
        self.on_connection_changed(app.connection.status)
        self.show_module(app.config.get("ui.default_module", "project_manager"))
        # isVisible() is False until the window is shown, so drive both from config.
        right_visible = bool(app.config.get("ui.right_panel_visible", True))
        bottom_visible = bool(app.config.get("ui.bottom_panel_visible", True))
        self.right_panel.setVisible(right_visible)
        self.bottom_panel.setVisible(bottom_visible)
        for toggle, visible in ((self.top_bar.right_toggle, right_visible),
                                (self.top_bar.bottom_toggle, bottom_visible)):
            toggle.blockSignals(True)
            toggle.setChecked(visible)
            toggle.blockSignals(False)
        if app.config.get("engine.auto_connect_on_open", True) and not app.connection.is_connected:
            app.connection.connect()  # background; quietly stays offline if no engine is running

    def showEvent(self, event: Any) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._sized:  # apply proportional splitter sizes once the real size is known
            self._sized = True
            width, height = max(self.width(), 900), max(self.height(), 560)
            self.h_split.setSizes([190, max(400, width - 190 - 300), 300])
            self.v_split.setSizes([max(300, height - 300), 250])

    # ------------------------------------------------------------------ wiring
    def _wire(self) -> None:
        tb, conn, jobs = self.top_bar, self.app.connection, self.app.jobs
        self.sidebar.module_selected.connect(self.show_module)
        tb.start_engine_requested.connect(conn.start_engine)
        tb.stop_engine_requested.connect(conn.stop_engine)
        tb.restart_engine_requested.connect(conn.restart_engine)
        tb.connect_requested.connect(conn.connect)
        tb.disconnect_requested.connect(conn.disconnect)
        tb.health_check_requested.connect(conn.health_check)
        tb.test_processing_requested.connect(self.bottom_panel.submit_test_job)
        tb.toggle_right_panel.connect(lambda v: self._set_panel("right", v))
        tb.toggle_bottom_panel.connect(lambda v: self._set_panel("bottom", v))
        tb.workspace_changed.connect(self.apply_workspace)
        tb.settings_requested.connect(self.open_settings)
        tb.save_requested.connect(self.save)
        tb.help_requested.connect(self.open_help)
        tb.about_requested.connect(self.show_about)
        tb.new_project_requested.connect(lambda: self._project_action("create_project"))
        tb.open_project_requested.connect(lambda: self._project_action("open_project"))
        tb.recent_project_requested.connect(self._open_recent)
        tb.close_project_requested.connect(self.app.projects.close_project)
        self.bridge.connection_changed.connect(self.on_connection_changed)
        self.bridge.project_changed.connect(lambda _p: self._refresh_project())
        _ = jobs  # job actions are wired inside the panels

    # ------------------------------------------------------------------ modules
    def show_module(self, module_id: str) -> None:
        descriptor = get_module(module_id) or MODULES[0]
        panel = self.panels.get(descriptor.module_id)
        if panel is None:
            try:
                panel = descriptor.load_panel_class()(descriptor, self.app, self.bridge)
            except Exception as exc:
                log.exception("Module failed to load: %s" % descriptor.module_id)
                panel = ErrorPanel(descriptor.title, str(exc))
            self.panels[descriptor.module_id] = panel
            self.stack.addWidget(panel)
        self.stack.setCurrentWidget(panel)
        self.current_module = descriptor.module_id
        self.sidebar.set_current(descriptor.module_id)
        self.right_panel.show_module(descriptor, panel)
        if hasattr(panel, "on_activated"):
            panel.on_activated()

    def panel(self, module_id: str) -> Optional[QtWidgets.QWidget]:
        return self.panels.get(module_id)

    # ------------------------------------------------------------------ slots
    def on_connection_changed(self, status: Any) -> None:
        state = status.state
        self.top_bar.status.set_state(state, status.message)
        busy = state in (ConnectionState.STARTING, ConnectionState.STOPPING)
        self.top_bar.set_engine_actions(connected=state == ConnectionState.CONNECTED, busy=busy,
                                        configured=state != ConnectionState.NOT_CONFIGURED,
                                        owns=self.app.connection.owns_engine)

    def _set_panel(self, which: str, visible: bool) -> None:
        widget = self.right_panel if which == "right" else self.bottom_panel
        widget.setVisible(visible)
        if self.app.config.get("ui.%s_panel_visible" % which) != visible:
            self.app.config.set("ui.%s_panel_visible" % which, visible)

    def apply_workspace(self, name: str) -> None:
        right, bottom, module = WORKSPACE_PRESETS.get(name, WORKSPACE_PRESETS["Default"])
        self.top_bar.right_toggle.setChecked(right)
        self.top_bar.bottom_toggle.setChecked(bottom)
        if module:
            self.show_module(module)

    def _refresh_project(self) -> None:
        project = self.app.projects.current
        self.top_bar.set_project_name(project.name if project else "")
        self.top_bar.set_recent_projects(self.app.projects.recent_projects())

    def _project_action(self, method: str) -> None:
        self.show_module("project_manager")
        getattr(self.panels["project_manager"], method)()

    def _open_recent(self, path: str) -> None:
        self.show_module("project_manager")
        self.panels["project_manager"]._open(path)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.app.config, self)
        if exec_dialog(dialog) == QtWidgets.QDialog.Accepted:
            dialog.apply()
            self.app.connection.refresh_configuration()
            log.info("Settings saved")

    def save(self) -> None:
        """Save toolkit state: settings + project manifest. Never saves or touches the Maya scene."""
        self.app.config.save()
        if self.app.projects.current is not None:
            panel = self.panels.get("project_manager")
            if panel is not None:
                panel.save_project()
            else:
                self.app.projects.save_current()
        log.info("Toolkit settings%s saved" % (" and project" if self.app.projects.current else ""))

    def open_help(self) -> None:
        docs = toolkit_root() / "docs" / "INSTALLATION.md"
        webbrowser.open(docs.as_uri())

    def show_about(self) -> None:
        env = self.app.environment
        QtWidgets.QMessageBox.about(self, "About", "<b>%s</b> %s<br><br>Maya: %s<br>Python: %s<br>Qt: %s %s" % (
            TOOLKIT_NAME, TOOLKIT_VERSION, env.maya_version or "n/a", env.python_version,
            env.qt_binding or "?", env.qt_version or ""))

    def cleanup(self) -> None:
        """Detach from the event bus. The engine keeps running; services stay alive for the session."""
        self.bridge.disconnect_events()
