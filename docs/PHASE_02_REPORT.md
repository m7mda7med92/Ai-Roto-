# Phase 02 completion report: AI Roto / segmentation

**Scope (chosen 2026-11-23):** model manager, AI masking with click/box prompts,
mask tracking across frames, matte refinement, mattes written to disk, Roto
Studio viewer in Maya.
**Status:** implemented and tested *outside* Maya with the built-in OpenCV
backend. **Not yet run inside Autodesk Maya 2024, not run with SAM 2 weights and
not run on a GPU.** Waiting for review/approval before Phase 03.

## 1. What is verified and what is not

| Area | Verified here | Not verified (needs your machine) |
| --- | --- | --- |
| Model store: download, size/SHA-256 checks, cancel, remove, busy guard | Yes, against a local HTTP server | Real downloads of SAM 2.1 weights (the build environment's network policy blocks dl.fbaipublicfiles.com) |
| Media: image sequences, 16-bit/float/alpha conversion, video, proxies | Yes (PNG sequences, MJPG video, synthetic arrays) | Real EXR/DPX plates and production codecs (ProRes .mov) |
| Classical backend (GrabCut + optical flow) | Yes: IoU > 0.9 on single frames, > 0.85 over tracked synthetic shots | Real footage quality (expect it to be clearly weaker than SAM 2; it is a fallback) |
| SAM 2.1 backend | Code written against the `sam2==1.1.0` API (source reviewed); import failure handled | **Never executed**: no torch/sam2/weights/GPU here. Treat as untested until checklist G9 passes |
| Engine API + jobs (`model_install`, `media_proxies`, `segment_frame`, `roto_track`) | Yes, over real HTTP, including cancel, validation errors and overwrite refusal | – |
| Roto Studio + Model Manager UI | Yes, under **mock Maya** (fake `maya.cmds`, offscreen Qt, real engine process), PySide2 and PySide6, with real mouse events in the viewer | Inside Maya 2024 (docking, responsiveness while tracking, HiDPI) |
| Scene safety | Mock tests assert no scene-modifying `maya.cmds` call during the whole roto workflow | Real Maya (checklist G11) |
| `setup_engine.ps1 -WithSam2` | Reviewed only (no Windows/PowerShell here) | Running it on Windows |

## 2. How it works (short)

1. **Load Plate** submits `media_proxies`: the engine opens the sequence/video and writes
   JPEG proxies (longest side 1280) to `<project>/cache/roto_proxies/<plate>_<hash>/`.
   Maya only reads those files for display; no pixels travel over HTTP.
2. Clicks in the viewer become prompts in **source pixel** coordinates
   (left = include, right/Shift = exclude, Ctrl-drag or Box mode = box).
3. **Segment Frame** (automatic after each prompt by default) runs `segment_frame` and
   shows the preview matte as a blue overlay.
4. **Track Mask** runs `roto_track` from the prompt frame forward, backward or both
   within the frame range, applies grow/shrink, feather and hole filling, and writes
   `<name>.####.png` (8-bit, source resolution) plus `<name>.roto.json` to
   `<project>/roto/<name>/` or a chosen folder.
5. **Model Manager** (AI Studio) lists the catalog; Install runs a cancellable
   `model_install` job; Remove deletes the weights after a confirmation dialog.

Details: [ARCHITECTURE.md § AI Roto](ARCHITECTURE.md#ai-roto-phase-02).

Screenshots (offscreen render under mock Maya, synthetic plate, CPU backend):
[Roto Studio after tracking](images/roto_studio_tracked.png) ·
[Model Manager](images/ai_studio_model_manager.png).

## 3. Rules from the brief and how they are kept

* **No AI packages in Maya's Python.** numpy/OpenCV/torch/sam2 are installed only in
  `.venv_engine`; the Maya side still uses only the standard library and Qt.
* **Localhost only.** No new listeners; model downloads are outbound HTTPS from the
  engine, only when the artist clicks Install.
* **No automatic scene changes.** Roto Studio never calls a scene-modifying Maya command.
  Mattes are files on disk; bringing them into the scene is left for a later phase as an
  explicit, undoable command.
* **No deleting user files without approval.** Existing mattes are never overwritten
  unless the artist confirms; model removal asks first and only touches the toolkit's
  own model store. Viewer proxies and previews live in the toolkit's cache folders.
* **No hardcoded machine paths.** All paths come from the user dir, the project or the
  artist's choice; the engine requires absolute paths in requests.

## 4. Test results

```
Python 3.10.20 + PySide2 5.15.2.1 (Maya 2024-like):  181 passed
Python 3.13    + PySide6 6.8.1   (Maya 2025-like):   181 passed
scripts/diagnostics.py --live:                      no failures (engine packages core OK, SAM 2 not installed)
```

New in Phase 02 (34 tests): `tests/unit/test_model_manager.py`, `tests/unit/test_media.py`,
`tests/unit/test_segmentation.py`, `tests/integration/test_roto_jobs_http.py`,
`tests/mock_maya/test_roto_studio_mock.py`. The mock-Maya tests use a **fake** `maya`
package and are not tests inside Autodesk Maya.

## 5. Known limitations

* SAM 2 is untested (see §1). The first real run may surface API or memory issues;
  please run checklist G9 and send the engine log if it fails.
* The classical backend is a CPU fallback: fine for high-contrast subjects, weak on
  hair, motion blur, occlusion and similar foreground/background colours.
* One object per track. Multiple objects = several tracks with different names.
* Tracking re-reads frames from the source; long 4K EXR shots are I/O bound.
* SAM 2 tracking stages frames as JPEG (quality 95) in a temp folder because the
  predictor requires it; it needs free disk space roughly equal to the shot as JPEG.
* Mattes are not yet loaded into Maya (image plane / shader); manual spline shapes,
  despill and temporal smoothing are still placeholders.
* EXR plates are converted to display-referred 8-bit with a simple sRGB curve for the
  AI only; output mattes are unaffected by this.

## 6. Manual verification

Section **G** of [MAYA_2024_MANUAL_TEST.md](MAYA_2024_MANUAL_TEST.md) covers Phase 02
inside Maya 2024, including the optional SAM 2 install.

## 7. Suggested next phase options

* Bring mattes into Maya on request (image plane / file texture), with undo.
* SAM 2 refinements: multiple objects per track, correction clicks on later frames
  (re-prompt and re-propagate), keyframe interval.
* Depth estimation or camera tracking, as originally listed.
