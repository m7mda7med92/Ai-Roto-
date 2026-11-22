"""Small building blocks used by every module panel (cards, forms, viewers, headers)."""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple

from maya_plugin.ui.qt import QtCore, QtWidgets


def h_line() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.HLine)
    line.setObjectName("Dim")
    return line


def phase_badge(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("PhaseBadge")
    label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
    label.setToolTip("Interface foundation only - functionality arrives in a later phase.")
    return label


def accent_button(text: str, tooltip: str = "") -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton(text)
    button.setProperty("accent", True)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def future_button(text: str, phase: str) -> QtWidgets.QPushButton:
    """A visible but disabled button for a feature scheduled in a later phase."""
    button = QtWidgets.QPushButton(text)
    button.setEnabled(False)
    button.setToolTip("Planned for %s" % phase)
    return button


class Card(QtWidgets.QFrame):
    """Titled panel with an optional phase badge and a vertical content layout."""

    def __init__(self, title: str, badge: str = "", parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)
        header = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("CardTitle")
        header.addWidget(title_label)
        header.addStretch(1)
        if badge:
            header.addWidget(phase_badge(badge), 0, QtCore.Qt.AlignVCenter)
        outer.addLayout(header)
        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(6)
        outer.addLayout(self.body)
        outer.addStretch(1)  # extra height goes below the content, not between rows

    def add(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout: QtWidgets.QLayout) -> QtWidgets.QLayout:
        self.body.addLayout(layout)
        return layout

    def add_form(self, rows: Sequence[Tuple[str, QtWidgets.QWidget]]) -> QtWidgets.QFormLayout:
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        for label, widget in rows:
            form.addRow(label, widget)
        self.body.addLayout(form)
        return form


class ViewerPlaceholder(QtWidgets.QFrame):
    """Dark framed area reserved for a future viewer (footage, depth, skeleton...)."""

    def __init__(self, text: str, min_height: int = 220, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Viewer")
        self.setMinimumHeight(min_height)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        layout = QtWidgets.QVBoxLayout(self)
        self.label = QtWidgets.QLabel(text)
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)


def combo(items: Iterable[str], current: int = 0) -> QtWidgets.QComboBox:
    box = QtWidgets.QComboBox()
    box.addItems(list(items))
    box.setCurrentIndex(current)
    return box


def spin(minimum: float, maximum: float, value: float, decimals: int = 0, suffix: str = "",
         step: float = 1.0) -> QtWidgets.QAbstractSpinBox:
    if decimals:
        box: Any = QtWidgets.QDoubleSpinBox()
        box.setDecimals(decimals)
        box.setSingleStep(step)
    else:
        box = QtWidgets.QSpinBox()
        minimum, maximum, value = int(minimum), int(maximum), int(value)
    box.setRange(minimum, maximum)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


def check(text: str, checked: bool = False) -> QtWidgets.QCheckBox:
    box = QtWidgets.QCheckBox(text)
    box.setChecked(checked)
    return box


def path_field(placeholder: str = "", dialog_title: str = "Select", folder: bool = False,
               file_filter: str = "All files (*.*)") -> QtWidgets.QWidget:
    """Line edit + browse button. The browse dialog only reads the file system."""
    container = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    edit = QtWidgets.QLineEdit()
    edit.setPlaceholderText(placeholder)
    button = QtWidgets.QToolButton()
    button.setText("...")

    def browse() -> None:
        if folder:
            path = QtWidgets.QFileDialog.getExistingDirectory(container, dialog_title, edit.text())
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(container, dialog_title, edit.text(), file_filter)
        if path:
            edit.setText(path)

    button.clicked.connect(browse)
    layout.addWidget(edit, 1)
    layout.addWidget(button)
    container.line_edit = edit  # type: ignore[attr-defined]
    return container


def grid(widgets: List[QtWidgets.QWidget], columns: int = 2) -> QtWidgets.QGridLayout:
    layout = QtWidgets.QGridLayout()
    layout.setSpacing(10)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, index // columns, index % columns)
    for column in range(columns):
        layout.setColumnStretch(column, 1)
    return layout
