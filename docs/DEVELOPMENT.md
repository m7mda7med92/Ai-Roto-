# Development

## Layout rules

* `shared/` – stdlib only, Python 3.10 syntax, imported by both processes.
* `maya_plugin/core` and `maya_plugin/communication` – no Qt, no Maya imports.
* `maya_plugin/ui` – import Qt only from `maya_plugin.ui.qt`.
* Only `maya_plugin/core/maya_bridge.py` may call `maya.cmds` for scene data.
* Widgets are updated only from Qt slots connected to `UiBridge` signals.
* New feature areas: a package under `maya_plugin/modules/<name>/` with a
  `panel.py` (`BaseModulePanel` subclass) plus a `ModuleDescriptor` entry in
  `modules/registry.py`. Business logic goes in separate files, heavy work in
  an engine processor.

## Test environments

Maya is not required to run the automated tests.

```bash
python -m pip install -r requirements_dev.txt
python -m pytest                 # unit + integration; mock-Maya UI tests run if PySide2/6 is installed
python -m pytest tests/unit      # fast, no processes
python -m pytest -m process      # real engine subprocesses
python -m pytest -m mock_maya    # Qt UI with the FAKE maya package (offscreen)
```

To mirror Maya 2024's runtime, use Python 3.10 with `PySide2==5.15.2.1`
(the bundled Qt is 5.15.2). For Maya 2025+, use PySide6. Offscreen Qt needs
`QT_QPA_PLATFORM=offscreen` (set automatically by `tests/mock_maya/conftest.py`).

`tests/mocks/maya` is a **fake** `maya` package. It records every
`maya.cmds` call so tests can assert that no scene-modifying command ran.
Passing mock tests do not prove behaviour inside Autodesk Maya; use
`docs/MAYA_2024_MANUAL_TEST.md` for that.

## UI preview without Maya

```bash
python -m maya_plugin.launcher --standalone
```

Set **Settings → Engine Python** to any Python ≥ 3.10 to exercise the engine.

## Reloading inside Maya during development

```python
import maya_plugin.launcher as l; l.reload_toolkit()
```

## Engine by hand

```bash
MAIT_ENGINE_TOKEN=$(python -c "print('x'*40)") python -m ai_engine.main --port 8765 --log-level DEBUG
curl -H "Authorization: Bearer xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" http://127.0.0.1:8765/api/v1/health
```

## Coding conventions

Type hints on public functions, docstrings on public classes/APIs, structured
logging via `get_logger(...)` with `extra={...}`, exceptions converted to
structured errors at layer boundaries, no machine-specific paths (use
`shared.paths`).

## Icons

`maya_plugin/resources/icons/*.png` are generated from `ui/components/logo.py`:
`python scripts/generate_icons.py`.
