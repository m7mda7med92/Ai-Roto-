"""Install / uninstall the Maya AI Production Toolkit for Maya (per user, no admin rights).

What it does
------------
1. Writes ``MayaAIProductionToolkit.mod`` into ``<maya app dir>/modules``. The
   module file points Maya at this folder (PYTHONPATH, plug-ins, icons, shelf).
2. Records the install location in ``<user dir>/install_info.json`` so tools
   can find the toolkit later.
3. When run *inside* Maya (drag-and-drop this file onto the viewport, or
   ``exec`` it in the Script Editor) it also loads the toolkit shelf right away.

What it never does
------------------
* It does not touch ``userPrefs.mel``, ``userSetup.py`` or any existing shelf.
* It never overwrites a ``.mod`` file it did not create.
* It does not install anything into Maya's Python.

Usage (outside Maya, any Python 3.7+)::

    python scripts/install_maya_plugin.py                 # install
    python scripts/install_maya_plugin.py --dry-run       # show what would happen
    python scripts/install_maya_plugin.py --uninstall     # remove
    python scripts/install_maya_plugin.py --maya-app-dir "D:/prefs/maya"
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

MODULE_NAME = "MayaAIProductionToolkit"
MOD_FILE_NAME = MODULE_NAME + ".mod"
SHELF_NAME = "MayaAIToolkit"


def toolkit_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_version(root: Path) -> str:
    namespace: dict = {}
    exec((root / "shared" / "version.py").read_text(encoding="utf-8"), namespace)
    return str(namespace["TOOLKIT_VERSION"])


def default_maya_app_dir() -> Path:
    """Maya's per-user folder (``MAYA_APP_DIR``), never a hard-coded machine path."""
    try:
        from maya import cmds  # type: ignore
        return Path(cmds.internalVar(userAppDir=True))
    except Exception:
        pass
    env = os.environ.get("MAYA_APP_DIR")
    if env:
        return Path(env)
    home = Path.home()
    if sys.platform.startswith("win"):
        documents = home / "Documents"
        try:  # honour a relocated "Documents" folder (OneDrive etc.)
            import ctypes.wintypes
            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # CSIDL_PERSONAL
            if buf.value:
                documents = Path(buf.value)
        except Exception:
            pass
        return documents / "maya"
    if sys.platform == "darwin":
        return home / "Library" / "Preferences" / "Autodesk" / "maya"
    return home / "maya"


