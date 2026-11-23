# Phase 04 completion report: rotomation onto your own rig

**Scope (chosen 2026-11-23):** body rotomation. The engine finds the actor's 2D joints on the plate,
takes a 3D body model as the starting motion, and fits that motion to **your** rig through **your**
tracked Maya camera. Maya tools then bake it onto your controls and help with cleanup (FK/IK
matching, foot locking). The toolkit still does no camera tracking.
**3D starting motion:** MediaPipe Pose Landmarker (your choice: commercial-friendly code now; the
structure allows adding SAM 3D Body later).
**Status:** implemented and tested *outside* Maya with a fake `maya.cmds`, synthetic shots and the
real MediaPipe model on a real photo. **Not yet run inside Autodesk Maya 2024, and RTMPose / YOLOX
were never executed here** (their download hosts are blocked in the test environment).
Waiting for review/approval before Phase 05.

## 1. What it does

**Body Tracking → Rotomation** panel, top to bottom:

1. **Plate & Subject.** Load the plate (same loader as Roto Studio). Draw a box around the actor
   on any frame, or point at a Roto Studio matte manifest so the box follows the matte.
2. **Body Pose (AI).** Pick the 2D model (RTMPose-m / RTMPose-x, or MediaPipe), the 3D model
   (MediaPipe heavy/full) and optionally the YOLOX person detector. **Track Body** runs on the
   engine from the seed frame forwards then backwards. The viewer shows the 2D joints in green
   (brighter = more confident). Result: `<name>.pose.json`.
3. **Rig & Camera.** Pick your tracked camera, the Maya frame offset and your rig root.
   **Auto-map** fills the 17 body joints from your joint names (tested on Mixamo, AdvancedSkeleton
   and `L_`/`R_` naming). Map your controls for each driver (hips, chest, FK limbs, IK hands/feet,
   poles). Save/load the mapping as a rig preset (`<project>/rotomation/rigs/*.json`).
4. **Fit to Your Camera.** Sends the camera per frame and your rig's joint positions on a
   reference frame. The engine solves the body per frame with your rig's bone lengths so the
   joints reproject onto the 2D detections, then a global pass smooths the motion and locks feet
   that are in contact with the ground. The viewer shows the fitted joints in orange on top of the
   green detections; frames with a large reprojection error are listed. Result:
   `<name>.rotofit.json`.
5. **Apply to Maya.** **Bake** FK, IK or all mapped controls over the range, or **Create Solve
   Locators** (keyed locators at the fitted joints, for your own constraints). Each is one undo step.
   Locked, non-keyable or constraint-driven channels are skipped.
6. **Cleanup Tools.** Per limb: record a match pose once, then **FK → IK** or **IK → FK** on the
   current frame or a range (optionally keys the switch attribute). **Foot lock** holds an IK foot
   at the pose of one frame over a range.

