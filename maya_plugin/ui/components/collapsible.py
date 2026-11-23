"""Collapsible section with a clickable header."""
from __future__ import annotations

from typing import Optional

from maya_plugin.ui.qt import QtCore, QtWidgets


class CollapsibleSection(QtWidgets.QWidget):
    toggled = QtCore.Signal(bool)

    def __init__(self, title: str, content: QtWidgets.QWidget, expanded: bool = True,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.header = QtWidgets.QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.header.setStyleSheet("QToolButton { font-weight: 600; text-align: left; padding: 6px; }")
        self.content = content
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(content)
        self.header.toggled.connect(self.set_expanded)
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.header.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        self.content.setVisible(expanded)
        if self.header.isChecked() != expanded:
            self.header.setChecked(expanded)
        self.toggled.emit(expanded)

    def is_expanded(self) -> bool:
        return self.header.isChecked()
