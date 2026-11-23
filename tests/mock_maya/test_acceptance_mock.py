"""REQUIRED ACCEPTANCE TEST, executed against MOCK Maya (fake maya.cmds, offscreen Qt).

Steps 1-14 of the Phase 01 acceptance workflow. Steps that need real Maya
(the actual shelf click, real viewport responsiveness) are covered by the
manual checklist in docs/MAYA_2024_MANUAL_TEST.md.
"""
import time

from qt_helpers import pump, pump_until
from shared.constants.engine_constants import ConnectionState, JobState


def test_acceptance_workflow_mock_maya(qapp, toolkit, fake_cmds):
    main = toolkit.main
    app = main.app

    # 1-3. Shelf command opened the UI as a dockable workspace control.
    assert toolkit.objectName() == "MayaAIToolkit"
    assert any(c[0] == "workspaceControl" and c[1][0] == "MayaAIToolkitWorkspaceControl" for c in fake_cmds.CALLS)
    assert main.isVisible()
    # Engine is offline (RED) before Start: plugin opened fine without an engine.
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.OFFLINE, 5)
    assert main.top_bar.status.dot.color_name() == "#f85149"

    # 4-7. Start Engine -> independent process -> connected -> GREEN.
    main.top_bar.start_button.click()
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.STARTING, 5) or \
        main.top_bar.status.state == ConnectionState.CONNECTED
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.CONNECTED, 30)
    assert main.top_bar.status.dot.color_name() == "#3fb950"
    assert app.connection.owns_engine and app.connection.status.engine_pid != __import__("os").getpid()

    # 8-10. Test Processing -> simulated job -> progress updates in the UI.
    bottom = main.bottom_panel
    assert bottom.test_button.isEnabled()
    bottom.test_button.click()
    assert pump_until(qapp, lambda: bottom.progress.value() > 0, 15)
    first = bottom.progress.value()
    assert pump_until(qapp, lambda: bottom.progress.value() > first, 10)
    job_id = bottom.current_job_id
    assert app.jobs.get(job_id).state == JobState.RUNNING
    assert "running" in bottom.task_label.text()

    # 13. UI stays responsive: the Qt event loop keeps turning during the job.
    ticks = []
    timer = __import__("maya_plugin.ui.qt", fromlist=["QtCore"]).QtCore.QTimer()
    timer.timeout.connect(lambda: ticks.append(time.time()))
    timer.start(20)
    pump(qapp, 0.5)
    timer.stop()
    assert len(ticks) >= 10
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) < 0.25

    # 11-12. Cancel -> job stops.
    assert bottom.cancel_button.isEnabled()
    bottom.cancel_button.click()
    assert pump_until(qapp, lambda: app.jobs.get(job_id).state == JobState.CANCELLED, 10)
    stopped_at = app.jobs.get(job_id).progress
    assert stopped_at < 1.0
    assert pump_until(qapp, lambda: "cancelled" in bottom.task_label.text(), 5)

    # Stop the engine from the UI.
    main.top_bar.action_stop.trigger()
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.OFFLINE, 30)

    # 14. No scene-modifying Maya command was issued during the whole workflow.
    assert fake_cmds.scene_modifying_calls() == []