How the fit works: [ARCHITECTURE.md § Rotomation](ARCHITECTURE.md#rotomation-phase-04).

## 2. Model licenses (please confirm with your legal team for commercial work)

| Model | Used for | Code license | Weights / data | Status |
| --- | --- | --- | --- | --- |
| MediaPipe Pose Landmarker (Google) | 3D starting motion and 2D | Apache-2.0 | Model card terms not clearly stated for commercial use | **Included, flagged** |
| RTMPose (OpenMMLab) via rtmlib | 2D joints (Halpe26) | Apache-2.0 | Trained on research datasets (COCO, Halpe, AI Challenger…) | **Included, flagged** |
| YOLOX (OpenMMLab) | person detection | Apache-2.0 | Trained on COCO / HumanArt | **Included, flagged** |
| WHAM | 3D motion | MIT | Needs SMPL body model: **non-commercial** | Not included |
| 4DHumans / HMR 2.0 | 3D motion | MIT | Needs SMPL: **non-commercial** | Not included |
| SAM 3D Body (Meta) | 3D motion | SAM License (appears to allow commercial use) | Gated download, heavy | Later, if you want it |

## 3. What is verified and what is not

| Area | Verified here | Not verified (needs your machine) |
| --- | --- | --- |
| Fit accuracy | Synthetic walk, 200 frames, moving camera, 3 px noise on the 2D joints: mean 3D error 1.3 cm (max 7.8 cm), reprojection 1.3 px, 65 s on CPU | Real footage, real rigs |
| Foot lock | Sliding of planted feet reduced by more than 50% in the synthetic test | Real shots |
| Retarget maths (driver frames, control offsets, poles) | Offsets reproduce the reference pose exactly and follow rigid moves; FK/IK match maths | On a real production rig |
| Auto-mapping | Mixamo, AdvancedSkeleton, custom `L_`/`R_` naming: 17/17 joints | Your rig's naming |
| `pose_track` → `roto_fit` over HTTP | Integration test with a fake pose model | – |
| MediaPipe | Real `pose_landmarker_heavy` on a real photo through `pose_track` | Windows CPU speed |
| RTMPose / YOLOX download and inference | Code written against `rtmlib==0.0.16` | **Never executed** (hosts blocked here). Checklist I3 |
| GPU (ONNX Runtime CUDA) | CPU fallback path | RTX 3090 with `onnxruntime-gpu` (I2) |
| Maya UI flow | Mock Maya: reads are query-only; bake, solve locators, foot lock and FK/IK match each run inside one undo chunk; a locked channel is skipped | Real Maya `bakeResults`, constraints, undo (I8-I12) |
| Scene units | Translate keys are converted from cm to the UI unit | A scene in metres or inches (I13) |
| `setup_engine.ps1 -WithPose` | – | Not run (no PowerShell here) |

## 4. Rules kept

* **No camera tracking in the toolkit.** The fit uses your Maya camera as is.
* **Reading the scene changes nothing.** Every write (bake, locators, foot lock, FK/IK match) is
  an explicit button and one undo step.
* **No AI packages in Maya's Python.** The fit and models run in the engine; Maya-side maths is
  plain Python.
* **No overwrite without asking.** Existing pose/fit files need confirmation.

## 5. Test results

```
Python 3.10 + PySide2 (Maya 2024-like):  225 passed, 1 skipped
Python 3.13 + PySide6 (Maya 2025-like):  225 passed, 1 skipped
```

The skipped test is the optional real-model test; it runs when `MAIT_TEST_MEDIAPIPE_TASK` and
`MAIT_TEST_PERSON_IMAGE` are set and passed here with the real MediaPipe model.
New tests: `tests/unit/test_rotomation_engine.py`, `tests/unit/test_rotomation_maya.py`,
`tests/integration/test_rotomation_http.py`, `tests/integration/test_rotomation_real_models.py`,
`tests/mock_maya/test_rotomation_mock.py`.

## 6. Known limitations

* One actor per shot. Fingers, face and spine bending are not solved (pelvis and chest are rigid
  blocks; there is a chest driver but no separate spine controls).
* The fit runs about 0.3 s per frame on CPU; very long shots are slow (splitting into windows is
  a possible improvement).
* Depth from one camera is ambiguous. The fit uses the 3D model as a prior and tries to avoid
  knee flips, but check fast turns and occlusions in the flagged frames.
* Rig scale comes from your rig's joints on the reference frame. Put the rig in a neutral pose on
  that frame.
* MediaPipe runs on CPU. RTMPose/YOLOX use the GPU when `onnxruntime-gpu` works, else CPU.

## 7. Manual verification

Section **I** of [MAYA_2024_MANUAL_TEST.md](MAYA_2024_MANUAL_TEST.md).

## 8. Suggested next phase options

* SAM 3D Body as a stronger 3D starting motion (gated download, GPU).
* Hands and spine bending; several actors.
* Interactive correction: drag a joint on a frame and refit around it.
