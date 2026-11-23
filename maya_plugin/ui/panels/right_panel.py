"""Right panel: Properties, Parameters, Tool / Processing / Model settings (collapsible)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from maya_plugin.ui.components.collapsible import CollapsibleSection
from maya_plugin.ui.components.widgets import combo, spin
from maya_plugin.ui.qt import QtCore, QtWidgets


def _form_widget() -> QtWidgets.QWidget:
    widget = QtWidgets.QWidget()
    form = QtWidgets.QFormLayout(widget)
    form.setContentsMargins(8, 2, 8, 8)
    form.setLabelAlignment(QtCore.Qt.AlignRight)
    return widget


class RightPanel(QtWidgets.QScrollArea):
    """Context panel. Module panels push their properties via :meth:`show_module`."""

    def __init__(self, app: Any, bridge: Any, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MAITRightPanel")
        self.setWidgetResizable(True)
        self.setMinimumWidth(240)
        self._app = app
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.properties = _form_widget()
        self.parameters = _form_widget()
        self.tool_settings = _form_widget()
        self.processing = _form_widget()
        self.model = _form_widget()

        self.sections: Dict[str, CollapsibleSection] = {}
        for key, title, widget in (("properties", "Properties", self.properties),
                                   ("parameters", "Parameters", self.parameters),
                                   ("tool", "Tool Settings", self.tool_settings),
                                   ("processing", "Processing Settings", self.processing),
                                   ("model", "Model Settings", self.model)):
            section = CollapsibleSection(title, widget, expanded=key in ("properties", "processing"))
            self.sections[key] = section
            layout.addWidget(section)
        layout.addStretch(1)
        self.setWidget(container)
        self._build_static_sections()
        bridge.engine_health.connect(self.on_health)
        bridge.models_updated.connect(self.on_models)

    def _build_static_sections(self) -> None:
        form = self.processing.layout()
        self.device_combo = combo(["Auto", "CUDA (GPU)", "CPU"])
        self.precision_combo = combo(["FP32", "FP16 (mixed)"])
        self.batch_spin = spin(1, 64, 1)
        self.gpu_label = QtWidgets.QLabel("Unknown (engine offline)")
        self.gpu_label.setWordWrap(True)
        form.addRow("Device", self.device_combo)
        form.addRow("Precision", self.precision_combo)
        form.addRow("Batch size", self.batch_spin)
        form.addRow("Engine GPU", self.gpu_label)
        model_form = self.model.layout()
        self.models_label = QtWidgets.QLabel("Engine offline")
        self.models_label.setWordWrap(True)
        note = QtWidgets.QLabel("Install or remove models in AI Studio > Model Manager.")
        note.setObjectName("Dim")
        note.setWordWrap(True)
        model_form.addRow("Installed", self.models_label)
        model_form.addRow("", note)

    def on_models(self, payload: Any) -> None:
        models = [m for m in (payload or {}).get("models", []) if m.installed]
        self.models_label.setText("\n".join(m.name for m in models) if models else "None (engine offline?)")

    @staticmethod
    def _clear(form_widget: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
        form = form_widget.layout()
        while form.rowCount():
            form.removeRow(0)
        return form

    def show_module(self, descriptor: Any, panel: Any) -> None:
        props = self._clear(self.properties)
        for label, text in (("Module", descriptor.title), ("Status", descriptor.status)):
            value = QtWidgets.QLabel(text)
            value.setWordWrap(True)
            props.addRow(label, value)
        desc = QtWidgets.QLabel(descriptor.description)
        desc.setWordWrap(True)
        desc.setObjectName("Dim")
        props.addRow("", desc)
        params = self._clear(self.parameters)
        for label, widget in getattr(panel, "parameter_widgets", lambda: [])():
            params.addRow(label, widget)
        if params.rowCount() == 0:
            params.addRow(QtWidgets.QLabel("No parameters for this module yet."))
        tool = self._clear(self.tool_settings)
        for label, widget in getattr(panel, "tool_setting_widgets", lambda: [])():
            tool.addRow(label, widget)
        if tool.rowCount() == 0:
            tool.addRow(QtWidgets.QLabel("No tool settings for this module yet."))

    def on_health(self, health: Any) -> None:
        if health.gpu_devices:
            names = ", ".join("%s (%s MB)" % (d.name, d.memory_total_mb or "?") for d in health.gpu_devices)
            self.gpu_label.setText(names + (" - CUDA capable" if health.cuda_available else ""))
        else:
            self.gpu_label.setText("No NVIDIA GPU detected - CPU only")
