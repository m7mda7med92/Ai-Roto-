# Maya 2024 manual test checklist (Phases 01-02)

These checks require real Autodesk Maya 2024 on Windows and have **not** been
run yet. Tick each box and note the result; report failures with the
contents of `%APPDATA%\MayaAIToolkit\logs\`.

Tester: ________  Date: ________  Maya build (`cmds.about(installedVersion=True)`): ________
GPU / driver: ________  Engine Python version: ________

## A. Installation

- [ ] A1. `python scripts\install_maya_plugin.py` prints the `.mod` path and finishes without error.
- [ ] A2. `Documents\maya\modules\MayaAIProductionToolkit.mod` exists; no other `.mod` file changed.
- [ ] A3. `scripts\setup_engine.ps1` creates `.venv_engine` and prints "Engine environment ready".
- [ ] A4. `python scripts\diagnostics.py --live` ends with "no failures".
- [ ] A5. Start Maya 2024: no errors in the Script Editor at startup.
- [ ] A6. A **MayaAIToolkit** shelf tab exists with the **AI Toolkit** button (orange icon). All previously existing shelves and buttons are unchanged.
- [ ] A7. *Windows → Settings/Preferences → Plug-in Manager* lists `MayaAIToolkit.py`; loading it prints no errors and does not open UI.

## B. Required acceptance workflow

Start with a **new scene** containing a cube (`polyCube`), saved to disk, so changes are detectable.

- [ ] B1. Open Maya.
- [ ] B2. Click the **AI Toolkit** shelf button.
- [ ] B3. The toolkit UI appears as a floating, dockable window with the dark theme.
- [ ] B4. Status shows RED "Engine Offline". Click **Start Engine**.
- [ ] B5. Status turns YELLOW "Engine Starting"; Task Manager shows a separate `python.exe` from `.venv_engine` (not `maya.exe`).
- [ ] B6. Within a few seconds Maya connects automatically.
- [ ] B7. Status turns GREEN "Engine Connected". Right panel → Processing Settings shows the engine GPU.
- [ ] B8. Click **Test Processing** (bottom panel).
- [ ] B9. A job appears in the Job Queue with state *Running*.
- [ ] B10. Progress bar and percentage advance smoothly inside Maya.
- [ ] B11. Click **Cancel**.
- [ ] B12. The job state becomes *Cancelled* within ~1 s; progress stops below 100 %.
- [ ] B13. During B8–B12, tumble the viewport, open menus, scrub the time slider: Maya stays responsive (no freezes).
- [ ] B14. Scene title bar shows no unsaved-change marker (`*`); `cmds.file(q=True, modified=True)` returns `False`; Undo history (`Edit → Undo`) contains no toolkit actions.

## C. Engine control

- [ ] C1. **Engine → Health Check** writes a "Health OK" line to *Engine Messages*.
- [ ] C2. **Engine → Disconnect** → RED; engine process still running. **Connect** → GREEN.
- [ ] C3. **Engine → Restart Engine** → YELLOW → GREEN with a new `python.exe` PID.
- [ ] C4. **Engine → Stop Engine** → RED; the engine `python.exe` exits.
- [ ] C5. Kill the engine `python.exe` in Task Manager while connected → status becomes RED within ~6 s; Maya does not freeze or error.
- [ ] C6. Run `scripts\start_engine.bat`, then open the toolkit: it auto-connects (GREEN) to that engine.
- [ ] C7. Rename `.venv_engine` temporarily, reopen the toolkit: status GRAY "Engine Not Configured", UI still fully usable. Restore the folder; **Settings → Save** turns it RED (configured).
- [ ] C8. Start the engine from Maya, then quit Maya: the engine `python.exe` exits within a few seconds.
- [ ] C9. Start the engine from Maya, then end `maya.exe` in Task Manager: the engine exits within ~5 s (parent watchdog).
- [ ] C10. **Pipeline Manager**: start a test job, **Pause** (progress freezes, state *Paused*), **Resume** (continues), **Cancel All**.

## D. UI

- [ ] D1. All 10 sidebar entries open their workspace; no error panel appears.
- [ ] D2. Dock the window into the Maya UI (drag its tab onto the Channel Box area), resize, undock. No errors.
- [ ] D3. **Props** and **Jobs** toggles hide/show the right and bottom panels; splitters resize.
- [ ] D4. Workspace selector presets change panels/modules as named.
- [ ] D5. Close the toolkit and click the shelf button again: one window only, still connected.
- [ ] D6. Node Studio: double-click library items to add nodes, drag output → input to connect, Delete removes, F frames, Save/Load workflow round-trips.
- [ ] D7. Project Manager: Create Project creates a folder with `mait_project.json`; it appears under Recent Projects and in the top bar.
- [ ] D8. Maya Studio → Refresh shows scene name, units, cameras; B14 conditions still hold afterwards.
- [ ] D9. Settings dialog saves; values persist after restarting Maya.
- [ ] D10. Text is readable at 100 % and 150 % Windows display scaling.

## E. Scene safety (repeat B14 after each)

- [ ] E1. Load plug-in / open UI / close UI.
- [ ] E2. Start / connect / health check / stop engine.
- [ ] E3. Create/open project, change settings, toolkit **Save**.
- [ ] E4. Submit, pause, resume, cancel simulated jobs.

## F. Uninstall

- [ ] F1. Close Maya, run `python scripts\install_maya_plugin.py --uninstall`.
- [ ] F2. Start Maya: no toolkit shelf, no errors; all other shelves intact.

## G. Phase 02: models and AI Roto

Prepare a short plate (20-50 frames, e.g. `D:\shots\sh010\plate.####.exr` or a `.mov`) with
one clear subject. Open a toolkit project first (Project Manager) so outputs go to its folders.
Keep the saved cube scene from section B open.

