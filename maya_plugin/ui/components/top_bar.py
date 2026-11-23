"""Top bar: logo, project, workspace selector, engine status/controls, save, settings, help."""
from __future__ import annotations

from typing import List, Optional

from maya_plugin.ui.components.logo import logo_pixmap
from maya_plugin.ui.components.status_indicator import StatusIndicator
from maya_plugin.ui.qt import QAction, QtWidgets, Signal
from shared.version import TOOLKIT_NAME

WORKSPACES = ("Default", "Tracking", "Roto & Matte", "Pipeline", "Minimal")


class TopBar(QtWidgets.QFrame):
    # project
    new_project_requested = Signal()
    open_project_requested = Signal()
    recent_project_requested = Signal(str)
    close_project_requested = Signal()
    # workspace / app
    workspace_changed = Signal(str)
    save_requested = Signal()
    settings_requested = Signal()
    help_requested = Signal()
    about_requested = Signal()
    toggle_right_panel = Signal(bool)
    toggle_bottom_panel = Signal(bool)
    # engine
    start_engine_requested = Signal()
    stop_engine_requested = Signal()
    restart_engine_requested = Signal()
    connect_requested = Signal()
    disconnect_requested = Signal()
    health_check_requested = Signal()
    test_processing_requested = Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MAITTopBar")
        self.setFixedHeight(46)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        logo = QtWidgets.QLabel()
        logo.setPixmap(logo_pixmap(26))
        logo.setStyleSheet("background: transparent;")
        title = QtWidgets.QLabel(TOOLKIT_NAME)
        title.setObjectName("AppTitle")
        self.project_label = QtWidgets.QLabel("No project")
        self.project_label.setObjectName("ProjectName")
        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addSpacing(8)

        # Project menu
        self.project_button = QtWidgets.QToolButton()
        self.project_button.setText("Project")
        self.project_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.project_menu = QtWidgets.QMenu(self.project_button)
        self.recent_menu = QtWidgets.QMenu("Recent Projects", self.project_menu)
        self._add(self.project_menu, "New Project...", self.new_project_requested)
        self._add(self.project_menu, "Open Project...", self.open_project_requested)
        self.project_menu.addMenu(self.recent_menu)
        self.project_menu.addSeparator()
        self._add(self.project_menu, "Close Project", self.close_project_requested)
        self.project_button.setMenu(self.project_menu)
        layout.addWidget(self.project_button)
        layout.addWidget(self.project_label)
        layout.addStretch(1)

        # Workspace selector
        ws_label = QtWidgets.QLabel("Workspace")
        ws_label.setObjectName("Dim")
        self.workspace_combo = QtWidgets.QComboBox()
        self.workspace_combo.addItems(list(WORKSPACES))
        self.workspace_combo.setToolTip("Layout preset (panel visibility and default module)")
        self.workspace_combo.currentTextChanged.connect(self.workspace_changed.emit)
        layout.addWidget(ws_label)
        layout.addWidget(self.workspace_combo)
        layout.addSpacing(10)

        # Engine status + controls
        self.status = StatusIndicator()
        layout.addWidget(self.status)
        self.engine_button = QtWidgets.QToolButton()
        self.engine_button.setText("Engine")
        self.engine_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(self.engine_button)
        self.action_start = self._add(menu, "Start Engine", self.start_engine_requested)
        self.action_stop = self._add(menu, "Stop Engine", self.stop_engine_requested)
        self.action_restart = self._add(menu, "Restart Engine", self.restart_engine_requested)
        menu.addSeparator()
        self.action_connect = self._add(menu, "Connect", self.connect_requested)
        self.action_disconnect = self._add(menu, "Disconnect", self.disconnect_requested)
        self.action_health = self._add(menu, "Health Check", self.health_check_requested)
        menu.addSeparator()
        self.action_test = self._add(menu, "Test Processing", self.test_processing_requested)
        self.engine_button.setMenu(menu)
        layout.addWidget(self.engine_button)

        self.start_button = QtWidgets.QPushButton("Start Engine")
        self.start_button.setProperty("accent", True)
        self.start_button.clicked.connect(self.start_engine_requested.emit)
        layout.addWidget(self.start_button)
        layout.addSpacing(10)

        # Panels / save / settings / help
        self.right_toggle = self._toggle("Props", "Show/hide the right properties panel", self.toggle_right_panel)
        self.bottom_toggle = self._toggle("Jobs", "Show/hide the bottom job/log panel", self.toggle_bottom_panel)
        layout.addWidget(self.right_toggle)
        layout.addWidget(self.bottom_toggle)
        save = QtWidgets.QToolButton()
        save.setText("Save")
        save.setToolTip("Save toolkit project and settings (does not save the Maya scene)")
        save.clicked.connect(self.save_requested.emit)
        settings = QtWidgets.QToolButton()
        settings.setText("Settings")
        settings.clicked.connect(self.settings_requested.emit)
        help_button = QtWidgets.QToolButton()
        help_button.setText("Help")
        help_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        help_menu = QtWidgets.QMenu(help_button)
        self._add(help_menu, "Documentation", self.help_requested)
        self._add(help_menu, "About", self.about_requested)
        help_button.setMenu(help_menu)
        for widget in (save, settings, help_button):
            layout.addWidget(widget)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _add(menu: QtWidgets.QMenu, text: str, signal: Signal) -> QAction:  # type: ignore[valid-type]
        action = QAction(text, menu)
        action.triggered.connect(lambda *_: signal.emit())  # type: ignore[attr-defined]
        menu.addAction(action)
        return action

    @staticmethod
    def _toggle(text: str, tooltip: str, signal: Signal) -> QtWidgets.QToolButton:  # type: ignore[valid-type]
        button = QtWidgets.QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setChecked(True)
        button.setToolTip(tooltip)
        button.toggled.connect(signal.emit)  # type: ignore[attr-defined]
        return button

    def set_project_name(self, name: str) -> None:
        self.project_label.setText(name or "No project")

    def set_recent_projects(self, paths: List[str]) -> None:
        self.recent_menu.clear()
        if not paths:
            empty = self.recent_menu.addAction("(none)")
            empty.setEnabled(False)
            return
        for path in paths:
            action = self.recent_menu.addAction(path)
            action.triggered.connect(lambda *_a, p=path: self.recent_project_requested.emit(p))

    def set_engine_actions(self, connected: bool, busy: bool, configured: bool, owns: bool) -> None:
        self.action_start.setEnabled(configured and not connected and not busy)
        self.start_button.setEnabled(configured and not connected and not busy)
        self.start_button.setText("Engine Running" if connected else ("Working..." if busy else "Start Engine"))
        self.action_stop.setEnabled(connected and not busy)
        self.action_restart.setEnabled(configured and not busy)
        self.action_connect.setEnabled(not connected and not busy)
        self.action_disconnect.setEnabled(connected and not busy)
        self.action_health.setEnabled(connected and not busy)
        self.action_test.setEnabled(connected and not busy)
