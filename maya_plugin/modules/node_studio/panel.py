"""Node Studio panel: library + search, graph view, properties, workflow save/load."""
from __future__ import annotations

from typing import Optional

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.modules.node_studio.graph_model import GraphError, NodeGraph
from maya_plugin.modules.node_studio.graph_view import NodeGraphView
from maya_plugin.modules.node_studio.node_library import NODE_TYPES, NODE_TYPES_BY_ID
from maya_plugin.ui.qt import QtCore, QtWidgets


class NodeStudioPanel(BaseModulePanel):
    phase_label = "Phase 01 - graph editing only"
    subtitle = "Drag from an output port to an input port to connect. Del removes, F frames, wheel zooms, MMB pans."

    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        toolbar = QtWidgets.QHBoxLayout()
        self.new_button = QtWidgets.QPushButton("New")
        self.load_button = QtWidgets.QPushButton("Load Workflow...")
        self.save_button = QtWidgets.QPushButton("Save Workflow...")
        self.frame_button = QtWidgets.QPushButton("Frame All")
        for button in (self.new_button, self.load_button, self.save_button, self.frame_button):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("Dim")
        toolbar.addWidget(self.status_label)
        layout.addLayout(toolbar)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        library = QtWidgets.QWidget()
        lib_layout = QtWidgets.QVBoxLayout(library)
        lib_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search nodes...")
        self.library_tree = QtWidgets.QTreeWidget()
        self.library_tree.setHeaderHidden(True)
        lib_layout.addWidget(self.search)
        lib_layout.addWidget(self.library_tree)
        self._populate_library()

        self.view = NodeGraphView()
        props = QtWidgets.QWidget()
        props_layout = QtWidgets.QVBoxLayout(props)
        props_layout.setContentsMargins(0, 0, 0, 0)
        title = QtWidgets.QLabel("Node Properties")
        title.setObjectName("CardTitle")
        self.props_table = QtWidgets.QTableWidget(0, 2)
        self.props_table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.props_table.horizontalHeader().setStretchLastSection(True)
        self.props_table.verticalHeader().setVisible(False)
        self.props_hint = QtWidgets.QLabel("Select a node.")
        self.props_hint.setObjectName("Dim")
        self.props_hint.setWordWrap(True)
        props_layout.addWidget(title)
        props_layout.addWidget(self.props_hint)
        props_layout.addWidget(self.props_table)

        splitter.addWidget(library)
        splitter.addWidget(self.view)
        splitter.addWidget(props)
        splitter.setSizes([180, 700, 240])
        splitter.setMinimumHeight(440)
        layout.addWidget(splitter, 1)

        self.search.textChanged.connect(self._populate_library)
        self.library_tree.itemDoubleClicked.connect(self._on_library_double_click)
        self.view.selection_changed.connect(self._show_properties)
        self.view.graph_changed.connect(self._update_status)
        self.view.error.connect(lambda msg: self.status_label.setText("Cannot connect: " + msg))
        self.props_table.itemChanged.connect(self._on_param_edited)
        self.new_button.clicked.connect(lambda: self.view.load_graph(NodeGraph()))
        self.save_button.clicked.connect(self.save_workflow)
        self.load_button.clicked.connect(self.load_workflow)
        self.frame_button.clicked.connect(self.view.frame_all)
        self._current_node: Optional[str] = None
        self._update_status()

    # ------------------------------------------------------------------ library
    def _populate_library(self, *_args: object) -> None:
        text = self.search.text().strip().lower() if hasattr(self, "search") else ""
        self.library_tree.clear()
        categories = {}
        for node_type in NODE_TYPES:
            if text and text not in node_type.title.lower() and text not in node_type.category.lower():
                continue
            parent = categories.get(node_type.category)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem([node_type.category])
                categories[node_type.category] = parent
                self.library_tree.addTopLevelItem(parent)
            child = QtWidgets.QTreeWidgetItem([node_type.title])
            child.setData(0, QtCore.Qt.UserRole, node_type.type_id)
            child.setToolTip(0, node_type.description)
            parent.addChild(child)
        self.library_tree.expandAll()

    def _on_library_double_click(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        type_id = item.data(0, QtCore.Qt.UserRole)
        if type_id:
            self.view.add_node(type_id)

    # ------------------------------------------------------------------ properties
    def _show_properties(self, node_id: Optional[str]) -> None:
        self._current_node = None
        self.props_table.blockSignals(True)
        self.props_table.setRowCount(0)
        node = self.view.graph.nodes.get(node_id) if node_id else None
        if node is None:
            self.props_hint.setText("Select a node.")
        else:
            self.props_hint.setText("%s (%s)\n%s" % (node.title, node.node_id,
                                                     NODE_TYPES_BY_ID[node.type_id].description))
            for key, value in node.params.items():
                row = self.props_table.rowCount()
                self.props_table.insertRow(row)
                key_item = QtWidgets.QTableWidgetItem(key)
                key_item.setFlags(key_item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.props_table.setItem(row, 0, key_item)
                self.props_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))
            self._current_node = node.node_id
        self.props_table.blockSignals(False)

    def _on_param_edited(self, item: QtWidgets.QTableWidgetItem) -> None:
        node = self.view.graph.nodes.get(self._current_node or "")
        if node is None or item.column() != 1:
            return
        key = self.props_table.item(item.row(), 0).text()
        original = node.params.get(key)
        text = item.text()
        try:
            if isinstance(original, bool):
                value: object = text.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(original, int):
                value = int(text)
            elif isinstance(original, float):
                value = float(text)
            else:
                value = text
        except ValueError:
            self.status_label.setText("Invalid value for %s" % key)
            return
        node.params[key] = value

    # ------------------------------------------------------------------ workflow files
    def _workflow_dir(self) -> str:
        project = self.app.projects.current
        return str(project.root_path / project.paths.get("workflows", "workflows")) if project else ""

    def save_workflow(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Workflow", self._workflow_dir(),
                                                        "Toolkit workflow (*.maitflow.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".maitflow.json"
        try:
            self.view.graph.save(path)
            self.status_label.setText("Saved %s" % path)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Save Workflow", str(exc))

    def load_workflow(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Workflow", self._workflow_dir(),
                                                        "Toolkit workflow (*.json)")
        if not path:
            return
        try:
            self.view.load_graph(NodeGraph.load(path))
            self.status_label.setText("Loaded %s" % path)
        except GraphError as exc:
            QtWidgets.QMessageBox.warning(self, "Load Workflow", str(exc))

    def _update_status(self) -> None:
        graph = self.view.graph
        self.status_label.setText("%d nodes, %d connections" % (len(graph.nodes), len(graph.edges)))
