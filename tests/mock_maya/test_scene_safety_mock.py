"""Scene-safety and robustness checks under MOCK Maya."""
import importlib
import sys

from qt_helpers import pump, pump_until
from shared.constants.engine_constants import ConnectionState


def test_plugin_load_registers_command_only(fake_cmds):
    import maya.api.OpenMaya as om
    om.REGISTERED.clear()
    entry = importlib.import_module("maya_plugin.plugin_entry")
    shim_path = __import__("qt_helpers").MOCKS.parent.parent / "plug-ins" / "MayaAIToolkit.py"
    spec = importlib.util.spec_from_file_location("MayaAIToolkit", shim_path)
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    shim.initializePlugin(object())
    assert list(om.REGISTERED) == [entry.COMMAND_NAME]
    assert entry.MayaAIToolkitCommand.creator().isUndoable() is False
    assert fake_cmds.CALLS == []  # loading the plug-in issued no Maya commands at all
    shim.uninitializePlugin(object())
    assert om.REGISTERED == {}


def test_importing_plugin_has_no_side_effects(fake_cmds):
    for name in [m for m in sys.modules if m.startswith("maya_plugin")]:
        del sys.modules[name]
    importlib.import_module("maya_plugin")
    importlib.import_module("maya_plugin.launcher")
    assert fake_cmds.CALLS == []


def test_ui_opens_when_engine_unavailable_and_never_raises(qapp, toolkit, fake_cmds):
    main = toolkit.main
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.OFFLINE, 5)
    for action in (main.top_bar.action_connect, main.top_bar.action_health, main.top_bar.action_disconnect,
                   main.top_bar.action_stop):
        action.trigger()
    main.bottom_panel.submit_test_job()  # engine offline -> reported, not raised
    pump(qapp, 0.5)
    assert main.isVisible()
    assert fake_cmds.scene_modifying_calls() == []


def test_reopen_replaces_previous_window(qapp, toolkit, fake_cmds):
    from maya_plugin import launcher
    second = launcher.show()
    pump(qapp, 0.1)
    assert second is not None and second is not toolkit
    # The previous workspace control (ours only) was closed and deleted before re-creating it.
    assert ("deleteUI", ("MayaAIToolkitWorkspaceControl",), {"control": True}) in fake_cmds.CALLS
    assert fake_cmds.scene_modifying_calls() == []


def test_launcher_failure_is_contained(monkeypatch, fake_cmds):
    import maya_plugin.ui.workspace as ws
    from maya_plugin import launcher

    def boom(_app):
        raise RuntimeError("simulated UI failure")

    monkeypatch.setattr(ws, "show_toolkit", boom)
    assert launcher.show() is None  # error reported to the Script Editor, not raised into Maya
    assert any(c[0] == "warning" for c in fake_cmds.CALLS)


def test_scene_operation_helper_uses_single_undo_chunk(fake_cmds):
    from maya_plugin.core import maya_bridge
    result = maya_bridge.run_scene_operation("MAIT test", lambda: 42)
    assert result == 42
    names = [(c[0], c[2]) for c in fake_cmds.CALLS]
    assert names == [("undoInfo", {"openChunk": True, "chunkName": "MAIT test"}), ("undoInfo", {"closeChunk": True})]
