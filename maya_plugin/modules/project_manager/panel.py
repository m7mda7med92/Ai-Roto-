"""Project Manager panel: create/open projects, recent projects, settings, shots, paths."""
from __future__ import annotations

from typing import Optional

from maya_plugin.core.project_service import Project, ProjectError
from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.widgets import Card, accent_button, grid
from maya_plugin.ui.dialogs.project_dialog import NewProjectDialog
from maya_plugin.ui.qt import QtWidgets, exec_dialog


class ProjectManagerPanel(BaseModulePanel):
    phase_label = ""

    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        actions = Card("Project")
        row = QtWidgets.QHBoxLayout()
        self.create_button = accent_button("Create Project...")
        self.open_button = QtWidgets.QPushButton("Open Project...")
        self.save_button = QtWidgets.QPushButton("Save Project")
        self.save_button.setToolTip("Writes the toolkit project manifest. Does not save the Maya scene.")
        for button in (self.create_button, self.open_button, self.save_button):
            row.addWidget(button)
        row.addStretch(1)
        actions.add_layout(row)
        self.current_label = QtWidgets.QLabel("No project open.")
        self.current_label.setObjectName("Dim")
        actions.add(self.current_label)
        layout.addWidget(actions)

        recent = Card("Recent Projects")
        self.recent_list = QtWidgets.QListWidget()
        self.recent_list.setMaximumHeight(140)
        recent.add(self.recent_list)
        settings = Card("Project Settings")
        self.fps_combo = QtWidgets.QComboBox()
        self.fps_combo.addItems(["23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60"])
        self.fps_combo.setCurrentText("24")
        self.res_w = QtWidgets.QSpinBox()
        self.res_w.setRange(16, 16384)
        self.res_w.setValue(1920)
        self.res_h = QtWidgets.QSpinBox()
        self.res_h.setRange(16, 16384)
        self.res_h.setValue(1080)
        self.colorspace = QtWidgets.QComboBox()
        self.colorspace.addItems(["ACEScg", "sRGB", "Linear Rec.709", "ACES2065-1"])
        settings.add_form([("Frame rate", self.fps_combo), ("Width", self.res_w), ("Height", self.res_h),
                           ("Working colorspace", self.colorspace)])
        layout.addLayout(grid([recent, settings]))

        shots = Card("Shot Browser")
        self.shot_list = QtWidgets.QListWidget()
        self.shot_list.setMaximumHeight(160)
        refresh = QtWidgets.QPushButton("Refresh Shots")
        refresh.clicked.connect(self.refresh)
        shots.add(self.shot_list)
        shots.add(refresh)
        paths = Card("Project Paths")
        self.paths_table = QtWidgets.QTableWidget(0, 2)
        self.paths_table.setHorizontalHeaderLabels(["Key", "Path"])
        self.paths_table.horizontalHeader().setStretchLastSection(True)
        self.paths_table.verticalHeader().setVisible(False)
        self.paths_table.setMaximumHeight(200)
        paths.add(self.paths_table)
        layout.addLayout(grid([shots, paths]))

        self.create_button.clicked.connect(self.create_project)
        self.open_button.clicked.connect(self.open_project)
        self.save_button.clicked.connect(self.save_project)
        self.recent_list.itemDoubleClicked.connect(lambda item: self._open(item.text()))
        self.bridge.project_changed.connect(lambda _p: self.refresh())
        self.refresh()

    # ------------------------------------------------------------------ actions (file system only)
    def create_project(self) -> None:
        default_parent = self.app.config.get("projects.default_root", "")
        dialog = NewProjectDialog(default_parent, self)
        if exec_dialog(dialog) != QtWidgets.QDialog.Accepted:
            return
        name, parent_dir = dialog.values()
        try:
            self._apply_settings(self.app.projects.create_project(parent_dir, name))
        except ProjectError as exc:
            QtWidgets.QMessageBox.warning(self, "Create Project", str(exc))

    def open_project(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Open Toolkit Project")
        if path:
            self._open(path)

    def _open(self, path: str) -> None:
        try:
            self.app.projects.open_project(path)
        except ProjectError as exc:
            QtWidgets.QMessageBox.warning(self, "Open Project", str(exc))

    def save_project(self) -> None:
        project = self.app.projects.current
        if project is None:
            return
        self._apply_settings(project)
        try:
            self.app.projects.save_current()
        except ProjectError as exc:
            QtWidgets.QMessageBox.warning(self, "Save Project", str(exc))

    def _apply_settings(self, project: Project) -> None:
        project.settings.update({"fps": self.fps_combo.currentText(), "width": self.res_w.value(),
                                 "height": self.res_h.value(), "colorspace": self.colorspace.currentText()})
        self.app.projects.save_current()

    def refresh(self) -> None:
        self.recent_list.clear()
        self.recent_list.addItems(self.app.projects.recent_projects())
        project: Optional[Project] = self.app.projects.current
        self.save_button.setEnabled(project is not None)
        self.shot_list.clear()
        self.paths_table.setRowCount(0)
        if project is None:
            self.current_label.setText("No project open.")
            return
        self.current_label.setText("%s  -  %s" % (project.name, project.root))
        shots = project.shots()
        self.shot_list.addItems(shots or ["(no shots - add folders under 'shots/')"])
        for key, rel in sorted(project.paths.items()):
            row = self.paths_table.rowCount()
            self.paths_table.insertRow(row)
            self.paths_table.setItem(row, 0, QtWidgets.QTableWidgetItem(key))
            self.paths_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(project.root_path / rel)))
        s = project.settings
        if s:
            self.fps_combo.setCurrentText(str(s.get("fps", "24")))
            self.res_w.setValue(int(s.get("width", 1920)))
            self.res_h.setValue(int(s.get("height", 1080)))
            self.colorspace.setCurrentText(str(s.get("colorspace", "ACEScg")))
