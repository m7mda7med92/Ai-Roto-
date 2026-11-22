# Phase 01 completion report

**Scope:** core architecture, Maya UI foundation, independent AI engine,
local API, connection manager, simulated job, installation, tests, docs.
**Status:** implemented and tested *outside* Maya. **Not yet run inside
Autodesk Maya 2024** – this environment has no Maya. Waiting for approval
before Phase 02.

## 1. Environment inspected

| Item | Finding |
| --- | --- |
| Project directory | Empty new repository; no existing files to preserve. |
| Maya | Not available in the build environment (Linux cloud container). |
| Maya 2024 runtime (target) | Python 3.10, PySide2 5.15.2 / Qt 5.15.2 – reproduced with a Python 3.10.20 + PySide2 5.15.2.1 venv for testing. |
| Maya 2025+ runtime | Python 3.11, PySide6 – UI also tested with Python 3.13 + PySide6 6.8.1. |
| GPU | None in the build environment; GPU detection is tested with recorded `nvidia-smi` output. |

## 2. Compatibility concerns and how they are handled

1. **Qt binding changes (PySide2 → PySide6 in Maya 2025).** All Qt imports go through `maya_plugin/ui/qt.py`; tested with both bindings.
2. **Maya's Python environment leaking into the engine.** Maya sets `PYTHONHOME`/`PYTHONPATH`; these are scrubbed before launching the engine, and `maya`/`mayapy`/`mayabatch` are refused as engine interpreters.
3. **Generic top-level package names** (`maya_plugin`, `shared`, `ai_engine`) could collide with other tools on `PYTHONPATH`. Kept as specified; renaming to a single namespaced package is a cheap change if a studio conflict appears.
4. **`MAYA_SHELF_PATH` shelves** are copied into Maya's prefs on exit; the uninstaller removes only our copy.
5. **Corporate HTTP proxies** can intercept `localhost` calls; the client explicitly bypasses proxies.
6. **Windows port hijacking**: the engine binds with `SO_EXCLUSIVEADDRUSE` and port 0 (OS-assigned) by default.
7. **Orphaned engines** after a Maya crash: parent-PID watchdog in the engine.

## 3. Deliverables

| # | Deliverable | Location |
| --- | --- | --- |
| 1–2 | Source code, project structure | repository root (see README) |
| 3 | Maya installation script | `scripts/install_maya_plugin.py` (CLI or drag-and-drop) |
| 4 | Engine setup script | `scripts/setup_engine.ps1` |
| 5 | Shelf launcher | `maya_plugin/resources/shelves/shelf_MayaAIToolkit.mel`, `maya_plugin/launcher.py` |
| 6 | Maya UI | `maya_plugin/ui`, `maya_plugin/modules` |
| 7 | Background engine | `ai_engine/` |
| 8 | Local communication | `maya_plugin/communication`, `ai_engine/server`, `shared/contracts` |
| 9 | Automated test results | §5 below |
| 10 | Maya 2024 manual tests | `docs/MAYA_2024_MANUAL_TEST.md` |
| 11 | Architecture documentation | `docs/ARCHITECTURE.md` |
| 12 | This report | `docs/PHASE_01_REPORT.md` |

Also: `scripts/start_engine.bat`, `scripts/diagnostics.py`, `docs/INSTALLATION.md`, `docs/DEVELOPMENT.md`.

## 4. Feature status

### Implemented and tested (automated, outside Maya)

