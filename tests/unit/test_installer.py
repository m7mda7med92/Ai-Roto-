"""Maya installer: module file creation, ownership checks, uninstall."""
import importlib.util
from pathlib import Path

import pytest

from helpers import ROOT
from shared.version import TOOLKIT_VERSION


@pytest.fixture
def installer():
    spec = importlib.util.spec_from_file_location("install_maya_plugin", ROOT / "scripts" / "install_maya_plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_install_and_uninstall(installer, tmp_path, isolated_user_dir):
    maya_dir = tmp_path / "maya"
    (maya_dir / "modules").mkdir(parents=True)
    other = maya_dir / "modules" / "SomeoneElse.mod"
    other.write_text("+ SomeoneElse 1.0 C:/x\n")
    installer.install(maya_dir)
    mod = maya_dir / "modules" / "MayaAIProductionToolkit.mod"
    text = mod.read_text()
    assert text.startswith(("+ MayaAIProductionToolkit %s " % TOOLKIT_VERSION) + ROOT.as_posix())
    assert "PYTHONPATH += " + ROOT.as_posix() in text
    assert "MAYA_SHELF_PATH +:= maya_plugin/resources/shelves" in text
    assert "#" not in text.splitlines()[0]
    info = isolated_user_dir / "install_info.json"
    assert Path(__import__("json").loads(info.read_text())["toolkit_root"]) == ROOT
    installer.install(maya_dir)  # re-install backs up and overwrites our own file
    assert (maya_dir / "modules" / "MayaAIProductionToolkit.mod.bak").exists()
    prefs_shelf = maya_dir / "2024" / "prefs" / "shelves"
    prefs_shelf.mkdir(parents=True)
    (prefs_shelf / "shelf_MayaAIToolkit.mel").write_text("x")
    (prefs_shelf / "shelf_Custom.mel").write_text("x")
    installer.uninstall(maya_dir)
    assert not mod.exists() and not info.exists()
    assert not (prefs_shelf / "shelf_MayaAIToolkit.mel").exists()
    assert (prefs_shelf / "shelf_Custom.mel").exists() and other.exists()


def test_refuses_to_overwrite_foreign_mod(installer, tmp_path):
    maya_dir = tmp_path / "maya"
    (maya_dir / "modules").mkdir(parents=True)
    foreign = maya_dir / "modules" / "MayaAIProductionToolkit.mod"
    foreign.write_text("+ Other 1.0 C:/x\n")
    with pytest.raises(SystemExit):
        installer.install(maya_dir)
    with pytest.raises(SystemExit):
        installer.uninstall(maya_dir)
    assert foreign.read_text() == "+ Other 1.0 C:/x\n"


def test_dry_run_writes_nothing(installer, tmp_path):
    maya_dir = tmp_path / "maya"
    actions = installer.install(maya_dir, dry_run=True)
    assert actions and not maya_dir.exists()


def test_shelf_file_only_defines_own_shelf():
    shelf = (ROOT / "maya_plugin" / "resources" / "shelves" / "shelf_MayaAIToolkit.mel").read_text()
    assert "global proc shelf_MayaAIToolkit" in shelf
    assert "maya_plugin.launcher" in shelf and "show()" in shelf
    assert shelf.count("shelfButton") == 1
