"""Maya plug-in (Python API 2.0): registers the ``mayaAIToolkit`` command.

Loaded through ``plug-ins/MayaAIToolkit.py`` (installed via the module file).
Loading the plug-in does **not** open the UI, start the engine or modify the
scene; it only registers the command::

    cmds.mayaAIToolkit()            # open the UI
"""
from __future__ import annotations

from typing import Any

import maya.api.OpenMaya as om  # type: ignore

from shared.version import TOOLKIT_VERSION

COMMAND_NAME = "mayaAIToolkit"
VENDOR = "Maya AI Production Toolkit"


def maya_useNewAPI() -> None:  # noqa: N802 - Maya convention
    """Tells Maya this plug-in uses Python API 2.0."""


class MayaAIToolkitCommand(om.MPxCommand):
    def __init__(self) -> None:
        super().__init__()

    def doIt(self, _args: Any) -> None:  # noqa: N802
        from maya_plugin import launcher
        launcher.show()

    def isUndoable(self) -> bool:  # noqa: N802 - opening UI is not a scene operation
        return False

    @staticmethod
    def creator() -> "MayaAIToolkitCommand":
        return MayaAIToolkitCommand()


def initializePlugin(plugin: Any) -> None:  # noqa: N802
    fn = om.MFnPlugin(plugin, VENDOR, TOOLKIT_VERSION, "Any")
    fn.registerCommand(COMMAND_NAME, MayaAIToolkitCommand.creator)


def uninitializePlugin(plugin: Any) -> None:  # noqa: N802
    from maya_plugin import launcher
    launcher.shutdown()
    om.MFnPlugin(plugin).deregisterCommand(COMMAND_NAME)
