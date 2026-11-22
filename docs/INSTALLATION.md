# Installation (Windows, Maya 2024)

Nothing is installed into Maya's Python, and no existing Maya preference or
shelf is modified. Everything is per user; no administrator rights needed.

## Requirements

* Windows 10/11, Autodesk Maya 2024 (Python 3.10, PySide2 5.15).
* A **standalone** CPython 3.10, 3.11 or 3.12 for the AI engine
  (https://www.python.org/downloads/windows/). Do *not* use Maya's `mayapy.exe`.
* Optional: NVIDIA GPU with a current driver (`nvidia-smi` on PATH). Phase 01
  only detects the GPU; it does not use it.

## 1. Put the toolkit somewhere permanent

Clone or copy the repository to a stable folder, e.g.
`D:\Tools\MayaAIProductionToolkit`. The installer records this location, so
move it only by re-running the installer.

## 2. Install into Maya

Choose one:

* **Command line** (any Python 3.7+):

  ```bat
  python scripts\install_maya_plugin.py
  ```

  Use `--dry-run` to preview, or `--maya-app-dir <folder>` if your
  `MAYA_APP_DIR` is non-standard.

* **Drag and drop**: drag `scripts\install_maya_plugin.py` from Explorer onto
  the Maya viewport. The toolkit shelf appears immediately.

The installer writes exactly two files:

| File | Purpose |
| --- | --- |
| `Documents\maya\modules\MayaAIProductionToolkit.mod` | Adds the toolkit to `PYTHONPATH`, plug-in path, icon path and shelf path |
| `%APPDATA%\MayaAIToolkit\install_info.json` | Remembers where the toolkit is installed |

It refuses to overwrite a `.mod` file it did not create.

## 3. Create the engine environment

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_engine.ps1
```

This finds a standalone Python ≥ 3.10 (`py -3.12/-3.11/-3.10`, then `python`),
rejects Maya interpreters, creates `<toolkit>\.venv_engine`, installs
`requirements_engine.txt` (empty in Phase 01) and verifies that the engine
starts. Options: `-PythonExe C:\Python311\python.exe`, `-VenvDir D:\envs\mait`,
`-Force` (recreate).

If you use a custom `-VenvDir`, set **Settings → Engine Python** to
`<VenvDir>\Scripts\python.exe` in the toolkit.

## 4. Open the toolkit

Restart Maya. A **MayaAIToolkit** shelf tab contains the **AI Toolkit** button.
Alternatively: `cmds.loadPlugin("MayaAIToolkit")` then `cmds.mayaAIToolkit()`.

Click **Start Engine** in the top bar. The status turns YELLOW, then GREEN.

## 5. Check the setup

```bat
python scripts\diagnostics.py          :: static checks
python scripts\diagnostics.py --live   :: also start engine, run+cancel a job, stop it
```

## Starting the engine manually (optional)

```bat
scripts\start_engine.bat
```

The engine prints its port and writes the session file; in Maya use
**Engine → Connect** (or it connects automatically when the toolkit opens).
Stop it with Ctrl+C or **Engine → Stop Engine**.

## Files the toolkit creates at runtime

`%APPDATA%\MayaAIToolkit\` contains `user_settings.json` (your overrides only),
`logs\maya_plugin.jsonl`, `logs\engine.jsonl`, `logs\engine_console.log` and
`runtime\engine_session.json` (while an engine is running).

## Uninstall

1. Close Maya.
2. `python scripts\install_maya_plugin.py --uninstall`
   This removes the `.mod` file, `install_info.json` and Maya's cached copy of
   *our* shelf (`Documents\maya\<version>\prefs\shelves\shelf_MayaAIToolkit.mel`)
   – no other shelf is touched.
3. Optionally delete `%APPDATA%\MayaAIToolkit` (settings and logs), the
   `.venv_engine` folder and the toolkit folder itself.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Status is GRAY "Engine Not Configured" | Run `setup_engine.ps1`, or set Settings → Engine Python. |
| Status goes YELLOW then RED | Read **Engine Messages** (bottom panel) and `logs\engine_console.log`. Run `diagnostics.py --live`. |
| Shelf button missing | Check the `.mod` file exists; run the installer again; or use the drag-and-drop install. |
| Corporate proxy | Not an issue: the toolkit always bypasses proxies for 127.0.0.1. |
| Antivirus blocks `python.exe` listening | Allow the venv's `python.exe` for loopback connections. |
