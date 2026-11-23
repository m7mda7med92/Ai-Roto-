"""Pipeline Manager: job queue, status, progress, pause/resume/cancel and processing logs."""
from __future__ import annotations

import html
import time
from typing import Any

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.job_table import JobTable
from maya_plugin.ui.components.widgets import Card, accent_button
from maya_plugin.ui.qt import QtWidgets
from maya_plugin.ui.styles import LOG_LEVEL_COLORS, PALETTE
from shared.constants.engine_constants import ConnectionState, JobState


class PipelineManagerPanel(BaseModulePanel):
    phase_label = ""

    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        summary = Card("Job Status")
        self.summary_label = QtWidgets.QLabel("No jobs yet.")
        summary.add(self.summary_label)
        controls = QtWidgets.QHBoxLayout()
        self.test_button = accent_button("Test Processing", "Submit a simulated job")
        self.pause_button = QtWidgets.QPushButton("Pause")
        self.resume_button = QtWidgets.QPushButton("Resume")
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_all_button = QtWidgets.QPushButton("Cancel All")
        for button in (self.test_button, self.pause_button, self.resume_button, self.cancel_button,
                       self.cancel_all_button):
            controls.addWidget(button)
        controls.addStretch(1)
        summary.add_layout(controls)
        layout.addWidget(summary)

        queue = Card("Job Queue")
        self.table = JobTable()
        self.table.setMinimumHeight(220)
        queue.add(self.table)
        layout.addWidget(queue)

        logs = Card("Processing Logs")
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setMinimumHeight(160)
        logs.add(self.log_view)
        layout.addWidget(logs)

        jobs = self.app.jobs
        self.test_button.clicked.connect(lambda: jobs.submit_test_job())
        self.pause_button.clicked.connect(lambda: self._selected(jobs.pause))
        self.resume_button.clicked.connect(lambda: self._selected(jobs.resume))
        self.cancel_button.clicked.connect(lambda: self._selected(jobs.cancel))
        self.cancel_all_button.clicked.connect(lambda: jobs.cancel_all())
        self.bridge.job_updated.connect(self.on_job_updated)
        self.bridge.job_error.connect(lambda e: self._log("ERROR", e.get("message", "")))
        self.bridge.connection_changed.connect(self.on_connection)
        for status in jobs.jobs():
            self.table.upsert(status)
        self.on_connection(self.app.connection.status)

    def _selected(self, action: Any) -> None:
        job_id = self.table.selected_job_id()
        if job_id:
            action(job_id)

    def on_connection(self, status: Any) -> None:
        connected = status.state == ConnectionState.CONNECTED
        for button in (self.test_button, self.pause_button, self.resume_button, self.cancel_button,
                       self.cancel_all_button):
            button.setEnabled(connected)

    def on_job_updated(self, status: Any) -> None:
        self.table.upsert(status)
        if status.is_terminal or status.state in (JobState.QUEUED, JobState.PAUSED):
            self._log("INFO", "%s %s: %s" % (status.label or status.job_id[:8], status.state.value, status.message))
        counts = {}
        for job in self.app.jobs.jobs():
            counts[job.state.value] = counts.get(job.state.value, 0) + 1
        self.summary_label.setText("  |  ".join("%s: %d" % (k.capitalize(), v) for k, v in sorted(counts.items())))

    def _log(self, level: str, message: str) -> None:
        color = LOG_LEVEL_COLORS.get(level, PALETTE["text"])
        self.log_view.appendHtml('<span style="color:%s">%s %s</span>' % (
            color, time.strftime("%H:%M:%S"), html.escape(message)))
