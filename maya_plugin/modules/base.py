"""Base class for module panels shown in the central workspace."""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from maya_plugin.ui.components.widgets import phase_badge
from maya_plugin.ui.qt import QtCore, QtWidgets


class BaseModulePanel(QtWidgets.QScrollArea):
    """Scrollable panel with a standard header. Subclasses implement :meth:`build`.

    ``app`` is the :class:`~maya_plugin.core.app_manager.AppManager`; ``bridge``
    is the :class:`~maya_plugin.ui.bridge.UiBridge` delivering events on the main thread.
    """

    subtitle: str = ""
    phase_label: str = "Phase 01 - UI foundation"

    def __init__(self, descriptor: Any, app: Any, bridge: Any, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.descriptor = descriptor
        self.app = app
        self.bridge = bridge
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        container = QtWidgets.QWidget()
        self.root_layout = QtWidgets.QVBoxLayout(container)
        self.root_layout.setContentsMargins(16, 12, 16, 16)
        self.root_layout.setSpacing(12)
        header = QtWidgets.QHBoxLayout()
        titles = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel(descriptor.title)
        title.setObjectName("ModuleTitle")
        subtitle = QtWidgets.QLabel(self.subtitle or descriptor.description)
        subtitle.setObjectName("ModuleSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        if self.phase_label:
            header.addWidget(phase_badge(self.phase_label), 0, QtCore.Qt.AlignTop)
        self.root_layout.addLayout(header)
        self.build(self.root_layout)
        self.root_layout.addStretch(1)
        self.setWidget(container)

    def build(self, layout: QtWidgets.QVBoxLayout) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def parameter_widgets(self) -> List[Tuple[str, QtWidgets.QWidget]]:
        """Rows shown in the right panel's *Parameters* section."""
        return []

    def tool_setting_widgets(self) -> List[Tuple[str, QtWidgets.QWidget]]:
        """Rows shown in the right panel's *Tool Settings* section."""
        return []

    def on_activated(self) -> None:
        """Called each time the module becomes visible."""


class ErrorPanel(QtWidgets.QWidget):
    """Shown when a module fails to load, so one broken module never breaks the toolkit."""

    def __init__(self, title: str, error: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel("<b>%s failed to load</b><br><br>%s" % (title, error))
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(label)
        layout.addStretch(1)