def user_data_dir() -> Path:
    override = os.environ.get("MAIT_USER_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "MayaAIToolkit"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MayaAIToolkit"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "MayaAIToolkit"


def module_file_text(root: Path, version: str) -> str:
    icons = "maya_plugin/resources/icons" + ("/%B" if sys.platform.startswith("linux") else "")
    # Only module-file syntax, no comments: ":=" paths are relative to the module root.
    return "\n".join([
        "+ %s %s %s" % (MODULE_NAME, version, root.as_posix()),
        "PYTHONPATH += %s" % root.as_posix(),
        "MAYA_PLUG_IN_PATH +:= plug-ins",
        "XBMLANGPATH +:= %s" % icons,
        "MAYA_SHELF_PATH +:= maya_plugin/resources/shelves",
        "",
    ])


def is_managed(path: Path) -> bool:
    """True if the file is a module file for this toolkit (written by this installer)."""
    try:
        first = path.read_text(encoding="utf-8").lstrip().splitlines()[0]
    except (OSError, IndexError):
        return False
    return first.startswith("+ %s " % MODULE_NAME)


def install(maya_app_dir: Path, dry_run: bool = False) -> List[str]:
    root = toolkit_root()
    version = read_version(root)
    actions: List[str] = []
    for required in ("maya_plugin", "shared", "plug-ins", "ai_engine"):
        if not (root / required).is_dir():
            raise SystemExit("Toolkit folder is incomplete: missing %s in %s" % (required, root))
    modules_dir = maya_app_dir / "modules"
    mod_path = modules_dir / MOD_FILE_NAME
    if mod_path.exists() and not is_managed(mod_path):
        raise SystemExit("%s exists and was not created by this installer; refusing to overwrite it." % mod_path)
    actions.append("write module file %s" % mod_path)
    info_path = user_data_dir() / "install_info.json"
    actions.append("record install location in %s" % info_path)
    if dry_run:
        return actions
    modules_dir.mkdir(parents=True, exist_ok=True)
    if mod_path.exists():
        shutil.copy2(str(mod_path), str(mod_path) + ".bak")
        actions.append("backed up previous module file to %s.bak" % mod_path)
    mod_path.write_text(module_file_text(root, version), encoding="utf-8")
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text(json.dumps({
        "toolkit_root": str(root), "version": version, "maya_app_dir": str(maya_app_dir),
        "module_file": str(mod_path), "installed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
    actions += _load_in_running_maya(root)
    return actions


def uninstall(maya_app_dir: Path, dry_run: bool = False) -> List[str]:
    actions: List[str] = []
    mod_path = maya_app_dir / "modules" / MOD_FILE_NAME
    if mod_path.exists():
        if not is_managed(mod_path):
            raise SystemExit("%s was not created by this installer; not removing it." % mod_path)
        actions.append("remove %s" % mod_path)
        if not dry_run:
            mod_path.unlink()
    info_path = user_data_dir() / "install_info.json"
    if info_path.exists():
        actions.append("remove %s" % info_path)
        if not dry_run:
            info_path.unlink()
    # Maya copies shelves it loaded into each version's prefs/shelves folder. Remove only our file.
    for shelf_file in sorted(maya_app_dir.glob("*/prefs/shelves/shelf_%s.mel" % SHELF_NAME)):
        actions.append("remove %s" % shelf_file)
        if not dry_run:
            shelf_file.unlink()
    if not dry_run:
        actions += _unload_in_running_maya()
    if not actions:
        actions.append("nothing to remove")
    return actions


def _load_in_running_maya(root: Path) -> List[str]:
    try:
        from maya import cmds, mel  # type: ignore
    except Exception:
        return ["restart Maya to load the toolkit shelf"]
    actions = []
    if str(root) not in sys.path:
        sys.path.append(str(root))
        actions.append("added toolkit to sys.path for this Maya session")
    icon_dir = (root / "maya_plugin" / "resources" / "icons").as_posix()
    xbm = os.environ.get("XBMLANGPATH", "")
    if icon_dir not in xbm:
        os.environ["XBMLANGPATH"] = xbm + os.pathsep + icon_dir if xbm else icon_dir
    if cmds.shelfLayout(SHELF_NAME, exists=True):
        actions.append("shelf '%s' already loaded" % SHELF_NAME)
    else:
        shelf_file = (root / "maya_plugin" / "resources" / "shelves" / ("shelf_%s.mel" % SHELF_NAME)).as_posix()
        mel.eval('loadNewShelf "%s"' % shelf_file)
        actions.append("loaded shelf '%s'" % SHELF_NAME)
    return actions


def _unload_in_running_maya() -> List[str]:
    try:
        from maya import cmds  # type: ignore
    except Exception:
        return []
    if cmds.shelfLayout(SHELF_NAME, exists=True):
        cmds.deleteUI(SHELF_NAME, layout=True)
        return ["removed shelf tab '%s' from this Maya session" % SHELF_NAME]
    return []


def onMayaDroppedPythonFile(*_args: object) -> None:  # noqa: N802 - Maya drag-and-drop hook
    """Called by Maya when this file is dragged onto the viewport."""
    for line in install(default_maya_app_dir()):
        print("[MayaAIToolkit] " + line)
    print("[MayaAIToolkit] Installed. Click the 'AI Toolkit' button on the MayaAIToolkit shelf.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--maya-app-dir", type=Path, default=None, help="override MAYA_APP_DIR")
    args = parser.parse_args(argv)
    maya_app_dir = args.maya_app_dir or default_maya_app_dir()
    actions = (uninstall if args.uninstall else install)(maya_app_dir, dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    for line in actions:
        print(prefix + line)
    if not args.uninstall and not args.dry_run:
        print("\nDone. Next steps:\n  1. Create the engine environment: scripts\\setup_engine.ps1\n"
              "  2. Start Maya and click 'AI Toolkit' on the MayaAIToolkit shelf.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
