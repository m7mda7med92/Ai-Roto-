# Phase 03 completion report: roto guided by your Maya tracking

**Scope (chosen 2026-11-23):** the toolkit does not do tracking. You track the shot in Maya
(camera, locators, geometry) and Roto Studio uses that tracking to guide the AI mask.
**Status:** implemented and tested *outside* Maya, with the built-in OpenCV backend and a fake
`maya.cmds`. **Not yet run inside Autodesk Maya 2024 and not run with SAM 2 weights.**
Waiting for review/approval before Phase 04.

## 1. What it does

1. In **Roto Studio → Guide from Maya Tracking** you pick the tracked camera and add the
   locators or geometry that follow the subject (*Subject*), plus anything to keep out (*Exclude*).
2. **Read Tracking from Maya** samples them over the plate's frames (Maya frame = plate frame +
   offset) using query commands only, and projects them onto the plate with Maya's camera model.
3. The viewer shows the guides in cyan on every frame. **Use Guide as Prompt** turns the guide of
   the current frame into prompts; **Track Mask** works without any clicks.
4. While tracking, the engine uses the guides (every frame, or every N frames) to keep the mask on
   the subject. After the track, frames where the mask left the guide are listed for review.

| Your Maya data | Becomes |
| --- | --- |
| Include locator / transform | include point |
| Exclude locator / transform | exclude point |
| Include mesh | box around its projected vertices |
| Exclude mesh | exclude point at its projected centre |

Screenshots (offscreen render under mock Maya, synthetic plate, CPU backend):
[viewer with guide and tracked mask](images/roto_guides_viewer.png) ·
[Guide from Maya Tracking card](images/roto_guides_panel.png).

Details: [ARCHITECTURE.md § Roto guided by Maya tracking](ARCHITECTURE.md#roto-guided-by-maya-tracking-phase-03).

## 2. What is verified and what is not

| Area | Verified here | Not verified (needs your machine) |
| --- | --- | --- |
| Projection math (film fit fill/horizontal/vertical/overscan, film offset, lens squeeze, rotated cameras, behind-camera, off-screen) | Unit tests against hand-computed values | Against real Maya cameras and plates (checklist H3-H4) |
| Reading camera/locators/meshes from the scene | Mock Maya: fake `getAttr(time=)`, `xform(query=True)`, `ls`, `listRelatives` | Real Maya `getAttr -time` on animated/constrained nodes, large meshes |
| Classical backend with guides | A disc jumping 45 px/frame: without guides IoU drops to 0 after frame 2; with guides every frame > 0.85 (points only, box only, both) | Real footage |
| SAM 2 with guides | Code adds guides as extra prompts per frame (`add_new_points_or_box`), reviewed against `sam2==1.1.0` | **Never executed** (no weights/GPU here). Checklist H8 |
| `roto_track` API: guides, guide_interval, guide_source, drift report, validation | Over real HTTP | – |
| Scene safety | Mock test asserts no scene-modifying command and no `currentTime` change during the whole guided workflow | Real Maya (H9) |

### Added during testing: networks that inspect SSL

Your first install showed that pip and the Model Manager could not download through a company
network that re-signs HTTPS. Now the Model Manager trusts the Windows certificate store
(`truststore`), and `setup_engine.ps1` retries pip with the Windows certificate stores
(or `-CaBundle <file>`). Certificate checks stay on. A test proves it: a local HTTPS server signed by
a private "company" CA is refused, then accepted once that CA is trusted. The Windows store path
itself (Windows CryptoAPI through truststore, PowerShell `Cert:` export) can only be checked on
your machine.

## 3. Rules kept

* **No tracking in the toolkit**, per your request. The Camera Tracking module stays a UI
  placeholder, now labelled "Later" instead of "Phase 03".
* **The scene is only read.** No scene command, no time change, no selection change. Mattes are
  still files on disk.
* **No AI packages in Maya's Python.** The projection is plain Python.
* **No overwrite without asking.** Unchanged from Phase 02.

## 4. Test results

```
Python 3.10.20 + PySide2 5.15.2.1 (Maya 2024-like):  201 passed
Python 3.13    + PySide6 6.8.1   (Maya 2025-like):   201 passed
```

New in Phase 03 (20 tests): `tests/unit/test_maya_guides.py`, guide tests in
`tests/unit/test_segmentation.py` and `tests/integration/test_roto_jobs_http.py`,
`tests/mock_maya/test_roto_guides_mock.py`, HTTPS trust tests in `tests/unit/test_model_manager.py`. The mock-Maya tests use a **fake** `maya` package.

## 5. Known limitations

* Deforming meshes (skinned characters) are sampled only through their transform. Use locators
  constrained to the rig (or a proxy mesh parented to a joint) for characters.
* Not modelled in the projection: camera pre/post scale, film roll, pan/zoom (2D pan), lens
  distortion. If your plate is undistorted and the Maya camera matches it, guides line up.
* One subject per track (as in Phase 02). The box from several include meshes is their union.
* Reading the tracking samples every frame with `getAttr -time`; very long shots with many guide
  objects take a few seconds and run on Maya's main thread.

## 6. Manual verification

Section **H** of [MAYA_2024_MANUAL_TEST.md](MAYA_2024_MANUAL_TEST.md).

## 7. Suggested next phase options

* Bring mattes into Maya on request (image plane / file texture on the tracked camera), with undo.
* Several subjects per track, and correction clicks on later frames.
* Lens distortion from your Maya/3DE setup so guides match distorted plates.
