"""Model Manager widget: lists the engine's model catalog; install/remove models.

Installs run as engine jobs (progress + cancel also appear in the job table).
Removing a model deletes its downloaded weights from the engine's model store,
so the artist is always asked to confirm first.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from maya_plugin.communication.model_service import model_id_from_label
from maya_plugin.ui.components.widgets import accent_button
from maya_plugin.ui.qt import QtCore, QtWidgets
from shared.constants.engine_constants import ConnectionState, JobState

COLUMNS = ["Model", "Backend", "Download", "VRAM", "Status"]


class ModelManagerWidget(QtWidgets.QWidget):
    def __init__(self, app: Any, bridge: Any, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.app = app
        self._models: List[Any] = []
        self._installs: Dict[str, Any] = {}   # model_id -> latest JobStatus of its install job
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(170)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table)
        self.details = QtWidgets.QLabel("")
        self.details.setObjectName("Dim")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.details)
        row = QtWidgets.QHBoxLayout()
        self.install_button = accent_button("Install", "Download the selected model's weights into the engine")
        self.install_button.clicked.connect(self.install_selected)
        self.remove_button = QtWidgets.QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected)
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: self.app.models.refresh())
        row.addWidget(self.install_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        bridge.models_updated.connect(self.on_models)
        bridge.job_updated.connect(self.on_job)
        bridge.connection_changed.connect(lambda _s: self._update_buttons())
        self.on_models({"models": app.models.models(), "error": ""})

    # ------------------------------------------------------------------ data
    def selected(self) -> Optional[Any]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        return self._models[index] if 0 <= index < len(self._models) else None

    def on_models(self, payload: Any) -> None:
        payload = payload or {}
        selected = self.selected()
        selected_id = selected.model_id if selected else None
        self._models = list(payload.get("models", []))
        self.table.setRowCount(len(self._models))
        for row, info in enumerate(self._models):
            size = "built in" if info.builtin else ("%d MB" % info.approx_size_mb if info.approx_size_mb else "?")
            vram = ("%g GB" % info.recommended_vram_gb) if info.recommended_vram_gb else "CPU"
            values = [info.name, info.backend, size, vram, self._status_text(info)]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(info.description)
                self.table.setItem(row, column, item)
            if info.model_id == selected_id:
                self.table.selectRow(row)
        self.table.resizeColumnsToContents()
        error = payload.get("error")
        if error:
            self.details.setText(error)
        elif not self._models:
            self.details.setText("Connect the engine to see available models.")
        self._update_buttons()

    def _status_text(self, info: Any) -> str:
        job = self._installs.get(info.model_id)
        if job is not None and not job.is_terminal:
            return "Installing %d%%" % int(job.progress * 100)
        if info.builtin:
            return "Ready (built in)"
        if info.installed:
            return "Installed" + ("" if info.verified else " (checksum not published)")
        if job is not None and job.state == JobState.FAILED and job.error:
            return "Install failed"
        return "Not installed"

    def on_job(self, status: Any) -> None:
        if getattr(status, "job_type", "") != "model_install":
            return
        model_id = model_id_from_label(status.label)
        if not model_id:
            return
        self._installs[model_id] = status
        for row, info in enumerate(self._models):
            if info.model_id == model_id and self.table.item(row, 4) is not None:
                self.table.item(row, 4).setText(self._status_text(info))
        if status.state == JobState.FAILED and status.error:
            self.details.setText("Install failed: %s" % status.error.message)
        elif not status.is_terminal and status.message:
            self.details.setText(status.message)
        self._update_buttons()

    # ------------------------------------------------------------------ commands
    def install_selected(self) -> None:
        info = self.selected()
        if info is None or info.builtin or info.installed:
            return
        self.details.setText("Starting download of %s (%s MB). License: %s" % (
            info.name, info.approx_size_mb or "?", info.license or "see model homepage"))
        self.app.models.install(info.model_id)

    def remove_selected(self) -> None:
        info = self.selected()
        if info is None or info.builtin or not info.installed:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Remove model?",
            "Delete the downloaded weights of '%s' from\n%s?\n\nYou can install it again later." % (
                info.name, info.install_path or "the model store"),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if answer == QtWidgets.QMessageBox.Yes:
            self.app.models.remove(info.model_id)

    def _update_buttons(self) -> None:
        connected = self.app.connection.state == ConnectionState.CONNECTED
        info = self.selected()
        installing = info is not None and info.model_id in self._installs and \
            not self._installs[info.model_id].is_terminal
        self.install_button.setEnabled(connected and info is not None and not info.builtin
                                       and not info.installed and not installing)
        self.remove_button.setEnabled(connected and info is not None and not info.builtin
                                      and info.installed and not installing)
        self.refresh_button.setEnabled(connected)
        if info is not None and not self.details.text().startswith(("Install", "Starting", "Downloading")):
            self.details.setText("%s - %s%s" % (info.name, info.description,
                                                ("  License: %s" % info.license) if info.license else ""))
