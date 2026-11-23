"""Phase 03: Roto Studio guided by the artist's Maya tracking, under MOCK Maya.

A FAKE scene (tests/mocks/maya/cmds.py) holds a tracked camera, a locator and a small mesh that
follow a fast-moving disc. The panel reads them (query-only), projects them onto the plate and the
REAL engine tracks the disc with those guides and no manual clicks. This is not a test inside
Autodesk Maya.
"""
import pytest

pytest.importorskip("cv2")

from helpers import JUMP_HEIGHT, JUMP_WIDTH, iou, jump_center, jump_plate  # noqa: E402
from qt_helpers import pump, pump_until  # noqa: E402
from shared.constants.engine_constants import ConnectionState  # noqa: E402

FOCAL, FILM_W, FILM_H, DEPTH = 35.0, 36.0, 13.5, 10.0  # film aspect == plate aspect (480 x 180)


def world_at(u, v):
    """World point at DEPTH in front of a camera at the origin that projects to plate pixel (u, v)."""
    x = (u / JUMP_WIDTH - 0.5) * FILM_W * DEPTH / FOCAL
    y = (0.5 - v / JUMP_HEIGHT) * FILM_H * DEPTH / FOCAL
    return x, y, -DEPTH


def translate(p):
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, p[0], p[1], p[2], 1.0]


def follow(frame):
    return translate(world_at(*jump_center(int(round(frame)) - 1001)))


def build_scene(cmds):
    cmds.add_node("|shotCam", "camera", lambda f: translate((0, 0, 0)), {
        "focalLength": lambda f: FOCAL, "horizontalFilmAperture": FILM_W / 25.4,
        "verticalFilmAperture": FILM_H / 25.4, "filmFit": 1, "lensSqueezeRatio": 1.0,
        "horizontalFilmOffset": 0.0, "verticalFilmOffset": 0.0})
    cmds.add_node("|subject_loc", "locator", follow)
    half = 30.0 / JUMP_WIDTH * FILM_W * DEPTH / FOCAL  # disc radius (30 px) in world units
    cmds.add_node("|subject_geo", "mesh", follow,
                  vertices=[(x, y, 0.0) for x in (-half, half) for y in (-half, half)])
    cmds.add_node("|tree_loc", "locator", lambda f: translate(world_at(440, 20)))


def test_guided_roto_from_maya_tracking(qapp, toolkit, fake_cmds, tmp_path):
    import cv2
    main = toolkit.main
    app = main.app
    toolkit.resize(1400, 1100)
    app.projects.create_project(str(tmp_path), "GuideShow")
    main.top_bar.start_button.click()
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.CONNECTED, 30)
    build_scene(fake_cmds)
    main.show_module("roto_studio")
    panel = main.panel("roto_studio")
    assert pump_until(qapp, lambda: panel.model_combo.count() >= 1, 10)

    pattern, truth = jump_plate(tmp_path / "footage")
    panel.source_field.line_edit.setText(pattern)
    panel.load_button.click()
    assert pump_until(qapp, lambda: panel.plate is not None, 30), panel.plate_info.text()

    panel.refresh_cameras()
    assert panel.guide_camera.itemText(0) == "|shotCam"  # user cameras before persp/top/front/side
    fake_cmds.SELECTION[:] = ["|subject_loc", "|subject_geo"]
    panel.include_list.add_button.click()
    fake_cmds.SELECTION[:] = ["|tree_loc"]
    panel.exclude_list.add_button.click()
    assert panel._items(panel.include_list) == ["|subject_loc", "|subject_geo"]
    assert panel._items(panel.exclude_list) == ["|tree_loc"]

    panel.read_guides_button.click()
    pump(qapp, 0.1)
    assert sorted(panel.guides) == list(range(1001, 1007)), panel.guide_status.text()
    assert "Guides on 6 of 6 frames" in panel.guide_status.text()
    guide = panel.guides[1004]
    cx, cy = jump_center(3)
    assert abs(guide["points"][0][0] - cx) < 0.5 and abs(guide["points"][0][1] - cy) < 0.5
    assert guide["points"][1][2] == 0  # exclude locator
    x0, y0, x1, y1 = guide["box"]
    assert abs((x0 + x1) / 2 - cx) < 0.5 and abs((x1 - x0) - 60) < 1
    panel.frame_spin.setValue(1003)
    pump(qapp, 0.05)
    assert panel.viewer.has_guide()

    # No clicks: Track Mask starts from the guide on the current frame and uses the rest as guides.
    panel.frame_spin.setValue(1001)
    assert panel.prompts.is_empty() and panel.track_button.isEnabled()
    panel.matte_name.setText("disc_guided")
    panel.direction.setCurrentIndex(1)  # forward
    panel.track_button.click()
    assert pump_until(qapp, lambda: "Wrote 6 mattes" in panel.track_status.text(), 60), panel.track_status.text()
    assert "Guided on 6 frame(s)" in panel.track_status.text()
    out = tmp_path / "GuideShow" / "roto" / "disc_guided"
    for frame, mask in truth.items():
        matte = cv2.imread(str(out / ("disc_guided.%04d.png" % frame)), cv2.IMREAD_UNCHANGED)
        assert iou(matte > 127, mask) > 0.85, frame

    # Reading the tracking never changed the scene (only ls/listRelatives/getAttr/xform queries).
    assert fake_cmds.scene_modifying_calls() == []
    assert not any(name == "currentTime" for name, _a, _k in fake_cmds.CALLS)
    panel.clear_guides_button.click()
    assert panel.guides == {} and not panel.viewer.has_guide()
