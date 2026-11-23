# Changelog

## 0.2.0 – Phase 02: AI Roto (unreleased)

### Added
- Engine model store and catalog (`config/model_catalog.json`): SAM 2.1 Tiny/Small/Base+/Large and a built-in OpenCV backend. Install as a cancellable job with progress, `.part` downloads, size and optional SHA-256 checks, `installed.json` marker; remove with confirmation. API: `GET /models`, `GET/DELETE /models/{id}`, `POST /models/{id}/install`, `GET /capabilities`.
- Media I/O in the engine: image sequences (`####`, `%04d`, `$F4`, or any member file; EXR/16-bit/float to RGB8) and video files (frames numbered from a start frame, default 1001); JPEG viewer proxies.
- Segmentation backends: SAM 2.1 (image + video predictor, CUDA bfloat16) and an OpenCV GrabCut + optical-flow CPU fallback; backend cache with a GPU inference lock.
- Jobs `media_proxies`, `segment_frame`, `roto_track` (forward/backward/both, grow/shrink, feather, fill holes; PNG mattes + JSON manifest; refuses to overwrite existing mattes unless asked).
- Maya: Roto Studio (viewer with include/exclude points and box prompts, auto mask preview, frame scrubbing, tracking with progress/cancel, overwrite confirmation), Model Manager in AI Studio, installed models in the right panel.
- `setup_engine.ps1 -WithSam2 -CudaVersion`, `requirements_sam2.txt`; diagnostics report engine packages and models.
- 34 new tests (181 total).

### Changed
- Engine requirements now include numpy and OpenCV (headless). Version 0.2.0.

## 0.1.0 – Phase 01 (unreleased)

### Added
- Independent AI engine: stdlib HTTP server on 127.0.0.1, session-token auth, Host-header check, versioned `/api/v1` (health, jobs, cancel, pause, resume, shutdown, job-types), threaded job queue, simulated processor, GPU/CUDA detection, JSON-lines logging, session file, parent-PID watchdog.
- Shared contracts (`JobRequest`, `JobStatus`, `HealthInfo`, error envelopes) with validation and JSON Schemas.
- Maya plugin: connection manager (start/stop/restart/connect/disconnect/health, status colours), job manager, layered config, event bus, structured logging, environment detection, toolkit projects, read-only scene bridge with undo-chunk helper for future scene operations.
- Dockable dark UI: top bar, 10-module sidebar, central workspace, collapsible right panel, bottom job/log panel, settings and project dialogs, interactive Node Studio graph.
- Qt shim supporting PySide2 (Maya 2024) and PySide6 (Maya 2025+).
- Installer (module file, shelf, uninstall), engine setup script, engine start script, diagnostics.
- 147 automated tests (unit, integration with real engine processes, mock-Maya UI acceptance flow).
