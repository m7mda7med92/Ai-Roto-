"""Qt binding shim.

Maya 2024 ships PySide2 (Qt 5.15); Maya 2025+ ships PySide6 (Qt 6). All UI
code imports Qt from here so supporting a new Maya version means touching
only this file. Override the choice with ``MAIT_QT_BINDING=PySide2|PySide6``.
"""
from __future__ import annotations

import os
from typing import Any

_preferred = os.environ.get("MAIT_QT_BINDING", "")
_order = [_preferred] if _preferred else []
_order += [b for b in ("PySide2", "PySide6") if b not in _order]

QT_BINDING = ""
QT_BINDING_VERSION = ""
_errors = []
for _binding in _order:
    try:
        if _binding == "PySide2":
            import PySide2 as _pyside  # type: ignore
            from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
            import shiboken2 as _shiboken  # type: ignore
        elif _binding == "PySide6":
            import PySide6 as _pyside  # type: ignore
            from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
            import shiboken6 as _shiboken  # type: ignore
        else:
            continue
        QT_BINDING = _binding
        QT_BINDING_VERSION = getattr(_pyside, "__version__", "")
        break
    except ImportError as _exc:  # pragma: no cover - depends on environment
        _errors.append("%s: %s" % (_binding, _exc))
else:  # pragma: no cover
    raise ImportError("No supported Qt binding found (%s)" % "; ".join(_errors))

Signal = QtCore.Signal
Slot = QtCore.Slot
IS_QT6 = QT_BINDING == "PySide6"

if IS_QT6:  # QAction moved to QtGui in Qt6
    QAction = QtGui.QAction
    QActionGroup = QtGui.QActionGroup
else:
    QAction = QtWidgets.QAction
    QActionGroup = QtWidgets.QActionGroup


def wrap_instance(ptr: Any, base: Any = None) -> Any:
    """Wrap a C++ pointer (e.g. from ``MQtUtil``) as a Python Qt object."""
    return _shiboken.wrapInstance(int(ptr), base or QtWidgets.QWidget)


def is_valid(obj: Any) -> bool:
    """False if the underlying C++ object was deleted (e.g. Maya closed the dock)."""
    try:
        return bool(obj is not None and _shiboken.isValid(obj))
    except Exception:
        return False


def exec_dialog(dialog: Any) -> int:
    return dialog.exec() if IS_QT6 else dialog.exec_()
