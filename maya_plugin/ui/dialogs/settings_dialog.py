"""Toolkit settings dialog. Changes are written to the user settings file only (never the scene)."""
from __future__ import annotations

from typing import Any, Optional

from maya_plugin.ui.components.widgets import check, combo, path_field, spin
from maya_plugin.ui.qt import QtWidgets


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, config: Any, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Toolkit Settings")
        self.setMinimumWidth(560)
        self._config = config
        layout = QtWidgets.QVBoxLayout(self)

        engine_box = QtWidgets.QGroupBox("AI Engine")
        engine_form = QtWidgets.QFormLayout(engine_box)
        self.python_field = path_field("Leave empty to use <toolkit>/.venv_engine", "Engine Python executable",
                                       file_filter="Python (python.exe python python3);;All files (*.*)")
        self.python_field.line_edit.setText(config.get("engine.python_executable", ""))
        self.venv_edit = QtWidgets.QLineEdit(config.get("engine.venv_dir", ".venv_engine"))
        self.port_spin = spin(0, 65535, config.get("engine.port", 0))
        self.port_spin.setSpecialValueText("Auto")
        self.startup_spin = spin(5, 300, config.get("engine.startup_timeout_seconds", 30), suffix=" s")
        self.health_spin = spin(0.5, 60, config.get("engine.health_check_interval_seconds", 3.0), 1, " s", 0.5)
        self.auto_connect = check("Connect automatically when the toolkit opens",
                                  config.get("engine.auto_connect_on_open", True))
        self.stop_on_exit = check("Stop the engine when Maya exits (if started from Maya)",
                                  config.get("engine.stop_owned_engine_on_exit", True))
        engine_form.addRow("Engine Python", self.python_field)
        engine_form.addRow("Engine venv folder", self.venv_edit)
        engine_form.addRow("Port", self.port_spin)
        engine_form.addRow("Startup timeout", self.startup_spin)
        engine_form.addRow("Health check every", self.health_spin)
        engine_form.addRow("", self.auto_connect)
        engine_form.addRow("", self.stop_on_exit)
        note = QtWidgets.QLabel("The engine always runs in its own Python - never Maya's interpreter.")
        note.setObjectName("Dim")
        engine_form.addRow("", note)
        layout.addWidget(engine_box)

        jobs_box = QtWidgets.QGroupBox("Jobs & Logging")
        jobs_form = QtWidgets.QFormLayout(jobs_box)
        self.test_duration = spin(1, 600, config.get("jobs.test_job_duration_seconds", 20), suffix=" s")
        self.poll_spin = spin(0.1, 10, config.get("jobs.poll_interval_seconds", 0.5), 1, " s", 0.1)
        levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        current = str(config.get("logging.level", "INFO")).upper()
        self.log_level = combo(levels, levels.index(current) if current in levels else 1)
        jobs_form.addRow("Test job duration", self.test_duration)
        jobs_form.addRow("Progress poll interval", self.poll_spin)
        jobs_form.addRow("Log level", self.log_level)
        layout.addWidget(jobs_box)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        c = self._config
        c.set("engine.python_executable", self.python_field.line_edit.text().strip(), save=False)
        c.set("engine.venv_dir", self.venv_edit.text().strip() or ".venv_engine", save=False)
        c.set("engine.port", int(self.port_spin.value()), save=False)
        c.set("engine.startup_timeout_seconds", int(self.startup_spin.value()), save=False)
        c.set("engine.health_check_interval_seconds", float(self.health_spin.value()), save=False)
        c.set("engine.auto_connect_on_open", self.auto_connect.isChecked(), save=False)
        c.set("engine.stop_owned_engine_on_exit", self.stop_on_exit.isChecked(), save=False)
        c.set("jobs.test_job_duration_seconds", int(self.test_duration.value()), save=False)
        c.set("jobs.poll_interval_seconds", float(self.poll_spin.value()), save=False)
        c.set("logging.level", self.log_level.currentText(), save=False)
        c.save()
