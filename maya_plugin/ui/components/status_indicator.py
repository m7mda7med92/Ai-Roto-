"""Engine connection status pill: coloured dot + label (GREEN/YELLOW/RED/GRAY)."""
from __future__ import annotations

from typing import Optional

from maya_plugin.ui.qt import QtCore, QtGui, QtWidgets
from maya_plugin.ui.styles import STATUS_COLORS, STATUS_LABELS
from shared.constants.engine_constants import ConnectionState


class StatusDot(QtWidgets.QWidget):
    def __init__(self, diameter: int = 10, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QtGui.QColor(STATUS_COLORS[ConnectionState.NOT_CONFIGURED])
        self._diameter = diameter
        self.setFixedSize(diameter + 4, diameter + 4)

    def set_color(self, color: str) -> None:
        self._color = QtGui.QColor(color)
        self.update()

    def color_name(self) -> str:
        return self._color.name()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        glow = QtGui.QColor(self._color)
        glow.setAlpha(70)
        painter.setBrush(glow)
        painter.drawEllipse(0, 0, self._diameter + 4, self._diameter + 4)
        painter.setBrush(self._color)
        painter.drawEllipse(2, 2, self._diameter, self._diameter)
        painter.end()


class StatusIndicator(QtWidgets.QWidget):
    """Shows the engine :class:`ConnectionState` using the colours defined in ``styles``."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)
        self.dot = StatusDot()
        self.label = QtWidgets.QLabel()
        self.label.setStyleSheet("background: transparent;")
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self.state = ConnectionState.NOT_CONFIGURED
        self.set_state(ConnectionState.NOT_CONFIGURED)

    def set_state(self, state: ConnectionState, detail: str = "") -> None:
        self.state = state
        self.dot.set_color(STATUS_COLORS[state])
        self.label.setText(STATUS_LABELS[state])
        self.setToolTip(detail or STATUS_LABELS[state])
