"""Maya Studio panel. Scene info is query-only; all import tools are disabled until later phases."""
from __future__ import annotations

from maya_plugin.core import maya_bridge
from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.widgets import Card, check, combo, future_button, grid
from maya_plugin.ui.qt import QtWidgets


class MayaStudioPanel(BaseModulePanel):
    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        info = Card("Maya Scene Information (read-only)")
        self.info_label = QtWidgets.QLabel("Press Refresh to read scene information.")
        self.info_label.setWordWrap(True)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.setToolTip("Reads scene information with query-only commands. Never modifies the scene.")
        refresh.clicked.connect(self.refresh)
        info.add(self.info_label)
        info.add(refresh)
        layout.addWidget(info)

        safety = QtWidgets.QLabel("All tools below will run only on an explicit click, on Maya's main thread, "
                                  "inside a single undo step.")
        safety.setObjectName("Dim")
        safety.setWordWrap(True)
        layout.addWidget(safety)

        cards = []
        for title, button, phase, rows in (
            ("Camera Import", "Import Solved Camera", "Phase 03", [("Scale", combo(["1 unit = 1 cm", "1 unit = 1 m"]))]),
            ("Geometry Import", "Import Geometry", "Phase 04", [("Format", combo(["OBJ", "Alembic", "USD"]))]),
            ("Animation Import", "Import Animation", "Phase 03", [("Keys", combo(["Bake every frame", "Reduce keys"]))]),
            ("Skeleton Import", "Import Skeleton", "Phase 05", [("Rig", combo(["HumanIK", "Joints only"]))]),
            ("Rotomation", "Start Rotomation", "Phase 06", [("Solve", check("Use body tracking as guide", True))]),
            ("FK / IK", "Match FK/IK", "Phase 06", [("Direction", combo(["FK to IK", "IK to FK"]))]),
        ):
            card = Card(title, badge=phase)
            card.add_form(rows)
            card.add(future_button(button, phase))
            cards.append(card)
        layout.addLayout(grid(cards))

    def refresh(self) -> None:
        if not maya_bridge.is_maya_available():
            self.info_label.setText("Maya is not available in this session.")
            return
        try:
            info = maya_bridge.read_scene_info()
        except Exception as exc:  # never let a query error break the UI
            self.info_label.setText("Could not read scene information: %s" % exc)
            return
        cams = ", ".join(info["cameras"]) or "none"
        self.info_label.setText(
            "Scene: %(scene)s%(mod)s\nMaya %(ver)s | units %(lin)s / %(time)s | frames %(a)s-%(b)s\n"
            "Cameras: %(cams)s\nMeshes: %(meshes)d | Joints: %(joints)d | Selected: %(sel)d" % {
                "scene": info["scene"], "mod": " (modified)" if info["modified"] else "", "ver": info["maya_version"],
                "lin": info["linear_unit"], "time": info["time_unit"], "a": info["frame_range"][0],
                "b": info["frame_range"][1], "cams": cams, "meshes": info["meshes"], "joints": info["joints"],
                "sel": info["selection"]})