- [ ] G1. `setup_engine.ps1` (without `-WithSam2`) installs numpy + OpenCV; `diagnostics.py` shows "Engine packages (core)" OK.
- [ ] G2. Start Engine. **AI Studio → Model Manager** lists 5 models; "Classical (OpenCV GrabCut, CPU)" shows "Ready (built in)"; Install/Remove are disabled for it.
- [ ] G3. **Roto Studio**: choose the plate, click **Load Plate**. A job appears in the Job Queue; the info line shows frame range and resolution; the viewer shows the first frame; the slider scrubs frames.
- [ ] G4. Left-click the subject: a green point appears and (with "Update mask after each prompt") a blue mask overlay appears within a few seconds. Right-click the background: red point, mask updates.
- [ ] G5. Ctrl+drag (or Box mode) draws a yellow box prompt; Undo Prompt / Clear Prompts work.
- [ ] G6. Set a name and frame range, click **Track Mask**. Progress advances; Maya stays responsive (orbit the viewport during tracking). Cancel works mid-way.
- [ ] G7. After completion `<project>\roto\<name>\<name>.####.png` and `<name>.roto.json` exist; scrubbing shows the tracked mask on every frame.
- [ ] G8. Track again with the same name: a dialog asks before overwriting; **No** leaves the files untouched (check modified times).
- [ ] G9. Optional SAM 2: run `setup_engine.ps1 -WithSam2 -CudaVersion <cuXXX>`; diagnostics shows torch/sam2 and CUDA True. Restart Engine, install **SAM 2.1 Hiera Tiny** (progress shown; Cancel leaves it "Not installed"); then select it in Roto Studio and repeat G4-G7. Note seconds per frame and GPU memory: ________
- [ ] G10. Remove the SAM 2 model: a confirmation appears; afterwards its folder under `%APPDATA%\MayaAIToolkit\models\` is gone.
- [ ] G11. Repeat B14: the Maya scene is unchanged (no new nodes, scene not marked modified) after all Roto Studio work.
