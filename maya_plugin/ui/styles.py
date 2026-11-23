"""Dark professional theme (Nuke / Houdini / Resolve inspired).

Colours are defined once in :data:`PALETTE`; widgets refer to roles via
object names / dynamic properties, never hard-coded colours.
"""
from __future__ import annotations

from shared.constants.engine_constants import ConnectionState

PALETTE = {
    "bg0": "#1b1d21",      # window background
    "bg1": "#23262b",      # panels
    "bg2": "#2b2f35",      # inputs / cards
    "bg3": "#343941",      # hover
    "border": "#3a3f47",
    "text": "#d7dae0",
    "text_dim": "#8b929c",
    "accent": "#e8833a",   # toolkit orange
    "accent_hover": "#f39a55",
    "select": "#3d5a80",
    "ok": "#3fb950",
    "warn": "#d29922",
    "error": "#f85149",
    "idle": "#6e7681",
}

STATUS_COLORS = {
    ConnectionState.CONNECTED: PALETTE["ok"],         # GREEN
    ConnectionState.STARTING: PALETTE["warn"],        # YELLOW
    ConnectionState.STOPPING: PALETTE["warn"],        # YELLOW
    ConnectionState.OFFLINE: PALETTE["error"],        # RED
    ConnectionState.NOT_CONFIGURED: PALETTE["idle"],  # GRAY
}

STATUS_LABELS = {
    ConnectionState.CONNECTED: "Engine Connected",
    ConnectionState.STARTING: "Engine Starting",
    ConnectionState.STOPPING: "Engine Stopping",
    ConnectionState.OFFLINE: "Engine Offline",
    ConnectionState.NOT_CONFIGURED: "Engine Not Configured",
}

LOG_LEVEL_COLORS = {"DEBUG": PALETTE["text_dim"], "INFO": PALETTE["text"], "WARNING": PALETTE["warn"],
                    "ERROR": PALETTE["error"], "CRITICAL": PALETTE["error"]}


def stylesheet() -> str:
    p = PALETTE
    return """
QWidget {{ background-color: transparent; color: {text}; font-size: 12px; }}
#MAITRoot, #MAITBottomPanel, QDialog, QStackedWidget, QAbstractScrollArea {{ background-color: {bg0}; }}
QToolTip {{ background-color: {bg2}; color: {text}; border: 1px solid {border}; }}

#MAITTopBar {{ background-color: {bg1}; border-bottom: 1px solid {border}; }}
#MAITTopBar QLabel#AppTitle {{ font-size: 14px; font-weight: 600; color: {text}; background: transparent; }}
#MAITTopBar QLabel#ProjectName {{ color: {text_dim}; background: transparent; }}

#MAITSidebar {{ background-color: {bg1}; border-right: 1px solid {border}; }}
#MAITSidebar QPushButton {{
    background: transparent; border: none; border-left: 3px solid transparent; color: {text_dim};
    padding: 9px 12px; text-align: left; font-size: 12px; border-radius: 0;
}}
#MAITSidebar QPushButton:hover {{ background-color: {bg3}; color: {text}; }}
#MAITSidebar QPushButton:checked {{ background-color: {bg2}; color: {text}; border-left: 3px solid {accent}; }}
#MAITSidebar QLabel#SidebarHeader {{ color: {text_dim}; font-size: 10px; padding: 10px 12px 4px 12px; background: transparent; }}

QFrame#Card {{ background-color: {bg1}; border: 1px solid {border}; border-radius: 4px; }}
QFrame#Card QLabel {{ background: transparent; }}
QLabel#CardTitle {{ font-weight: 600; color: {text}; }}
QLabel#ModuleTitle {{ font-size: 18px; font-weight: 600; color: {text}; }}
QLabel#ModuleSubtitle {{ color: {text_dim}; }}
QLabel#Dim {{ color: {text_dim}; background: transparent; }}
QLabel#PhaseBadge {{ color: {accent}; border: 1px solid {accent}; border-radius: 3px; padding: 1px 6px; font-size: 10px; background: transparent; }}
QFrame#Viewer {{ background-color: #111316; border: 1px dashed {border}; border-radius: 4px; }}
QFrame#Viewer QLabel {{ color: {text_dim}; background: transparent; }}

QPushButton {{ background-color: {bg2}; border: 1px solid {border}; border-radius: 3px; padding: 5px 12px; color: {text}; }}
QPushButton:hover {{ background-color: {bg3}; }}
QPushButton:pressed {{ background-color: {select}; }}
QPushButton:disabled {{ color: {text_dim}; background-color: {bg1}; border-color: {bg2}; }}
QPushButton[accent="true"] {{ background-color: {accent}; border-color: {accent}; color: #1b1d21; font-weight: 600; }}
QPushButton[accent="true"]:hover {{ background-color: {accent_hover}; }}
QPushButton[accent="true"]:disabled {{ background-color: {bg2}; color: {text_dim}; border-color: {border}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 4px 8px; color: {text}; }}
QToolButton:hover {{ background-color: {bg3}; border-color: {border}; }}
QToolButton::menu-indicator {{ image: none; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {bg2}; border: 1px solid {border}; border-radius: 3px; padding: 3px 6px; color: {text};
    selection-background-color: {select};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}
QComboBox QAbstractItemView {{ background-color: {bg2}; selection-background-color: {select}; }}
QCheckBox, QRadioButton {{ background: transparent; }}

QTabWidget::pane {{ border: 1px solid {border}; top: -1px; background: {bg1}; }}
QTabBar::tab {{ background: {bg1}; color: {text_dim}; padding: 6px 12px; border: 1px solid {border}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {bg2}; color: {text}; border-top: 2px solid {accent}; }}
QTabBar::tab:hover {{ color: {text}; }}

QTableWidget, QTreeWidget, QListWidget, QTableView {{
    background-color: {bg1}; alternate-background-color: {bg2}; border: 1px solid {border}; gridline-color: {border};
    selection-background-color: {select};
}}
QHeaderView::section {{ background-color: {bg2}; color: {text_dim}; border: none; border-right: 1px solid {border}; padding: 4px 6px; }}
QProgressBar {{ background-color: {bg2}; border: 1px solid {border}; border-radius: 3px; text-align: center; color: {text}; height: 14px; }}
QProgressBar::chunk {{ background-color: {accent}; border-radius: 2px; }}
QSplitter::handle {{ background-color: {bg0}; }}
QSplitter::handle:hover {{ background-color: {accent}; }}
QScrollBar:vertical {{ background: {bg1}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {bg3}; min-height: 24px; border-radius: 4px; }}
QScrollBar:horizontal {{ background: {bg1}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {bg3}; min-width: 24px; border-radius: 4px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QGroupBox {{ border: 1px solid {border}; border-radius: 4px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {text_dim}; }}
QMenu {{ background-color: {bg2}; border: 1px solid {border}; }}
QMenu::item:selected {{ background-color: {select}; }}
QGraphicsView {{ background-color: #15171a; border: 1px solid {border}; }}
""".format(**p)
