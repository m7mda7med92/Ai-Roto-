"""Qt-free node graph data model with JSON (de)serialisation and validation."""
from __future__ import annotations

import copy
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from maya_plugin.modules.node_studio.node_library import NODE_TYPES_BY_ID

WORKFLOW_FORMAT = "mait.workflow"
WORKFLOW_VERSION = 1


class GraphError(ValueError):
    pass


@dataclass
class Node:
    node_id: str
    type_id: str
    title: str
    x: float = 0.0
    y: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    src: str
    src_port: str
    dst: str
    dst_port: str


class NodeGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self._counter = itertools.count(1)

    def add_node(self, type_id: str, x: float = 0.0, y: float = 0.0, node_id: Optional[str] = None) -> Node:
        node_type = NODE_TYPES_BY_ID.get(type_id)
        if node_type is None:
            raise GraphError("unknown node type '%s'" % type_id)
        if node_id is None:
            node_id = self._next_id(type_id)
        if node_id in self.nodes:
            raise GraphError("duplicate node id '%s'" % node_id)
        node = Node(node_id, type_id, node_type.title, x, y, copy.deepcopy(dict(node_type.params)))
        self.nodes[node_id] = node
        return node

    def remove_node(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        self.edges = [e for e in self.edges if e.src != node_id and e.dst != node_id]

    def connect(self, src: str, src_port: str, dst: str, dst_port: str) -> Edge:
        if src == dst:
            raise GraphError("cannot connect a node to itself")
        for node_id, port, kind in ((src, src_port, "outputs"), (dst, dst_port, "inputs")):
            node = self.nodes.get(node_id)
            if node is None:
                raise GraphError("unknown node '%s'" % node_id)
            if port not in getattr(NODE_TYPES_BY_ID[node.type_id], kind):
                raise GraphError("node '%s' has no %s port '%s'" % (node_id, kind[:-1], port))
        if self._reaches(dst, src):
            raise GraphError("connection would create a cycle")
        # one connection per input port: replace any existing one
        self.edges = [e for e in self.edges if not (e.dst == dst and e.dst_port == dst_port)]
        edge = Edge(src, src_port, dst, dst_port)
        self.edges.append(edge)
        return edge

    def disconnect(self, edge: Edge) -> None:
        self.edges = [e for e in self.edges if e != edge]

    def _reaches(self, start: str, target: str) -> bool:
        stack, seen = [start], set()
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(e.dst for e in self.edges if e.src == current)
        return False

    def _next_id(self, type_id: str) -> str:
        while True:
            candidate = "%s_%d" % (type_id, next(self._counter))
            if candidate not in self.nodes:
                return candidate

    # ------------------------------------------------------------------ serialisation
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": WORKFLOW_FORMAT, "version": WORKFLOW_VERSION,
            "nodes": [{"id": n.node_id, "type": n.type_id, "title": n.title, "x": n.x, "y": n.y, "params": n.params}
                      for n in self.nodes.values()],
            "edges": [{"src": e.src, "src_port": e.src_port, "dst": e.dst, "dst_port": e.dst_port} for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "NodeGraph":
        if not isinstance(data, dict) or data.get("format") != WORKFLOW_FORMAT:
            raise GraphError("not a toolkit workflow file")
        if int(data.get("version", 0)) > WORKFLOW_VERSION:
            raise GraphError("workflow was saved by a newer toolkit version")
        graph = cls()
        try:
            for item in data.get("nodes", []):
                node = graph.add_node(item["type"], float(item.get("x", 0)), float(item.get("y", 0)), item["id"])
                node.title = str(item.get("title", node.title))
                node.params.update(item.get("params") or {})
            for item in data.get("edges", []):
                graph.connect(item["src"], item["src_port"], item["dst"], item["dst_port"])
        except (KeyError, TypeError) as exc:
            raise GraphError("malformed workflow: %s" % exc)
        return graph

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "NodeGraph":
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            if isinstance(exc, GraphError):
                raise
            raise GraphError("cannot read workflow: %s" % exc)

