"""Procedurally drawn toolkit logo (no binary assets needed)."""
from __future__ import annotations

from maya_plugin.ui.qt import QtCore, QtGui
from maya_plugin.ui.styles import PALETTE


def logo_pixmap(size: int = 28) -> QtGui.QPixmap:
    """An orange rounded square with a stylised lens/track mark."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    rect = QtCore.QRectF(0.5, 0.5, size - 1, size - 1)
    gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QtGui.QColor(PALETTE["accent_hover"]))
    gradient.setColorAt(1.0, QtGui.QColor("#b85a1c"))
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(rect, size * 0.22, size * 0.22)
    pen = QtGui.QPen(QtGui.QColor("#1b1d21"))
    pen.setWidthF(max(1.5, size * 0.08))
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    c = size / 2.0
    painter.drawEllipse(QtCore.QPointF(c, c), size * 0.26, size * 0.26)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        painter.drawLine(QtCore.QPointF(c + dx * size * 0.30, c + dy * size * 0.30),
                         QtCore.QPointF(c + dx * size * 0.42, c + dy * size * 0.42))
    painter.setBrush(QtGui.QColor("#1b1d21"))
    painter.drawEllipse(QtCore.QPointF(c, c), size * 0.06, size * 0.06)
    painter.end()
    return pixmap
