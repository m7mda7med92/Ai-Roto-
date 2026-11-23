# Changelog

## 0.1.0 – Phase 01 (unreleased)

### Added
- Independent AI engine: stdlib HTTP server on 127.0.0.1, session-token auth, Host-header check, versioned `/api/v1` (health, jobs, cancel, pause, resume, shutdown, job-types), threaded job queue, simulated processor, GPU/CUDA detection, JSON-lines logging, session file, parent-PID watchdog.
- Shared contracts (`JobRequest`, `JobStatus`, `HealthInfo`, error envelopes) with validation and JSON Schemas.
- Maya plugin: connection manager (start/stop/restart/connect/disconnect/health, status colours), job manager, layered config, event bus, structured logging, environment detection, toolkit projects, read-only scene bridge with undo-chunk helper for future scene operations.
- Dockable dark UI: top bar, 10-module sidebar, central workspace, collapsible right panel, bottom job/log panel, settings and project dialogs, interactive Node Studio graph.
- Qt shim supporting PySide2 (Maya 2024) and PySide6 (Maya 2025+).
- Installer (module file, shelf, uninstall), engine setup script, engine start script, diagnostics.
- 147 automated tests (unit, integration with real engine processes, mock-Maya UI acceptance flow).
