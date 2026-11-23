"""Event bus, project service, environment detection, node graph model."""
import json
import threading

import pytest

from maya_plugin.core.config_manager import ConfigManager
from maya_plugin.core.environment import detect_environment
from maya_plugin.core.event_manager import EventManager, Events
from maya_plugin.core.project_service import MANIFEST_NAME, ProjectError, ProjectService
from maya_plugin.modules.node_studio.graph_model import GraphError, NodeGraph


def test_event_bus_delivers_and_unsubscribes():
    bus = EventManager()
    got = []
    unsubscribe = bus.subscribe("x", got.append)
    bus.publish("x", 1)
    unsubscribe()
    bus.publish("x", 2)
    assert got == [1]


def test_event_bus_isolates_failing_subscribers():
    bus = EventManager()
    got = []
    bus.subscribe("x", lambda _p: 1 / 0)
    bus.subscribe("x", got.append)
    bus.publish("x", "ok")
    assert got == ["ok"]


def test_event_bus_thread_safety():
    bus = EventManager()
    counter = []
    lock = threading.Lock()

    def add(_p):
        with lock:
            counter.append(1)

    bus.subscribe("x", add)
    threads = [threading.Thread(target=lambda: [bus.publish("x") for _ in range(200)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(counter) == 1600


def test_project_create_open_recent(tmp_path):
    config = ConfigManager(user_path=tmp_path / "u.json")
    bus = EventManager()
    changes = []
    bus.subscribe(Events.PROJECT_CHANGED, changes.append)
    service = ProjectService(config, bus)
    project = service.create_project(str(tmp_path), "SHOW_seq010")
    assert (tmp_path / "SHOW_seq010" / MANIFEST_NAME).exists()
    assert (tmp_path / "SHOW_seq010" / "shots").is_dir()
    (tmp_path / "SHOW_seq010" / "shots" / "sh010").mkdir()
    reopened = ProjectService(config, bus).open_project(str(tmp_path / "SHOW_seq010"))
    assert reopened.name == "SHOW_seq010" and reopened.shots() == ["sh010"]
    assert service.recent_projects()[0] == project.root
    assert len(changes) == 2
    manifest = json.loads((tmp_path / "SHOW_seq010" / MANIFEST_NAME).read_text())
    assert "root" not in manifest  # relocatable


@pytest.mark.parametrize("name", ["", "../evil", "a/b", "x" * 80, " "])
def test_project_name_validation(tmp_path, name):
    service = ProjectService(ConfigManager(user_path=tmp_path / "u.json"), EventManager())
    with pytest.raises(ProjectError):
        service.create_project(str(tmp_path), name)


def test_project_errors(tmp_path):
    service = ProjectService(ConfigManager(user_path=tmp_path / "u.json"), EventManager())
    with pytest.raises(ProjectError):
        service.open_project(str(tmp_path))
    with pytest.raises(ProjectError):
        service.create_project(str(tmp_path / "missing"), "P")
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "file.txt").write_text("x")
    with pytest.raises(ProjectError):
        service.create_project(str(tmp_path), "busy")


def test_environment_detection_outside_maya():
    env = detect_environment(include_qt=False)
    assert env.python_version and env.os_name
    assert env.to_dict()["python_executable"]


def test_node_graph_rules_and_roundtrip(tmp_path):
    graph = NodeGraph()
    read = graph.add_node("read_footage").node_id
    seg = graph.add_node("ai_segment").node_id
    out = graph.add_node("write_output").node_id
    graph.connect(read, "image", seg, "image")
    graph.connect(seg, "mask", out, "in")
    with pytest.raises(GraphError):
        graph.connect(seg, "mask", seg, "image")          # self
    with pytest.raises(GraphError):
        graph.connect(read, "nope", seg, "image")         # bad port
    with pytest.raises(GraphError):
        graph.add_node("does_not_exist")
    merge = graph.add_node("merge").node_id
    graph.connect(seg, "mask", merge, "a")
    with pytest.raises(GraphError):
        graph.connect(merge, "out", seg, "image")         # cycle
    graph.connect(read, "image", merge, "a")              # replaces existing input connection
    assert sum(1 for e in graph.edges if e.dst == merge and e.dst_port == "a") == 1
    path = tmp_path / "wf.maitflow.json"
    graph.save(path)
    loaded = NodeGraph.load(path)
    assert loaded.to_dict() == graph.to_dict()
    graph.remove_node(seg)
    assert all(seg not in (e.src, e.dst) for e in graph.edges)
    with pytest.raises(GraphError):
        NodeGraph.from_dict({"format": "other"})
