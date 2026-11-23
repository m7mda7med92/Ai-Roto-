"""Prompts, matte refinement, the classical backend and the backend cache."""
import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ai_engine.models.model_manager import ModelManager  # noqa: E402
from ai_engine.segmentation.backends import (BackendCache, BackendUnavailable, ClassicalBackend,  # noqa: E402
                                             Sam2Backend)
from ai_engine.segmentation.prompts import Prompts  # noqa: E402
from ai_engine.segmentation.refine import RefineSettings, refine_mask  # noqa: E402
from helpers import ROOT, iou, synthetic_plate  # noqa: E402
from shared.contracts.errors import ContractError  # noqa: E402


def test_prompt_validation():
    p = Prompts.from_dict({"points": [[10, 20, 1], [5, 5, 0]], "box": [50, 60, 0, 0]}, 100, 100)
    assert p.positive == [(10.0, 20.0)] and p.negative == [(5.0, 5.0)]
    assert p.box == (0.0, 0.0, 50.0, 60.0)
    for bad in ({"points": [[1, 1, 0]]}, {"points": [[1, 2]]}, {"points": [[1, 1, 2]]}, {"box": [0, 0, 1, 1]},
                {"points": [[1, 1, True]]}, "nope"):
        with pytest.raises(ContractError):
            Prompts.from_dict(bad, 100, 100)
    with pytest.raises(ContractError, match="outside"):
        Prompts.from_dict({"points": [[150, 10, 1]]}, 100, 100)


def test_refine_grow_shrink_feather_fill():
    mask = np.zeros((60, 60), bool)
    mask[20:40, 20:40] = True
    mask[28:32, 28:32] = False  # hole
    base = refine_mask(mask, RefineSettings())
    assert base.dtype == np.uint8 and set(np.unique(base)) == {0, 255}
    assert (refine_mask(mask, RefineSettings(fill_holes=True))[30, 30]) == 255
    assert (refine_mask(mask, RefineSettings(grow_px=3)) > 0).sum() > (base > 0).sum()
    assert (refine_mask(mask, RefineSettings(grow_px=-3)) > 0).sum() < (base > 0).sum()
    soft = refine_mask(mask, RefineSettings(feather_px=4))
    assert len(np.unique(soft)) > 10
    with pytest.raises(ContractError):
        RefineSettings.from_dict({"grow_px": 1.5})


def test_classical_segment_from_point_and_box(tmp_path):
    pattern, truth = synthetic_plate(tmp_path / "seq", count=1)
    image = cv2.cvtColor(cv2.imread(pattern.replace("####", "1001")), cv2.COLOR_BGR2RGB)
    backend = ClassicalBackend()
    assert iou(backend.segment(image, Prompts(points=[(106, 90, 1)])), truth[1001]) > 0.9
    assert iou(backend.segment(image, Prompts(box=(60, 45, 155, 135))), truth[1001]) > 0.9


def test_classical_track_follows_moving_subject(tmp_path):
    pattern, truth = synthetic_plate(tmp_path / "seq", count=6, step=8)
    from ai_engine.media.frame_source import open_source
    masks = {}
    with open_source(pattern) as source:
        ClassicalBackend().track(source.read, source.frames(), 1001, Prompts(points=[(106, 90, 1)]),
                                 masks.__setitem__, lambda: None)
    assert sorted(masks) == list(range(1001, 1007))
    assert min(iou(masks[f], truth[f]) for f in masks) > 0.85


def test_backend_cache_and_sam2_without_dependencies(tmp_path):
    manager = ModelManager(tmp_path / "store", ROOT / "config" / "model_catalog.json")
    cache = BackendCache(manager)
    backend = cache.get("opencv_grabcut")
    assert backend.name == "classical" and cache.get("opencv_grabcut") is backend
    assert cache.loaded() == "opencv_grabcut"
    cache.evict("opencv_grabcut")
    assert cache.loaded() is None
    with pytest.raises(BackendUnavailable, match="not installed"):
        cache.get("sam2.1_hiera_tiny")
    caps = cache.capabilities()
    assert caps["cv2"] and caps["numpy"]
    if not caps["torch"] or not caps["sam2"]:
        with pytest.raises(BackendUnavailable):
            Sam2Backend(tmp_path / "missing.pt", "configs/sam2.1/sam2.1_hiera_t.yaml")


# ------------------------------------------------------------------ Phase 03: Maya guides
def test_guides_validation_and_clipping():
    from ai_engine.segmentation.prompts import clip_to_frame, guides_from_dict
    from shared.contracts.errors import ContractError
    guides = guides_from_dict({"1001": {"points": [[5, 5, 1]]}, "1002": {"box": [0, 0, 20, 20]}})
    assert sorted(guides) == [1001, 1002]
    assert guides_from_dict(None) == {}
    for bad in ({"x": {"points": [[1, 1, 1]]}}, {"1001": {"points": []}}, [1, 2]):
        with pytest.raises(ContractError):
            guides_from_dict(bad)
    clipped = clip_to_frame(Prompts(points=[(5, 5, 1), (500, 5, 1)], box=(-10, -10, 50, 50)), 100, 100)
    assert clipped.points == [(5, 5, 1)] and clipped.box == (0.0, 0.0, 50, 50)
    assert clip_to_frame(Prompts(points=[(500, 5, 1)]), 100, 100) is None


@pytest.mark.parametrize("points,box", [(True, True), (True, False), (False, True)])
def test_maya_guides_keep_classical_track_on_a_fast_subject(tmp_path, points, box):
    from helpers import jump_guides, jump_plate
    from ai_engine.media.frame_source import open_source
    from ai_engine.segmentation.prompts import guides_from_dict
    pattern, truth = jump_plate(tmp_path / "seq")
    start = Prompts(points=[(160, 90, 1)])
    unguided, guided = {}, {}
    with open_source(pattern) as source:
        ClassicalBackend().track(source.read, source.frames(), 1001, start, unguided.__setitem__, lambda: None)
        ClassicalBackend().track(source.read, source.frames(), 1001, start, guided.__setitem__, lambda: None,
                                 guides=guides_from_dict(jump_guides(points=points, box=box)))
    assert min(iou(unguided[f], truth[f]) for f in truth) < 0.5  # flow alone loses the subject
    assert min(iou(guided[f], truth[f]) for f in truth) > 0.85
