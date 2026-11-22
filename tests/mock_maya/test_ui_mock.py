"""UI structure and behaviour under MOCK Maya (offscreen Qt)."""
import json

from qt_helpers import pump, pump_until
from maya_plugin.modules.registry import MODULES
from shared.constants.engine_constants import ConnectionState


def test_all_modules_load_and_sidebar_navigates(qapp, toolkit):
    main = toolkit.main
    assert len(MODULES) == 10 and len(main.sidebar.buttons) == 10
    for descriptor in MODULES:
        main.sidebar.buttons[descriptor.module_id].click()
        pump(qapp, 0.02)
        assert main.current_module == descriptor.module_id
        panel = main.panel(descriptor.module_id)
        assert type(panel).__name__ != "ErrorPanel", descriptor.module_id
        assert main.stack.currentWidget() is panel


def test_panels_collapse_and_workspaces(qapp, toolkit):
    main = toolkit.main
    main.top_bar.right_toggle.setChecked(False)
    main.top_bar.bottom_toggle.setChecked(False)
    assert not main.right_panel.isVisible() and not main.bottom_panel.isVisible()
    main.top_bar.workspace_combo.setCurrentText("Tracking")
    assert main.right_panel.isVisible() and main.bottom_panel.isVisible()
    assert main.current_module == "camera_tracking"
    main.top_bar.workspace_combo.setCurrentText("Pipeline")
    assert not main.right_panel.isVisible() and main.current_module == "pipeline_manager"
    section = main.right_panel.sections["model"]
    section.set_expanded(True)
    assert section.content.isVisibleTo(main.right_panel.widget())
    section.set_expanded(False)
    assert not section.content.isVisibleTo(main.right_panel.widget())


def test_gray_status_when_engine_not_configured(qapp, toolkit, tmp_path):
    main = toolkit.main
    main.app.config.set("engine.python_executable", str(tmp_path / "missing_python.exe"), save=False)
    main.app.connection.refresh_configuration().result(5)
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.NOT_CONFIGURED, 5)
    assert main.top_bar.status.dot.color_name() == "#6e7681"
    assert not main.top_bar.start_button.isEnabled()
    main.top_bar.start_button.click()  # disabled: nothing happens, nothing raises
    main.top_bar.action_connect.trigger()
    pump(qapp, 0.3)
    assert main.top_bar.status.state == ConnectionState.NOT_CONFIGURED


def test_job_buttons_disabled_while_offline(qapp, toolkit):
    main = toolkit.main
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.OFFLINE, 5)
    assert not main.bottom_panel.test_button.isEnabled()
    assert not main.top_bar.action_test.isEnabled()
    main.show_module("pipeline_manager")
    assert not main.panel("pipeline_manager").test_button.isEnabled()


def test_settings_dialog_writes_user_settings_only(qapp, toolkit):
    from maya_plugin.ui.dialogs.settings_dialog import SettingsDialog
    config = toolkit.main.app.config
    dialog = SettingsDialog(config, toolkit.main)
    dialog.port_spin.setValue(8899)
    dialog.auto_connect.setChecked(False)
    dialog.apply()
    saved = json.loads(config.user_path.read_text())
    assert saved["engine"]["port"] == 8899 and saved["engine"]["auto_connect_on_open"] is False


def test_project_flow_updates_ui(qapp, toolkit, tmp_path):
    main = toolkit.main
    main.app.projects.create_project(str(tmp_path), "DemoShow")
    assert pump_until(qapp, lambda: main.top_bar.project_label.text() == "DemoShow", 3)
    main.show_module("project_manager")
    panel = main.panel("project_manager")
    pump(qapp, 0.1)
    assert panel.recent_list.count() >= 1 and panel.paths_table.rowCount() == 8
    main.save()  # toolkit save; must not touch the Maya scene
    from maya import cmds
    assert not any(c[0] == "file" and not c[2].get("query") for c in cmds.CALLS)


def test_node_studio_interaction(qapp, toolkit, tmp_path):
    from maya_plugin.modules.node_studio.graph_model import NodeGraph
    from maya_plugin.ui.qt import QtCore
    main = toolkit.main
    main.show_module("node_studio")
    panel = main.panel("node_studio")
    view = panel.view
    a = view.add_node("read_footage", QtCore.QPointF(0, 0))
    b = view.add_node("ai_segment", QtCore.QPointF(250, 0))
    assert view.connect_ports(view.node_items[a].ports["out:image"], view.node_items[b].ports["in:image"])
    assert not view.connect_ports(view.node_items[b].ports["in:image"], view.node_items[b].ports["out:mask"])
    assert len(view.edge_items) == 1
    panel.search.setText("depth")
    names = [panel.library_tree.topLevelItem(i).child(j).text(0)
             for i in range(panel.library_tree.topLevelItemCount())
             for j in range(panel.library_tree.topLevelItem(i).childCount())]
    assert names == ["AI Depth"]
    view.node_items[a].setSelected(True)
    pump(qapp, 0.05)
    assert panel.props_table.rowCount() == 3
    path = tmp_path / "flow.maitflow.json"
    view.graph.save(path)
    view.load_graph(NodeGraph())
    assert not view.node_items
    view.load_graph(NodeGraph.load(path))
    assert len(view.node_items) == 2 and len(view.edge_items) == 1
    view.node_items[a].setSelected(True)
    view.delete_selected()
    assert len(view.node_items) == 1 and not view.edge_items


def test_maya_studio_scene_info_is_query_only(qapp, toolkit, fake_cmds):
    main = toolkit.main
    main.show_module("maya_studio")
    panel = main.panel("maya_studio")
    fake_cmds.CALLS.clear()
    panel.refresh()
    assert "perspShape" in panel.info_label.text()
    assert fake_cmds.CALLS and fake_cmds.scene_modifying_calls() == []
