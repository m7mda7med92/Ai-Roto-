"""Plate viewer with a body skeleton overlay (Phase 04).

Green: the AI's 2D body joints (brighter = more confident). Orange: the fitted rig joints projected
back through the Maya camera, so the artist sees where the fit disagrees with the plate. A box drawn
in the viewer marks the subject on that frame.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from maya_plugin.modules.roto_studio.viewer import RotoViewer
from maya_plugin.ui.qt import QtCore, QtGui

POSE_COLOR = QtGui.QColor(90, 230, 120)
FIT_COLOR = QtGui.QColor(255, 150, 50)
BOX_COLOR = QtGui.QColor(255, 210, 70)

Point = Tuple[float, float, float]   # x, y, confidence (1 for fitted joints)


class PoseViewer(RotoViewer):
    def __init__(self, bones: Sequence[Tuple[int, int]], parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.bones = list(bones)
        self._pose: List[Point] = []
        self._fit: List[Optional[Tuple[float, float]]] = []
        self._subject: Optional[Tuple[float, float, float, float]] = None
        self.min_confidence = 0.25
        self.set_mode("box")

    def set_pose(self, pose: Sequence[Sequence[float]]) -> None:
        self._pose = [(float(p[0]), float(p[1]), float(p[-1])) for p in pose]
        self.update()

    def set_fit(self, fit: Sequence[Optional[Sequence[float]]]) -> None:
        self._fit = [(float(p[0]), float(p[1])) if p is not None else None for p in fit]
        self.update()

    def set_subject_box(self, box: Optional[Sequence[float]]) -> None:
        self._subject = tuple(box) if box else None  # type: ignore[assignment]
        self.update()

    def has_pose(self) -> bool:
        return any(p[2] >= self.min_confidence for p in self._pose)

    def has_fit(self) -> bool:
        return any(p is not None for p in self._fit)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.image_rect().isEmpty():
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        if self._subject is not None:
            painter.setPen(QtGui.QPen(BOX_COLOR, 1.5, QtCore.Qt.DashLine))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(QtCore.QRectF(self.to_widget(self._subject[0], self._subject[1]),
                                           self.to_widget(self._subject[2], self._subject[3])).normalized())
        self._draw(painter, [(x, y) if c >= self.min_confidence else None for x, y, c in self._pose], POSE_COLOR,
                   [c for _x, _y, c in self._pose])
        self._draw(painter, self._fit, FIT_COLOR, None)
        painter.end()

    def _draw(self, painter: QtGui.QPainter, points: Sequence[Optional[Tuple[float, float]]], color: QtGui.QColor,
              confidence: Optional[Sequence[float]]) -> None:
        if not points:
            return
        for a, b in self.bones:
            if a < len(points) and b < len(points) and points[a] is not None and points[b] is not None:
                painter.setPen(QtGui.QPen(color, 2.0))
                painter.drawLine(self.to_widget(*points[a]), self.to_widget(*points[b]))
        for index, point in enumerate(points):
            if point is None:
                continue
            shade = QtGui.QColor(color)
            if confidence is not None:
                shade.setAlphaF(0.35 + 0.65 * max(0.0, min(1.0, confidence[index])))
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
            painter.setBrush(shade)
            painter.drawEllipse(self.to_widget(*point), 3.5, 3.5)
