"""Node type catalogue. Phase 01 node types are descriptive only - they do not process data."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class NodeType:
    type_id: str
    title: str
    category: str
    inputs: Tuple[str, ...] = ()
    outputs: Tuple[str, ...] = ()
    params: Dict[str, object] = field(default_factory=dict)
    description: str = ""


NODE_TYPES: List[NodeType] = [
    NodeType("read_footage", "Read Footage", "Input", (), ("image",), {"path": "", "first": 1001, "last": 1100},
             "Image sequence or video source."),
    NodeType("read_maya_camera", "Maya Camera", "Input", (), ("camera",), {"camera": ""},
             "Reference to a Maya camera (read-only)."),
    NodeType("ai_segment", "AI Segment", "AI", ("image",), ("mask",), {"model": "", "prompt": "points"},
             "AI segmentation (Phase 02)."),
    NodeType("ai_depth", "AI Depth", "AI", ("image",), ("depth",), {"model": "", "metric": False},
             "Monocular depth estimation (Phase 04)."),
    NodeType("track_camera", "Camera Track", "Tracking", ("image", "mask"), ("camera", "points"),
             {"features": 400}, "Camera tracking and solve (Phase 03)."),
    NodeType("track_object", "Object Track", "Tracking", ("image", "mask"), ("transform",), {"mode": "2d"},
             "Object tracking (Phase 03)."),
    NodeType("pose_estimate", "Body Pose", "Tracking", ("image",), ("skeleton",), {"people": 1},
             "Body tracking (Phase 05)."),
    NodeType("refine_matte", "Refine Matte", "Matte", ("image", "mask"), ("mask",), {"edge": "fine"},
             "Matte refinement (Phase 02)."),
    NodeType("merge", "Merge", "Utility", ("a", "b"), ("out",), {"operation": "over"}, "Combine two inputs."),
    NodeType("write_output", "Write Output", "Output", ("in",), (), {"path": "", "format": "exr"},
             "Write results to disk."),
    NodeType("maya_export", "Send to Maya", "Output", ("in",), (), {"undoable": True},
             "Explicit, undoable import into the Maya scene (future phase)."),
]

NODE_TYPES_BY_ID: Dict[str, NodeType] = {t.type_id: t for t in NODE_TYPES}
