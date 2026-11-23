"""FAKE ``MayaQWidgetDockableMixin``: records the workspaceControl call and shows a plain widget."""
from typing import Any

from maya import cmds


class MayaQWidgetDockableMixin:
    def show(self, dockable: bool = False, floating: bool = True, retain: bool = True,  # type: ignore[override]
             width: int = 0, height: int = 0, **kwargs: Any) -> None:
        if dockable:
            cmds.workspaceControl(self.objectName() + "WorkspaceControl", label=self.windowTitle(),
                                  floating=floating, retain=retain)
        if width and height:
            self.resize(width, height)
        super().show()  # type: ignore[misc]
