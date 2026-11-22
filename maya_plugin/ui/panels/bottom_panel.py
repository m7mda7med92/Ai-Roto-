"""Bottom panel: current task + progress, job queue, activity log, engine messages."""
from __future__ import annotations

import html
import time
from typing import Any, Optional

from maya_plugin.ui.components.job_table import JobTable
from maya_plugin.ui.components.widgets import accent_button
from maya_plugin.ui.qt import QtCore, QtWidgets
from maya_plugin.ui.styles import LOG_LEVEL_COLORS, PALETTE
from shared.constants.engine_constants import ConnectionState, JobState
from shared.contracts.job_contracts import JobStatus


def _log_view() -> QtWidgets.QPlainTextEdit:
    view = QtWidgets.QPlainTextEdit()
    view.setReadOnly(True)
    view.setMaximumBlockCount(2000)
    view.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 11px;")
    return view


class BottomPanel(QtWidgets.QFrame):
    def __init__(self, app: Any, bridge: Any, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MAITBottomPanel")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._app = app
        self.current_job_id: Optional[str] = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)

        # Current task strip
        strip = QtWidgets.QHBoxLayout()
        caption = QtWidgets.QLabel("Current task:")
        caption.setObjectName("Dim")
        self.task_label = QtWidgets.QLabel("Idle")
        self.task_label.setMinimumWidth(220)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.test_button = accent_button("Test Processing", "Submit a simulated job to the engine")
        self.pause_button = QtWidgets.QPushButton("Pause")
        self.resume_button = QtWidgets.QPushButton("Resume")
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        for widget in (caption, self.task_label):
            strip.addWidget(widget)
        strip.addWidget(self.progress, 1)
        for widget in (self.test_button, self.pause_button, self.resume_button, self.cancel_button):
            strip.addWidget(widget)
        layout.addLayout(strip)

        # Tabs
        self.tabs = QtWidgets.QTabWidget()
        queue_page = QtWidgets.QWidget()
        queue_layout = QtWidgets.QVBoxLayout(queue_page)
        queue_layout.setContentsMargins(4, 4, 4, 4)
        self.job_table = JobTable()
        queue_layout.addWidget(self.job_table)
        clear = QtWidgets.QPushButton("Clear Finished")
        clear.clicked.connect(self.job_table.clear_finished)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(clear)
        queue_layout.addLayout(row)
        self.activity_log = _log_view()
        self.engine_messages = _log_view()
        self.tabs.addTab(queue_page, "Job Queue")
        self.tabs.addTab(self.activity_log, "Activity Log")
        self.tabs.addTab(self.engine_messages, "Engine Messages")
        layout.addWidget(self.tabs, 1)

        # Wiring (buttons only queue background work; they never block)
        self.test_button.clicked.connect(self.submit_test_job)
        self.pause_button.clicked.connect(lambda: self._job_action(self._app.jobs.pause))
        self.resume_button.clicked.connect(lambda: self._job_action(self._app.jobs.resume))
        self.cancel_button.clicked.connect(lambda: self._job_action(self._app.jobs.cancel))
        bridge.job_updated.connect(self.on_job_updated)
        bridge.job_error.connect(lambda e: self.append_engine_message("error", e.get("message", "")))
        bridge.log_record.connect(self.append_log_record)
        bridge.engine_message.connect(lambda m: self.append_engine_message(m.get("level", "info"), m.get("message", "")))
        bridge.connection_changed.connect(self.on_connection_changed)
        self.on_connection_changed(app.connection.status)

    # ------------------------------------------------------------------ actions
    def submit_test_job(self) -> None:
        self.task_label.setText("Submitting test job...")
        self._app.jobs.submit_test_job()

    def _job_action(self, action: Any) -> None:
        job_id = self.job_table.selected_job_id() or self.current_job_id
        if job_id:
            action(job_id)

    # ------------------------------------------------------------------ slots (main thread)
    def on_connection_changed(self, status: Any) -> None:
        connected = status.state == ConnectionState.CONNECTED
        self.test_button.setEnabled(connected)
        self._update_job_buttons()

    def on_job_updated(self, status: JobStatus) -> None:
        self.job_table.upsert(status)
        if not status.is_terminal or status.job_id == self.current_job_id or self.current_job_id is None:
            self.current_job_id = status.job_id
            self.progress.setValue(int(round(status.progress * 1000)))
            label = status.label or status.job_type
            self.task_label.setText("%s - %s (%s)" % (label, status.state.value, status.message))
        self._update_job_buttons()

    def _update_job_buttons(self) -> None:
        connected = self._app.connection.is_connected
        status = self._app.jobs.get(self.current_job_id) if self.current_job_id else None
        active = status is not None and not status.is_terminal
        self.cancel_button.setEnabled(connected and active)
        self.pause_button.setEnabled(connected and active and status.state == JobState.RUNNING)
        self.resume_button.setEnabled(connected and active and status.state == JobState.PAUSED)

    def append_log_record(self, record: dict) -> None:
        level = record.get("level", "INFO")
        stamp = time.strftime("%H:%M:%S", time.localtime(record.get("ts", time.time())))
        color = LOG_LEVEL_COLORS.get(level, PALETTE["text"])
        self.activity_log.appendHtml('<span style="color:%s">%s %-7s %s</span>' % (
            color, stamp, level, html.escape(record.get("message", ""))))

    def append_engine_message(self, level: str, message: str) -> None:
        if not message:
            return
        color = LOG_LEVEL_COLORS.get(level.upper(), PALETTE["text"])
        stamp = time.strftime("%H:%M:%S")
        self.engine_messages.appendHtml('<span style="color:%s">%s %s</span>' % (color, stamp, html.escape(message)))
