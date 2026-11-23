"""Roto viewer: shows an engine-made proxy frame, a mask overlay and the artist's prompts.

Controls (prompts are reported in *source* pixel coordinates, whatever the proxy size):

* Points mode: left-click = include point, right-click or Shift+left-click = exclude point.
* Box mode (or Ctrl+drag in points mode): drag a rectangle around the subject.

The widget only paints and reports clicks. It never reads media itself, never talks to
the engine and never touches the Maya scene.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from maya_plugin.ui.qt import QtCore, QtGui, QtWidgets, Signal

INCLUDE_COLOR = QtGui.QColor(80, 220, 120)
EXCLUDE_COLOR = QtGui.QColor(240, 80, 80)
BOX_COLOR = QtGui.QColor(255, 200, 60)
MASK_COLOR = QtGui.QColor(70, 160, 255)


def _event_pos(event: QtGui.QMouseEvent) -> QtCore.QPointF:
    if hasattr(event, "position"):  # Qt6
        return event.position()
    return QtCore.QPointF(event.pos())


def mask_overlay(mask: QtGui.QImage, color: QtGui.QColor = MASK_COLOR, opacity: float = 0.45) -> QtGui.QImage:
    """Grayscale matte -> coloured ARGB overlay whose alpha follows the matte."""
    alpha = mask.convertToFormat(QtGui.QImage.Format_Grayscale8)
    overlay = QtGui.QImage(alpha.size(), QtGui.QImage.Format_ARGB32)
    overlay.fill(color)
    overlay.setAlphaChannel(alpha)
    result = QtGui.QImage(alpha.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
    result.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(result)
    painter.setOpacity(opacity)
    painter.drawImage(0, 0, overlay)
    painter.end()
    return result


class RotoViewer(QtWidgets.QWidget):
    point_added = Signal(float, float, bool)          # source x, y, include
    box_drawn = Signal(float, float, float, float)    # source x0, y0, x1, y1

    MODES = ("points", "box")

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("RotoViewer")
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._image: Optional[QtGui.QImage] = None
        self._overlay: Optional[QtGui.QImage] = None
        self._source_size: Tuple[int, int] = (0, 0)
        self._points: List[Tuple[float, float, int]] = []
        self._box: Optional[Tuple[float, float, float, float]] = None
        self._drag_start: Optional[QtCore.QPointF] = None
        self._drag_now: Optional[QtCore.QPointF] = None
        self._message = "Load a plate to start"
        self.mode = "points"
        self.show_mask = True

    # ------------------------------------------------------------------ state
    def set_frame(self, image: Optional[QtGui.QImage], source_size: Tuple[int, int]) -> None:
        self._image = image if image is not None and not image.isNull() else None
        self._source_size = (int(source_size[0]), int(source_size[1]))
        self.update()

    def set_mask(self, mask: Optional[QtGui.QImage]) -> None:
        self._overlay = mask_overlay(mask) if mask is not None and not mask.isNull() else None
        self.update()

    def has_mask(self) -> bool:
        return self._overlay is not None

    def set_prompts(self, points: List[Tuple[float, float, int]],
                    box: Optional[Tuple[float, float, float, float]]) -> None:
        self._points, self._box = list(points), box
        self.update()

    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    def set_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(mode)
        self.mode = mode
        self.setCursor(QtCore.Qt.CrossCursor)

    # ------------------------------------------------------------------ geometry
    def image_rect(self) -> QtCore.QRectF:
        """Where the frame is drawn: source aspect ratio, fitted and centred."""
        sw, sh = self._source_size
        if not sw or not sh:
            return QtCore.QRectF()
        scale = min(self.width() / float(sw), self.height() / float(sh))
        w, h = sw * scale, sh * scale
        return QtCore.QRectF((self.width() - w) / 2.0, (self.height() - h) / 2.0, w, h)

    def to_source(self, pos: QtCore.QPointF) -> Optional[Tuple[float, float]]:
        rect = self.image_rect()
        if rect.isEmpty() or not rect.contains(pos):
            return None
        sw, sh = self._source_size
        x = (pos.x() - rect.left()) * sw / rect.width()
        y = (pos.y() - rect.top()) * sh / rect.height()
        return min(max(x, 0.0), sw - 1.0), min(max(y, 0.0), sh - 1.0)

    def to_widget(self, x: float, y: float) -> QtCore.QPointF:
        rect = self.image_rect()
        sw, sh = self._source_size
        return QtCore.QPointF(rect.left() + x * rect.width() / sw, rect.top() + y * rect.height() / sh)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QtGui.QColor(18, 19, 22))
        rect = self.image_rect()
        if self._image is None or rect.isEmpty():
            painter.setPen(QtGui.QColor(120, 125, 135))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, self._message)
            painter.end()
            return
        painter.drawImage(rect, self._image)
        if self._overlay is not None and self.show_mask:
            painter.drawImage(rect, self._overlay)
        if self._box is not None:
            self._draw_box(painter, self.to_widget(self._box[0], self._box[1]),
                           self.to_widget(self._box[2], self._box[3]))
        if self._drag_start is not None and self._drag_now is not None:
            self._draw_box(painter, self._drag_start, self._drag_now)
        for x, y, label in self._points:
            center = self.to_widget(x, y)
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 2))
            painter.setBrush(INCLUDE_COLOR if label == 1 else EXCLUDE_COLOR)
            painter.drawEllipse(center, 5.5, 5.5)
        if self._message and self._image is not None:
            painter.setPen(QtGui.QColor(200, 205, 215))
            painter.drawText(QtCore.QRectF(8, 6, self.width() - 16, 20), QtCore.Qt.AlignLeft, self._message)
        painter.end()

    @staticmethod
    def _draw_box(painter: QtGui.QPainter, a: QtCore.QPointF, b: QtCore.QPointF) -> None:
        pen = QtGui.QPen(BOX_COLOR, 1.5, QtCore.Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(QtCore.QRectF(a, b).normalized())

    # ------------------------------------------------------------------ mouse
    def _wants_box(self, event: QtGui.QMouseEvent) -> bool:
        return self.mode == "box" or bool(event.modifiers() & QtCore.Qt.ControlModifier)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        pos = _event_pos(event)
        if self.to_source(pos) is None:
            return
        if event.button() == QtCore.Qt.LeftButton and self._wants_box(event):
            self._drag_start = self._drag_now = pos
            return
        if event.button() in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
            include = event.button() == QtCore.Qt.LeftButton and not (event.modifiers() & QtCore.Qt.ShiftModifier)
            x, y = self.to_source(pos)  # type: ignore[misc]
            self.point_added.emit(x, y, include)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self._drag_start is not None:
            self._drag_now = _event_pos(event)
            self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self._drag_start is None:
            return
        start, end = self._drag_start, _event_pos(event)
        self._drag_start = self._drag_now = None
        self.update()
        rect = self.image_rect()
        end = QtCore.QPointF(min(max(end.x(), rect.left()), rect.right() - 0.01),
                             min(max(end.y(), rect.top()), rect.bottom() - 0.01))
        a, b = self.to_source(start), self.to_source(end)
        if a is None or b is None or abs(end.x() - start.x()) < 4 or abs(end.y() - start.y()) < 4:
            return
        self.box_drawn.emit(a[0], a[1], b[0], b[1])