* **Engine process**: starts independently (`python -m ai_engine.main`), binds only to 127.0.0.1 (other hosts refused, exit code 2), weak tokens refused, port-in-use fails cleanly (exit 3), writes/removes session file, SIGTERM and `/shutdown` give clean exit 0, parent-PID watchdog stops an orphaned engine, token never on the command line.
* **API v1**: `/health` (status, versions, Python, GPU, CUDA, active/queued jobs), `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `/cancel`, `/pause`, `/resume`, `/shutdown`, `/job-types`; structured errors for 400/401/403/404/405/409/413/422.
* **Authentication**: bearer session token, constant-time compare, Host-header check.
* **Job system**: unique IDs, incremental progress, cancellation (running, paused and queued jobs), pause/resume, failure reporting, queue limits, history pruning, shutdown cancels all jobs.
* **Connection manager**: start, stop, restart, connect (to Maya-started or manually started engines), disconnect, health check, periodic health monitor, crash detection, startup-failure reporting, NOT_CONFIGURED/OFFLINE/STARTING/CONNECTED/STOPPING states, non-blocking (`Future`-based) API, engine stopped on Maya exit when Maya started it.
* **Job manager**: submit, monitor (polling), pause/resume/cancel, graceful failure when offline.
* **Configuration** (plugin defaults + user overrides; engine defaults + file + CLI), **JSON serialization**, **error handling**, **project service**, **node graph model**, **installer** (install/uninstall/ownership checks).
* **Diagnostics** script including a live engine round-trip.

### Implemented, tested only with MOCK Maya (offscreen Qt + fake `maya` package) – not tested inside Maya

* Shelf entry point `launcher.show()` → dockable window via `MayaQWidgetDockableMixin` (mocked).
* Full UI: top bar (logo, project menu, workspace selector, engine status/menu, Start Engine, panel toggles, Save, Settings, Help), sidebar with 10 modules, central workspace, collapsible right panel, bottom panel (current task, progress, job queue, activity log, engine messages).
* The **acceptance workflow** (open UI → Start Engine → GREEN → Test Processing → progress updates → Cancel → job stops → event loop stays responsive → no scene-modifying command) passes end-to-end with a real engine process and the fake `maya.cmds` recorder.
* Status colours GREEN/YELLOW/RED/GRAY, button enable/disable rules, settings dialog, project flow, Node Studio editing and save/load, Maya Studio read-only scene query, plug-in command registration, failure containment when the UI or engine fails.
* Tested with PySide2 5.15.2.1 (Maya 2024 binding) and PySide6 6.8.1 (Maya 2025+ binding).

### Implemented but not executed at all

* `scripts/setup_engine.ps1` and `scripts/start_engine.bat` (no Windows/PowerShell in the build environment). Their logic mirrors the tested engine launch path.
* The Maya module file being loaded by real Maya, the shelf loading via `MAYA_SHELF_PATH`, drag-and-drop install, real docking/undocking, `atexit` on Maya quit, `maya.utils.executeInMainThreadWithResult`, Windows-specific code paths (`CREATE_NO_WINDOW`, `SO_EXCLUSIVEADDRUSE`, Windows PID check, `%APPDATA%`/Documents detection).
* GPU detection against a real NVIDIA driver (parser tested with sample output).

### Simulated

* The only job type is `simulated`: it sleeps through N steps, reports progress, honours pause/cancel and can be told to fail. No AI model, no media decoding.
* AI Studio's *Submit Test Job* submits this simulated job.

### Not implemented (by design, later phases)

* AI models, model management/downloads, CUDA inference, `torch` dependency.
* Video/image decoding, viewers (footage, depth, point cloud, skeleton, track error graph are placeholders).
* Camera/object/planar/body tracking, segmentation/roto, depth, reconstruction, lens distortion, retargeting, rotomation, FK/IK.
* Any Maya scene import/export (buttons visible but disabled, labelled with their phase).
* Processing nodes in Node Studio (graph editing only).
* Nuke / 3DEqualizer integration.

## 5. Automated test results

Run on 2026-11-22.

| Suite | Environment | Result |
| --- | --- | --- |
| `tests/unit` (109) | Python 3.10.20 | 109 passed |
| `tests/integration` (23, real engine processes) | Python 3.10.20 | 23 passed |
| `tests/mock_maya` (15, **mock Maya**, offscreen Qt) | Python 3.10.20 + PySide2 5.15.2.1 | 15 passed |
| Full suite | Python 3.10.20 + PySide2 5.15.2.1 | **147 passed** |
| Full suite | Python 3.13.18 + PySide6 6.8.1 | **147 passed** |
| Syntax check | `python3.10 -m compileall` on all code | OK |
| Static check | `pyflakes` | clean except intentional package re-exports |
| `scripts/diagnostics.py --live` | Python 3.13 as engine Python | no failures (start, health, auth rejection, progress + cancel, clean shutdown) |

Coverage of the required test areas: configuration loading ✔, API health ✔,
authentication ✔, job submission ✔, job progress ✔, job cancellation ✔,
engine shutdown ✔, connection failure handling ✔, JSON serialization ✔,
error handling ✔, mock Maya tests ✔.

Reproduce: `python -m pytest` (see `docs/DEVELOPMENT.md`).

## 6. Known limitations / risks to check in Maya

* Real `MayaQWidgetDockableMixin` behaviour (docking, restore) is untested; the window is created with `retain=False`, so Maya will not try to restore it on startup.
* Maya's own Qt style may slightly change widget metrics compared with the offscreen screenshots.
* `atexit` handlers run on normal Maya exit; on a crash the engine watchdog handles cleanup (tested on Linux only).
* Polling at 0.5 s is adequate for progress; a push channel (WebSocket/SSE) can replace it later without changing the UI (events stay the same).

## 7. Proposed Phase 02 (awaiting approval)

AI model management + first real processor (AI segmentation/roto): model
registry and download manager in the engine, CUDA/torch environment setup,
image/video I/O, Roto Studio viewer, mask results written to disk, explicit
import into Maya. No work on Phase 02 has started.
