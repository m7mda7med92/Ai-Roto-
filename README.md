# Maya AI Production Toolkit

An AI-assisted VFX and 3D production toolkit that runs **inside Autodesk Maya**,
with all heavy processing in an **independent background engine** reached over a
token-authenticated localhost API.

> **Phase 03 – Roto guided by your Maya tracking.** Track the shot in Maya as usual; Roto
> Studio reads the tracked camera and the locators/geometry on the subject (read only),
> projects them onto the plate and uses them to keep the AI mask locked on. Built on Phase 02
> (Model Manager, click/box prompts, mask tracking, SAM 2.1 + OpenCV CPU fallback). Not yet
> verified inside real Maya or with SAM 2 weights: see [docs/PHASE_03_REPORT.md](docs/PHASE_03_REPORT.md)
> ([Phase 02](docs/PHASE_02_REPORT.md), [Phase 01](docs/PHASE_01_REPORT.md)).

## Quick start (Windows, Maya 2024)

```bat
python scripts\install_maya_plugin.py
powershell -ExecutionPolicy Bypass -File scripts\setup_engine.ps1
```

Optional AI segmentation: `setup_engine.ps1 -WithSam2 -CudaVersion cu124`.

Start Maya → **MayaAIToolkit** shelf → **AI Toolkit** → **Start Engine** → **Roto Studio** →
**Load Plate** → click the subject → **Track Mask**.

Full guide: [docs/INSTALLATION.md](docs/INSTALLATION.md) ·
Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
Development & tests: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) ·
Manual Maya checklist: [docs/MAYA_2024_MANUAL_TEST.md](docs/MAYA_2024_MANUAL_TEST.md)

## Layout

```
maya_plugin/          Maya side (UI, connection/job managers, config, modules)
  core/               config, events, logging, environment, projects, maya_bridge (scene access)
  communication/      engine client, connection manager, job manager (no Qt, no Maya)
  ui/                 Qt shim, theme, main window, panels, dialogs, dock host
  modules/            10 feature modules (Project Manager ... Pipeline Manager)
  resources/          shelf + icons
ai_engine/            independent engine process (HTTP server, auth, job queue, processors,
                      model store, media I/O, segmentation backends)
shared/               wire contracts, constants, JSON schemas, session file (stdlib only)
plug-ins/             Maya plug-in shim (registers the mayaAIToolkit command)
config/               default plugin settings, engine settings, model catalog
scripts/              install, engine setup/start, diagnostics
tests/                unit, integration (real engine processes), mock_maya (fake maya + offscreen Qt)
docs/                 architecture, installation, development, manual tests, phase report
```

## Principles

* The engine never runs in Maya's Python; it has its own venv.
* The engine binds to `127.0.0.1` only and requires a per-session token.
* Maya's main thread never waits on the engine; results reach the UI through Qt signals.
* Nothing modifies the Maya scene unless the user explicitly asks, inside one undo step.
