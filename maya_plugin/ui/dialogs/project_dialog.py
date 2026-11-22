"""New-project dialog (creates a folder on disk; never touches the Maya scene)."""
from __future__ import annotations

from typing import Optional, Tuple

from maya_plugin.ui.components.widgets import path_field
from maya_plugin.ui.qt import QtWidgets


class NewProjectDialog(QtWidgets.QDialog):
    def __init__(self, default_parent: str = "", parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Toolkit Project")
        self.setMinimumWidth(480)
        layout = QtWidgets.QFormLayout(self)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("e.g. SHOW_seq010")
        self.parent_field = path_field("Folder that will contain the project", "Project location", folder=True)
        self.parent_field.line_edit.setText(default_parent)
        layout.addRow("Project name", self.name_edit)
        layout.addRow("Location", self.parent_field)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> Tuple[str, str]:
        return self.name_edit.text().strip(), self.parent_field.line_edit.text().strip()
