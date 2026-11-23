"""Regenerate the shelf icons from the procedural logo (developer utility).

    python scripts/generate_icons.py      # needs PySide2 or PySide6
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from maya_plugin.ui.qt import QtWidgets  # noqa: E402
from maya_plugin.ui.components.logo import logo_pixmap  # noqa: E402


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841 - required for QPixmap
    icons = ROOT / "maya_plugin" / "resources" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    for name, size in (("mayaAIToolkit.png", 32), ("mayaAIToolkit_64.png", 64)):
        logo_pixmap(size).save(str(icons / name))
        print("wrote", icons / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
