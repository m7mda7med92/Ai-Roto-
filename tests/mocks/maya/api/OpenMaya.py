"""FAKE ``maya.api.OpenMaya`` - just enough for plugin_entry registration tests."""
from typing import Any, Callable, Dict

REGISTERED: Dict[str, Callable[[], Any]] = {}


class MPxCommand:
    def __init__(self) -> None:
        pass


class MFnPlugin:
    def __init__(self, plugin: Any, vendor: str = "", version: str = "", api: str = "") -> None:
        self.plugin = plugin
        self.vendor = vendor
        self.version = version

    def registerCommand(self, name: str, creator: Callable[[], Any]) -> None:  # noqa: N802
        if name in REGISTERED:
            raise RuntimeError("command already registered: %s" % name)
        REGISTERED[name] = creator

    def deregisterCommand(self, name: str) -> None:  # noqa: N802
        REGISTERED.pop(name, None)
