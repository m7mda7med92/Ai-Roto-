"""MOCK MAYA test setup.

These tests import the FAKE ``maya`` package from ``tests/mocks`` and run the
real plugin + Qt UI offscreen. They verify plugin logic under a Maya-like
runtime; they are NOT tests inside Autodesk Maya. See docs/PHASE_01_REPORT.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qt_helpers import MOCKS, pump  # noqa: E402,F401

if str(MOCKS) not in sys.path:
    sys.path.insert(0, str(MOCKS))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from maya_plugin.ui import qt
except ImportError:  # no PySide2/PySide6 in this interpreter: skip mock-Maya UI tests
    qt = None
    collect_ignore_glob = ["test_*.py"]


def pytest_collection_modifyitems(items):
    for item in items:
        if "mock_maya" in str(item.fspath):
            item.add_marker(pytest.mark.mock_maya)


@pytest.fixture(scope="session")
def qapp():
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    yield app


@pytest.fixture
def fake_cmds():
    from maya import cmds
    assert getattr(sys.modules["maya"], "IS_MOCK", False), "real Maya must never be used by these tests"
    cmds.reset()
    return cmds


@pytest.fixture
def toolkit(qapp, fake_cmds, plugin_config, monkeypatch):
    """Open the toolkit through the real launcher (shelf entry point) with mock Maya."""
    import maya_plugin.core.app_manager as am
    from maya_plugin import launcher

    monkeypatch.setattr(am, "ConfigManager", lambda: plugin_config)
    am.reset_app_manager()
    window = launcher.show()
    assert window is not None
    pump(qapp, 0.2)
    yield window
    launcher.shutdown()
    pump(qapp, 0.1)
