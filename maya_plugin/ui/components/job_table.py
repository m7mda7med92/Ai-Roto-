"""Job list widget shared by the bottom panel and the Pipeline Manager."""
from __future__ import annotations

import time
from typing import Dict, Optional

from maya_plugin.ui.qt import QtCore, QtGui, QtWidgets
from maya_plugin.ui.styles import PALETTE
from shared.constants.engine_constants import JobState
from shared.contracts.job_contracts import JobStatus

_STATE_COLORS = {
    JobState.QUEUED: PALETTE["text_dim"], JobState.RUNNING: PALETTE["accent"], JobState.PAUSED: PALETTE["warn"],
    JobState.CANCELLING: PALETTE["warn"], JobState.CANCELLED: PALETTE["idle"], JobState.COMPLETED: PALETTE["ok"],
    JobState.FAILED: PALETTE["error"],
}
COLUMNS = ("Job", "Type", "State", "Progress", "Message", "Started")


class JobTable(QtWidgets.QTableWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(list(COLUMNS))
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        for column, width in ((0, 90), (1, 90), (2, 90), (3, 140), (5, 80)):
            self.setColumnWidth(column, width)
        self._rows: Dict[str, int] = {}
        self._status: Dict[str, JobStatus] = {}

    def upsert(self, status: JobStatus) -> None:
        row = self._rows.get(status.job_id)
        if row is None:
            row = self.rowCount()
            self.insertRow(row)
            self._rows[status.job_id] = row
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 1000)
            self.setCellWidget(row, 3, bar)
            for column in (0, 1, 2, 4, 5):
                self.setItem(row, column, QtWidgets.QTableWidgetItem())
            self.item(row, 0).setData(QtCore.Qt.UserRole, status.job_id)
        self._status[status.job_id] = status
        self.item(row, 0).setText(status.label or status.job_id[:8])
        self.item(row, 0).setToolTip(status.job_id)
        self.item(row, 1).setText(status.job_type)
        state_item = self.item(row, 2)
        state_item.setText(status.state.value.capitalize())
        state_item.setForeground(QtGui.QBrush(QtGui.QColor(_STATE_COLORS.get(status.state, PALETTE["text"]))))
        bar = self.cellWidget(row, 3)
        bar.setValue(int(round(status.progress * 1000)))
        bar.setFormat("%d%%" % int(round(status.progress * 100)))
        message = status.error.message if status.error else status.message
        self.item(row, 4).setText(message)
        self.item(row, 4).setToolTip(message)
        if status.started_at:
            self.item(row, 5).setText(time.strftime("%H:%M:%S", time.localtime(status.started_at)))

    def selected_job_id(self) -> Optional[str]:
        rows = self.selectionModel().selectedRows()
        if rows:
            item = self.item(rows[0].row(), 0)
            return item.data(QtCore.Qt.UserRole) if item else None
        # fall back to the most recent active job
        for job_id in reversed(list(self._rows)):
            if not self._status[job_id].is_terminal:
                return job_id
        return None

    def progress_text(self, job_id: str) -> str:
        row = self._rows.get(job_id)
        return self.cellWidget(row, 3).format() if row is not None else ""

    def clear_finished(self) -> None:
        for job_id in [j for j, s in self._status.items() if s.is_terminal]:
            self.removeRow(self._rows.pop(job_id))
            self._status.pop(job_id)
            self._rows = {jid: index for index, jid in enumerate(self._iter_ids())}

    def _iter_ids(self):
        for row in range(self.rowCount()):
            yield self.item(row, 0).data(QtCore.Qt.UserRole)
