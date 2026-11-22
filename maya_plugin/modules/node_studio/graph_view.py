"""Interactive node graph view (QGraphicsScene) backed by :class:`NodeGraph`.

Interactions: drag nodes, drag from an output port to an input port to connect,
Delete/Backspace removes the selection, mouse wheel zooms, middle mouse pans,
F frames all nodes.
"""
from __future__ import annotations

from typing import Dict, Optional

from maya_plugin.modules.node_studio.graph_model import Edge, GraphError, NodeGraph
from maya_plugin.modules.node_studio.node_library import NODE_TYPES_BY_ID
from maya_plugin.ui.qt import QtCore, QtGui, QtWidgets, Signal
from maya_plugin.ui.styles import PALETTE

NODE_WIDTH = 170
HEADER_H = 24
PORT_SPACING = 20
PORT_R = 6
CATEGORY_COLORS = {"Input": "#3d7ea6", "AI": "#9b59b6", "Tracking": "#e8833a", "Matte": "#2e9e6e",
                   "Utility": "#6e7681", "Output": "#c0392b"}


class PortItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, node_item: "NodeItem", name: str, is_output: bool) -> None:
        super().__init__(-PORT_R, -PORT_R, 2 * PORT_R, 2 * PORT_R, node_item)
        self.node_item = node_item
        self.name = name
        self.is_output = is_output
        self.setBrush(QtGui.QBrush(QtGui.QColor("#c9d1d9")))
        self.setPen(QtGui.QPen(QtGui.QColor("#1b1d21"), 1.5))
        self.setAcceptHoverEvents(True)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setToolTip(name)

    def hoverEnterEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["accent"])))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self.setBrush(QtGui.QBrush(QtGui.QColor("#c9d1d9")))
        super().hoverLeaveEvent(event)


class NodeItem(QtWidgets.QGraphicsObject):
    moved = Signal()

    def __init__(self, node_id: str, title: str, type_id: str) -> None:
        super().__init__()
        self.node_id = node_id
        self.title = title
        node_type = NODE_TYPES_BY_ID[type_id]
        self.color = QtGui.QColor(CATEGORY_COLORS.get(node_type.category, "#6e7681"))
        rows = max(len(node_type.inputs), len(node_type.outputs), 1)
        self.height = HEADER_H + 10 + rows * PORT_SPACING
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsMovable | QtWidgets.QGraphicsItem.ItemIsSelectable
                      | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        self.ports: Dict[str, PortItem] = {}
        for index, name in enumerate(node_type.inputs):
            port = PortItem(self, name, False)
            port.setPos(0, HEADER_H + 14 + index * PORT_SPACING)
            self.ports["in:" + name] = port
        for index, name in enumerate(node_type.outputs):
            port = PortItem(self, name, True)
            port.setPos(NODE_WIDTH, HEADER_H + 14 + index * PORT_SPACING)
            self.ports["out:" + name] = port
        self._inputs = node_type.inputs
        self._outputs = node_type.outputs

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        return QtCore.QRectF(-PORT_R, -2, NODE_WIDTH + 2 * PORT_R, self.height + 4)

    def paint(self, painter: QtGui.QPainter, _option: object, _widget: object = None) -> None:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        body = QtCore.QRectF(0, 0, NODE_WIDTH, self.height)
        pen = QtGui.QPen(QtGui.QColor(PALETTE["accent"] if self.isSelected() else "#111316"),
                         2.0 if self.isSelected() else 1.0)
        painter.setPen(pen)
        painter.setBrush(QtGui.QColor("#2b2f35"))
        painter.drawRoundedRect(body, 5, 5)
        header = QtGui.QPainterPath()
        header.addRoundedRect(QtCore.QRectF(0, 0, NODE_WIDTH, HEADER_H), 5, 5)
        painter.fillPath(header, self.color)
        painter.setPen(QtGui.QColor("#f0f3f6"))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QtCore.QRectF(8, 0, NODE_WIDTH - 16, HEADER_H), QtCore.Qt.AlignVCenter, self.title)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(PALETTE["text_dim"]))
        for index, name in enumerate(self._inputs):
            painter.drawText(QtCore.QRectF(10, HEADER_H + 6 + index * PORT_SPACING, 70, 16), QtCore.Qt.AlignLeft, name)
        for index, name in enumerate(self._outputs):
            painter.drawText(QtCore.QRectF(NODE_WIDTH - 80, HEADER_H + 6 + index * PORT_SPACING, 70, 16),
                             QtCore.Qt.AlignRight, name)

    def itemChange(self, change: QtWidgets.QGraphicsItem.GraphicsItemChange, value: object) -> object:  # noqa: N802
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            self.moved.emit()
        return super().itemChange(change, value)


class EdgeItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, edge: Optional[Edge], src: PortItem, dst: Optional[PortItem] = None) -> None:
        super().__init__()
        self.edge = edge
        self.src = src
        self.dst = dst
        self.setZValue(-1)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, edge is not None)
        self.setPen(QtGui.QPen(QtGui.QColor("#9aa4ae"), 2))
        self.update_path()

    def update_path(self, end: Optional[QtCore.QPointF] = None) -> None:
        start = self.src.scenePos()
        stop = self.dst.scenePos() if self.dst is not None else (end or start)
        dx = max(40.0, abs(stop.x() - start.x()) * 0.5)
        path = QtGui.QPainterPath(start)
        path.cubicTo(start + QtCore.QPointF(dx, 0), stop - QtCore.QPointF(dx, 0), stop)
        self.setPath(path)
        color = PALETTE["accent"] if self.isSelected() else "#9aa4ae"
        self.setPen(QtGui.QPen(QtGui.QColor(color), 2))


class NodeGraphView(QtWidgets.QGraphicsView):
    selection_changed = Signal(object)   # node_id or None
    graph_changed = Signal()
    error = Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.graph = NodeGraph()
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.scene().setSceneRect(-5000, -5000, 10000, 10000)
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setMinimumHeight(360)
        self.node_items: Dict[str, NodeItem] = {}
        self.edge_items: list = []
        self._drag_edge: Optional[EdgeItem] = None
        self._panning: Optional[QtCore.QPoint] = None
        self.scene().selectionChanged.connect(self._on_selection)

    # ------------------------------------------------------------------ model <-> scene
    def add_node(self, type_id: str, pos: Optional[QtCore.QPointF] = None) -> str:
        if pos is None:
            pos = self.mapToScene(self.viewport().rect().center()) + QtCore.QPointF(
                (len(self.node_items) % 5) * 30, (len(self.node_items) % 5) * 30)
        node = self.graph.add_node(type_id, pos.x(), pos.y())
        self._create_node_item(node.node_id)
        self.graph_changed.emit()
        return node.node_id

    def _create_node_item(self, node_id: str) -> NodeItem:
        node = self.graph.nodes[node_id]
        item = NodeItem(node.node_id, node.title, node.type_id)
        item.setPos(node.x, node.y)
        item.moved.connect(lambda nid=node.node_id: self._on_node_moved(nid))
        self.scene().addItem(item)
        self.node_items[node.node_id] = item
        return item

    def connect_ports(self, src: PortItem, dst: PortItem) -> bool:
        if src.is_output == dst.is_output:
            return False
        if not src.is_output:
            src, dst = dst, src
        try:
            self.graph.connect(src.node_item.node_id, src.name, dst.node_item.node_id, dst.name)
        except GraphError as exc:
            self.error.emit(str(exc))
            return False
        self.rebuild_edges()
        self.graph_changed.emit()
        return True

    def rebuild_edges(self) -> None:
        for item in self.edge_items:
            self.scene().removeItem(item)
        self.edge_items = []
        for edge in self.graph.edges:
            src = self.node_items[edge.src].ports["out:" + edge.src_port]
            dst = self.node_items[edge.dst].ports["in:" + edge.dst_port]
            item = EdgeItem(edge, src, dst)
            self.scene().addItem(item)
            self.edge_items.append(item)

    def load_graph(self, graph: NodeGraph) -> None:
        self.scene().clear()
        self.node_items, self.edge_items = {}, []
        self.graph = graph
        for node_id in graph.nodes:
            self._create_node_item(node_id)
        self.rebuild_edges()
        self.frame_all()
        self.graph_changed.emit()

    def delete_selected(self) -> None:
        for item in self.scene().selectedItems():
            if isinstance(item, NodeItem):
                self.graph.remove_node(item.node_id)
            elif isinstance(item, EdgeItem) and item.edge is not None:
                self.graph.disconnect(item.edge)
        for node_id in [n for n in self.node_items if n not in self.graph.nodes]:
            self.scene().removeItem(self.node_items.pop(node_id))
        self.rebuild_edges()
        self.graph_changed.emit()

    def frame_all(self) -> None:
        if self.node_items:
            rect = self.scene().itemsBoundingRect().adjusted(-60, -60, 60, 60)
            self.fitInView(rect, QtCore.Qt.KeepAspectRatio)

    def _on_node_moved(self, node_id: str) -> None:
        item = self.node_items.get(node_id)
        node = self.graph.nodes.get(node_id)
        if item is None or node is None:
            return
        node.x, node.y = item.pos().x(), item.pos().y()
        for edge_item in self.edge_items:
            if edge_item.edge and node_id in (edge_item.edge.src, edge_item.edge.dst):
                edge_item.update_path()

    def _on_selection(self) -> None:
        try:
            nodes = [i for i in self.scene().selectedItems() if isinstance(i, NodeItem)]
        except RuntimeError:  # scene being destroyed
            return
        self.selection_changed.emit(nodes[0].node_id if len(nodes) == 1 else None)

    # ------------------------------------------------------------------ mouse / keys
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = event.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            return
        item = self.itemAt(event.pos())
        if isinstance(item, PortItem) and event.button() == QtCore.Qt.LeftButton:
            self._drag_edge = EdgeItem(None, item)
            self.scene().addItem(self._drag_edge)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self._panning is not None:
            delta = event.pos() - self._panning
            self._panning = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        if self._drag_edge is not None:
            self._drag_edge.update_path(self.mapToScene(event.pos()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self._panning is not None and event.button() == QtCore.Qt.MiddleButton:
            self._panning = None
            self.unsetCursor()
            return
        if self._drag_edge is not None:
            source = self._drag_edge.src
            self.scene().removeItem(self._drag_edge)
            self._drag_edge = None
            target = self.itemAt(event.pos())
            if isinstance(target, PortItem) and target is not source:
                self.connect_ports(source, target)
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        scale = self.transform().m11() * factor
        if 0.2 <= scale <= 3.0:
            self.scale(factor, factor)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_selected()
            return
        if event.key() == QtCore.Qt.Key_F:
            self.frame_all()
            return
        super().keyPressEvent(event)

    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:  # noqa: N802
        painter.fillRect(rect, QtGui.QColor("#15171a"))
        step = 25
        left, top = int(rect.left()) - int(rect.left()) % step, int(rect.top()) - int(rect.top()) % step
        minor, major = QtGui.QPen(QtGui.QColor("#1d2024")), QtGui.QPen(QtGui.QColor("#24282d"))
        x = left
        while x < rect.right():
            painter.setPen(major if x % (step * 5) == 0 else minor)
            painter.drawLine(QtCore.QLineF(x, rect.top(), x, rect.bottom()))
            x += step
        y = top
        while y < rect.bottom():
            painter.setPen(major if y % (step * 5) == 0 else minor)
            painter.drawLine(QtCore.QLineF(rect.left(), y, rect.right(), y))
            y += step
