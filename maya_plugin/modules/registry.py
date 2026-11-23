"""Module registry: metadata + lazy panel factories.

Panels are imported and built on first use, so a broken module shows an
error placeholder instead of preventing the toolkit from opening.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, List, Optional

PHASE_01_STATUS = "UI foundation (Phase 01)"


@dataclass(frozen=True)
class ModuleDescriptor:
    module_id: str
    title: str
    description: str
    panel_path: str              # "package.module:ClassName"
    status: str = PHASE_01_STATUS

    def load_panel_class(self) -> Any:
        module_name, _, class_name = self.panel_path.partition(":")
        return getattr(importlib.import_module(module_name), class_name)


MODULES: List[ModuleDescriptor] = [
    ModuleDescriptor("project_manager", "Project Manager",
                     "Create and open toolkit projects, browse shots and project paths.",
                     "maya_plugin.modules.project_manager.panel:ProjectManagerPanel",
                     status="Functional (projects on disk)"),
    ModuleDescriptor("ai_studio", "AI Studio", "Model Manager, GPU info and AI job submission.",
                     "maya_plugin.modules.ai_studio.panel:AIStudioPanel",
                     status="Functional (model install/remove; Phase 02)"),
    ModuleDescriptor("camera_tracking", "Camera Tracking", "Camera, lens and sensor setup, tracking and solve.",
                     "maya_plugin.modules.camera_tracking.panel:CameraTrackingPanel"),
    ModuleDescriptor("object_tracking", "Object Tracking", "2D, 3D, planar and multi-object tracking.",
                     "maya_plugin.modules.object_tracking.panel:ObjectTrackingPanel"),
    ModuleDescriptor("body_tracking", "Body Tracking", "AI body pose, rotomation on your rig through your "
                     "tracked camera, FK/IK matching and foot locks.",
                     "maya_plugin.modules.body_tracking.panel:BodyTrackingPanel",
                     status="Functional (rotomation; Phase 04)"),
    ModuleDescriptor("roto_studio", "Roto Studio", "AI and manual masking, mask tracking and matte refinement.",
                     "maya_plugin.modules.roto_studio.panel:RotoStudioPanel",
                     status="Functional (AI masking + tracking, guided by Maya tracking; Phases 02-03). "
                            "Manual shapes: later phase"),
    ModuleDescriptor("depth_geometry", "Depth & Geometry", "Depth maps, point clouds, meshes and ground plane.",
                     "maya_plugin.modules.depth_geometry.panel:DepthGeometryPanel"),
    ModuleDescriptor("node_studio", "Node Studio", "Node-based workflow editor (graph editing foundation).",
                     "maya_plugin.modules.node_studio.panel:NodeStudioPanel",
                     status="Interactive graph editor (no processing nodes yet)"),
    ModuleDescriptor("maya_studio", "Maya Studio", "Scene information and future camera/geometry/animation import.",
                     "maya_plugin.modules.maya_studio.panel:MayaStudioPanel"),
    ModuleDescriptor("pipeline_manager", "Pipeline Manager", "Job queue, status, progress, pause/resume/cancel, logs.",
                     "maya_plugin.modules.pipeline_manager.panel:PipelineManagerPanel",
                     status="Functional (engine job control)"),
]


def get_module(module_id: str) -> Optional[ModuleDescriptor]:
    for descriptor in MODULES:
        if descriptor.module_id == module_id:
            return descriptor
    return None
