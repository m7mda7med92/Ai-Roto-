"""Phase 03: projecting the artist's Maya tracking onto the plate (pure Python, no Maya)."""
import math

import pytest

from maya_plugin.modules.roto_studio import maya_guides as mg
from maya_plugin.modules.roto_studio import roto_requests as rr

W, H = 1920, 1080
IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def translate(x, y, z):
    m = list(IDENTITY)
    m[12:15] = [x, y, z]
    return m


def rot_y(deg, t=(0, 0, 0)):
    """Maya row-vector matrix rotating about Y then translating."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, t[0], t[1], t[2], 1]


def cam(matrix=None, focal=35.0, fit=mg.FIT_FILL, h=36 / 25.4, v=24 / 25.4, **kw):
    return mg.CameraSample(matrix or translate(0, 0, 10), focal, h, v, fit, **kw)


def proj(point, camera, w=W, h=H):
    return mg.project(point, camera, mg.mat_inverse(camera.world_matrix), w, h)


def test_matrix_inverse_round_trip():
    m = rot_y(30, (1, 2, 3))
    inv = mg.mat_inverse(m)
    p = mg.transform_point(mg.transform_point((0.3, -1.2, 4.0), m), inv)
    assert all(abs(a - b) < 1e-9 for a, b in zip(p, (0.3, -1.2, 4.0)))
    with pytest.raises(ValueError):
        mg.mat_inverse([0.0] * 16)


def test_projection_centre_offsets_and_behind_camera():
    c = cam()
    u, v = proj((0, 0, 0), c)
    assert abs(u - W / 2) < 1e-6 and abs(v - H / 2) < 1e-6
    # 36x24 film (1.5) on a 16:9 plate with fill -> horizontal fit: 3.5 mm of 36 mm
    u, v = proj((1, 0, 0), c)
    assert abs(u - (3.5 / 36 + 0.5) * W) < 1e-6
    u, v = proj((0, 1, 0), c)
    assert abs(v - (0.5 - 3.5 / (36 / (W / H))) * H) < 1e-6  # y up in Maya -> smaller row
    assert proj((0, 0, 20), c) is None  # behind the camera


def test_film_fit_modes_and_offset():
    vertical = cam(fit=mg.FIT_VERTICAL)
    u, _ = proj((1, 0, 0), vertical)
    assert abs(u - (3.5 / (24 * W / H) + 0.5) * W) < 1e-6
    overscan = cam(fit=mg.FIT_OVERSCAN)  # film 1.5 < 1.78 -> vertical
    assert abs(proj((1, 0, 0), overscan)[0] - u) < 1e-6
    shifted = cam(h_offset_in=1.0 / 25.4)  # 1 mm film offset moves the image left
    assert proj((0, 0, 0), shifted)[0] < W / 2
    squeezed = cam(lens_squeeze=2.0)
    assert proj((1, 0, 0), squeezed)[0] < proj((1, 0, 0), cam())[0]


def test_rotated_camera_still_projects_to_centre():
    c = cam(rot_y(90, (10, 0, 0)))  # at x=10 looking down -X (towards the origin)
    u, v = proj((0, 0, 0), c)
    assert abs(u - W / 2) < 1e-6 and abs(v - H / 2) < 1e-6


def scene_with(objects, frames=(1, 2, 3)):
    return mg.GuideScene("|cam", {f: cam() for f in frames}, objects)


def test_build_guides_from_locators_mesh_and_excludes():
    loc = mg.GuideObject("|loc", mg.INCLUDE, "point", [(0, 0, 0)],
                         {f: translate(0.5 * (f - 1), 0, 0) for f in (1, 2, 3)})
    cube = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    mesh = mg.GuideObject("|geo", mg.INCLUDE, "mesh", cube, {f: IDENTITY for f in (1, 2, 3)})
    bad = mg.GuideObject("|bad", mg.EXCLUDE, "point", [(0, 0, 0)], {f: translate(-3, 0, 0) for f in (1, 2, 3)})
    guides = mg.build_guides(scene_with([loc, mesh, bad]), [1, 2, 3], W, H)
    assert sorted(guides) == [1, 2, 3]
    g = guides[2]
    assert [p[2] for p in g["points"]] == [1, 0]
    assert g["points"][0][0] > W / 2  # locator moved right
    x0, y0, x1, y1 = g["box"]
    assert x0 < W / 2 < x1 and y0 < H / 2 < y1
    payload = mg.guides_payload(guides)
    assert list(payload) == ["1", "2", "3"]


def test_frame_offset_and_off_screen_frames_are_skipped():
    loc = mg.GuideObject("|loc", mg.INCLUDE, "point", [(0, 0, 0)],
                         {11: IDENTITY, 12: translate(100, 0, 0), 13: translate(0, 0, 50)})
    scene = mg.GuideScene("|cam", {f: cam() for f in (11, 12, 13)}, [loc])
    guides = mg.build_guides(scene, [1, 2, 3, 4], W, H, frame_offset=10)
    assert list(guides) == [1]  # 2: off screen, 3: behind camera, 4: no Maya data


def test_sample_points_limits_vertex_count():
    pts = [(i, 0, 0) for i in range(1000)]
    assert len(mg.sample_points(pts, 100)) <= 100
    assert mg.sample_points([], 10) == []


def test_prompt_state_from_guide_and_guided_track_request():
    state = rr.prompt_state_from_guide(1005, {"points": [[10, 20, 1], [30, 40, 0]], "box": [1, 2, 50, 60]})
    assert state.frame == 1005 and state.is_usable() and state.box == (1, 2, 50, 60)
    assert [p[2] for p in state.points] == [1, 0]
    request = rr.track_request("/p.####.png", 1001, "opencv_grabcut", state, 1001, 1010, "both", {}, "/out",
                               "hero", guides={"1005": {"points": [[1, 2, 1]], "box": None}}, guide_interval=2,
                               guide_source="maya camera cam1")
    assert request.params["guides"]["1005"]["points"] == [[1, 2, 1]]
    assert request.params["guide_interval"] == 2 and "Maya-guided" in request.label
    plain = rr.track_request("/p.####.png", 1001, "opencv_grabcut", state, 1001, 1010, "both", {}, "/out", "hero")
    assert "guides" not in plain.params
