"""Left navigation sidebar: one checkable button per registered module."""
from __future__ import annotations

from typing import Dict, Optional, Sequence

from maya_plugin.ui.qt import QtWidgets, Signal


class Sidebar(QtWidgets.QFrame):
    module_selected = Signal(str)

    def __init__(self, modules: Sequence[object], parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MAITSidebar")
        self.setMinimumWidth(170)
        self.setMaximumWidth(240)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(0)
        header = QtWidgets.QLabel("MODULES")
        header.setObjectName("SidebarHeader")
        layout.addWidget(header)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: Dict[str, QtWidgets.QPushButton] = {}
        for index, module in enumerate(modules, start=1):
            button = QtWidgets.QPushButton("%2d   %s" % (index, getattr(module, "title").replace("&", "&&")))
            button.setToolTip(getattr(module, "description", ""))
            button.setCheckable(True)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            button.setFlat(True)
            module_id = getattr(module, "module_id")
            button.clicked.connect(lambda *_a, m=module_id: self.module_selected.emit(m))
            self._group.addButton(button)
            self.buttons[module_id] = button
            layout.addWidget(button)
        layout.addStretch(1)

    def set_current(self, module_id: str) -> None:
        button = self.buttons.get(module_id)
        if button is not None and not button.isChecked():
            button.setChecked(True)
