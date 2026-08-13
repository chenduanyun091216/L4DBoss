from __future__ import annotations

import os
import sys
import time
import subprocess
import ctypes
import re
try:
    import winreg
except ImportError:  # pragma: no cover - only relevant on non-Windows tooling
    winreg = None
from html import escape
from collections import Counter
from copy import deepcopy
from pathlib import Path
from threading import Event

from PyQt5.QtCore import QEvent, QObject, QPoint, QRunnable, QSize, QTimer, QUrl, Qt, QThreadPool, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAction, QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QAbstractItemView, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QWidgetAction,
)

from .categories import CATEGORIES, SIMPLE_CATEGORIES, infer_categories, simple_categories
from .collection_sync import delete_collection_folder, restore_collection_files, sync_collection_files
from .models import Mod, ModCollection
from .steam_client import SteamClient
from .storage import AppStorage
from .vpk_scanner import is_conflict_relevant_path, scan_mod_directory

APP_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND_IMAGE = APP_ROOT / "files" / "bg.png"
TITLE_IMAGE = APP_ROOT / "files" / "title.png"
TITLE_ICON = APP_ROOT / "files" / "title.ico"
# Runtime data is kept outside the bundled application so it remains writable
# and survives Nuitka onefile extraction.
USER_DATA_ROOT = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home()))
) / "L4DBoss"
UI_SCALE = 1.0
PREVIEW_CACHE: dict[str, QPixmap] = {}


def ui(value: int) -> int:
    return max(1, round(value * UI_SCALE))


# Active theme (global so programmatically painted widgets can adapt).
ACTIVE_THEME = "dark"

# Programmatically painted colors per theme. Keys are stable identifiers used
# by the custom paintEvent/draw code; each theme maps them to concrete colors.
THEME_PALETTE: dict[str, dict[str, str]] = {
    "dark": {
        "surface": "#0a0e16",
        "panel": "#121826",
        "panel_border": "#5f83b5",
        "tree_default": "#687384",
        "tree_favorite": "#f1c2c7",
        "tree_expand": "#9fb2ce",
        "toggle_off_border": "#4a5d78",
        "toggle_off_fill": "#35445a",
        "toggle_on_border": "#5b8ced",
        "toggle_on_fill": "#2d65d6",
        "toggle_knob": "#f4f8ff",
        "link": "#79a5ff",
    },
    "light": {
        "surface": "#d9e2f1",
        "panel": "#f4f8fd",
        "panel_border": "#7fa6e2",
        "tree_default": "#8a94a6",
        "tree_favorite": "#d9646f",
        "tree_expand": "#5a6a82",
        "toggle_off_border": "#b9c6d8",
        "toggle_off_fill": "#c7d3e4",
        "toggle_on_border": "#7fb0ff",
        "toggle_on_fill": "#2d65d6",
        "toggle_knob": "#ffffff",
        "link": "#2d65d6",
    },
}


def theme_color(key: str, fallback_theme: str = "dark") -> str:
    """Look up a programmatic-paint color for the active theme."""
    palette = THEME_PALETTE.get(ACTIVE_THEME, THEME_PALETTE[fallback_theme])
    return palette.get(key, THEME_PALETTE[fallback_theme][key])


# Ordered list of selectable themes (label shown in the theme switcher).
THEME_ORDER = ["dark", "light"]
THEME_LABELS = {
    "dark": "深渊蓝",
    "light": "晴空白",
}
THEME_HINTS = {
    "dark": "深渊蓝：深色背景，护眼低亮，适合夜间使用",
    "light": "晴空白：浅色背景，明亮清晰，适合白天使用",
}


THEMES = {
    "dark": r"""
    QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"; }
    QMainWindow, QDialog { background: transparent; color: #e8edf5; }
    #appSurface { background: transparent; border-radius: 14px; }
    #header { background: transparent; border-bottom: 1px solid #2a3444; border-top-left-radius: 14px; border-top-right-radius: 14px; }
    #brand { color: #f4f8ff; font-size: 20px; font-weight: 800; letter-spacing: 2px; }
    #brandButton { color: #f4f8ff; background: transparent; border: 0; padding: 0; font-size: 20px; font-weight: 800; letter-spacing: 2px; text-align: left; }
    #brandButton:hover { color: #79a5ff; }
    #brandCredit { color: #8291a8; font-size: 11px; font-weight: 700; }
    #brandSub, #contentSubtitle { color: #8090a8; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
    #headerHint { color: #a9bbd5; font-size: 11px; font-weight: 600; padding-right: 8px; }
    #headerButton, #headerButtonSecondary, #primaryButton, #secondaryButton, #launchButton { background: #273347; color: #d9e4f4; border: 1px solid #38465c; border-radius: 7px; padding: 8px 13px; font-weight: 700; }
    #headerButton:hover, #headerButtonSecondary:hover, #primaryButton:hover, #secondaryButton:hover, #launchButton:hover { background: #3a5378; color: white; border: 2px solid #6aa0ff; }
    #headerIconButton { background: #202c40; border: 0; border-radius: 7px; padding: 0; }
    #headerIconButton:hover { background: #2d65d6; border: 0; }
    QToolTip { color: #eaf2ff; background: #202b3b; border: 1px solid #40516a; border-radius: 5px; padding: 5px 8px; }
    #launchButton:disabled { color: #6a7689; background: #1c2636; border-color: #2a3548; }
    #totalModCount, #activeModCount { background: transparent; border: 0; padding: 0; font-weight: 700; }
    #totalModCount { color: #75a7ff; }
    #activeModCount { color: #48d89a; }
    #totalModCount:hover, #activeModCount:hover { text-decoration: underline; }
    #totalModCount[selected="true"] { border: 1px solid #75a7ff; border-radius: 6px; padding: 3px 6px; }
    #activeModCount[selected="true"] { border: 1px solid #48d89a; border-radius: 6px; padding: 3px 6px; }
    #launchButton { background: #273347; color: #d9e4f4; border: 1px solid #38465c; border-radius: 7px; padding: 8px 13px; font-weight: 700; }
    #launchButton:hover { background: #3a5378; color: white; border: 2px solid #6aa0ff; }
    #launchButton:disabled { color: #6a7689; background: #1c2636; border-color: #2a3548; }
    #sidebar { background: transparent; border-right: 1px solid #283242; }
    #sectionLabel { color: #94a4bc; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
    #categorySwitchLabel { color: #91a0b4; font-size: 11px; font-weight: 700; }
    #sideHint { color: #718097; font-size: 11px; line-height: 1.45; padding: 10px; background: #1c2533; border-radius: 7px; }
    QTreeWidget { background: transparent; border: 0; color: #b8c4d5; outline: none; font-size: 13px; }
    QTreeWidget::item { min-height: 24px; border-radius: 6px; padding: 2px 6px; }
    QTreeWidget::item:hover { background: #212b3a; color: #f2f6fc; }
    QTreeWidget::item:selected { background: #2b5fca; color: white; font-weight: 700; }
    QScrollArea { border: 0; background: transparent; }
    #cardsScroll, #cardsViewport, #cardsHost { background: transparent; }
    #cardsLoadingOverlay { background: rgba(10, 15, 24, 80); }
    #cardsLoadingPanel { background: #18212e; border: 2px solid #5486ec; border-radius: 12px; }
    #cardsLoadingSpinner { color: #78a8ff; font-size: 30px; font-weight: 700; }
    #cardsLoadingLabel { color: #c4d5ee; font-size: 13px; font-weight: 700; }
    QScrollBar:vertical { background: #10151e; width: 8px; margin: 5px 0 5px 0; border-radius: 4px; }
    QScrollBar::handle:vertical { background: #273242; min-height: 42px; border-radius: 4px; }
    QScrollBar::handle:vertical:hover { background: #3a4a60; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QScrollBar:horizontal { background: #10151e; height: 8px; margin: 0 4px 3px 4px; border-radius: 4px; }
    QScrollBar::handle:horizontal { background: #273242; min-width: 42px; border-radius: 4px; }
    QScrollBar::handle:horizontal:hover { background: #3a4a60; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
    #contentTitle { color: #f1f5fb; font-size: 22px; font-weight: 800; }
    QLineEdit, QComboBox { min-height: 32px; background: #19212e; color: #e6edf7; border: 1px solid #2c384a; border-radius: 7px; padding: 0 11px; }
    QLineEdit:focus, QComboBox:focus { border-color: #5486ec; background: #1b2534; }
    #searchInput { min-width: 235px; }
    #collectionCombo { padding-left: 12px; padding-right: 30px; font-weight: 600; }
    #collectionCombo QLineEdit { background: transparent; border: 0; padding: 0; color: #e6edf7; font-weight: 600; }
    #collectionCombo:hover { background: #202b3c; border-color: #3b506e; }
    #collectionCombo::drop-down { subcontrol-origin: padding; subcontrol-position: top right; border: 0; width: 30px; }
    #collectionComboMenu { background: #18212e; color: #dfe9f8; border: 1px solid #3a4a61; border-radius: 8px; outline: 0; padding: 5px; selection-background-color: transparent; }
    #collectionComboMenu::item { min-height: 25px; border: 1px solid transparent; border-radius: 6px; padding: 0 12px; margin: 1px 0; }
    #collectionComboMenu::item:hover { background: #25344a; border-color: #3a5272; color: #ffffff; }
    #collectionComboMenu::item:selected { background: #2d65d6; border-color: #4d83eb; color: #ffffff; font-weight: 700; }
    #collectionComboMenu QScrollBar:vertical { background: transparent; width: 7px; margin: 7px 3px 7px 0; }
    #collectionComboMenu QScrollBar::handle:vertical { background: #3a4b63; min-height: 30px; border-radius: 3px; }
    #collectionComboMenu QScrollBar::handle:vertical:hover { background: #50709a; }
    #modCard, #modCardActive, #modCardConflict { background: #18202c; border: 1px solid #293649; border-radius: 10px; }
    #modCard:hover { background: #1c2634; border: 2px solid #6aa0ff; }
    #modCardActive { border: 2px solid #23c987; background: #12362e; }
    #modCardActive:hover { background: #174538; border: 2px solid #55efad; }
    #modCardConflict { border: 2px solid #ff4757; background: #481923; }
    #modCardConflict:hover { background: #5a1d29; border: 2px solid #ff7885; }
    #modCard[favorite="true"], #modCardActive[favorite="true"], #modCardConflict[favorite="true"] {
        border: 2px solid #f04455;
    }
    #modCard[favorite="true"]:hover, #modCardActive[favorite="true"]:hover, #modCardConflict[favorite="true"]:hover {
        border: 2px solid #ff5a66;
    }
    #preview { background: #111821; border-radius: 7px; min-height: 112px; max-height: 112px; }
    #cardTitle { color: #f2f6fc; font-size: 13px; font-weight: 700; line-height: 1.32; }
    #cardMeta { color: #91a0b4; font-size: 10px; }
    #typeSummary { color: #91a0b4; font-size: 9px; font-weight: 600; padding: 0; }
    #tag, #tagButton { min-height: 20px; max-height: 20px; color: #ffffff; border-radius: 4px; padding: 0 6px; font-size: 9px; font-weight: 700; }
    #cardAction, #cardActionActive { min-height: 24px; max-height: 24px; border-radius: 6px; font-size: 11px; font-weight: 700; }
    #cardAction { color: #cbd7e8; background: #253247; border: 1px solid #34445c; }
    #cardAction:hover { color: white; background: #3c78ee; border: 2px solid #5b8ced; }
    #cardActionActive { color: #d2ffeb; background: #167453; border: 1px solid #2be39a; }
    #cardActionActive:hover { color: white; background: #cf4a55; border: 2px solid #ff7885; }
    #tagButton { border: 0; }
    #tagButton:hover { border: 1px solid #d8e7ff; padding: 0 5px; }
    #favoriteStar { background: transparent; border: none; color: #6c7c93; font-size: 18px; font-weight: 700; padding: 0; }
    #favoriteStar:hover { color: #ff5a6a; }
    #favoriteStar:checked { color: #ff3b4d; text-shadow: 0 0 8px rgba(240, 68, 85, 0.9); }
    #emptyText { color: #9db2d0; background: transparent; border: 0; padding: 0; font-size: 15px; font-weight: 500; line-height: 1.7; letter-spacing: 0.5px; }
    #paginationBar { min-height: 22px; }
    #paginationButton { min-height: 0; max-height: 22px; color: #cbd7e8; background: #253247; border: 1px solid #34445c; border-radius: 5px; padding: 0 9px; font-size: 11px; }
    #paginationButton:hover { color: white; background: #2d65d6; border-color: #2d65d6; }
    #paginationButton:disabled { color: #687384; background: #1b222d; border-color: #2d3747; }
    #pageLabel { color: #91a0b4; min-width: 64px; font-size: 11px; qproperty-alignment: AlignCenter; }
    #steamSyncStatus { background: #1b2a3d; border: 1px solid #355577; border-radius: 7px; }
    #steamSyncLabel { color: #bcd7ff; font-size: 11px; font-weight: 700; }
    #steamSyncProgress { min-height: 6px; max-height: 6px; border: 0; border-radius: 3px; background: #263a54; }
    #steamSyncProgress::chunk { border-radius: 3px; background: #4c86eb; }
    #footer { background: transparent; border-top: 1px solid #283242; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #footer QLabel { color: #9eacc0; padding-right: 12px; }
    #conflictButton { color: #ffabab; background: transparent; border: 0; font-weight: 700; }
    #conflictButton:hover { text-decoration: underline; }
    #conflictButton:disabled { color: #718097; }
    #conflictButton[selected="true"] { border: 1px solid #ff8f99; border-radius: 6px; padding: 3px 6px; }
    #closeButton { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0; border: 0; color: #92a1b6; background: transparent; font-size: 18px; font-weight: 800; }
    #closeButton:hover { color: #ff7a85; background: transparent; }
    #windowControlButton { padding: 0; border: 0; color: #92a1b6; background: transparent; font-size: 16px; font-weight: 700; }
    #windowControlButton:hover { color: #f3f7ff; background: #29364a; border-radius: 5px; }
    #dialogHeader { background: #1b2432; border-bottom: 1px solid #2d3a4d; border-top-left-radius: 14px; border-top-right-radius: 14px; }
    #dialogTitle { color: #f1f5fb; font-size: 17px; font-weight: 800; }
    #modDetailsContent { background: #151d29; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #modDetailsTitle { color: #f4f8ff; font-size: 16px; font-weight: 800; }
    #modDetailsKey { color: #8ca4c6; font-size: 11px; font-weight: 700; }
    #modDetailsValue { color: #e2ebf8; font-size: 11px; }
    #modDetailsDescription { color: #b9c7d9; background: #111822; border: 1px solid #29384d; border-radius: 7px; padding: 9px; font-size: 11px; }
    #contentBackButton { color: #aebfd6; background: transparent; border: 0; padding: 0; }
    #contentBackButton:hover { color: #ffffff; background: rgba(73, 103, 145, 120); border-radius: 5px; }
    #mainDetailsHost, #mainConflictHost { background: transparent; }
    #mainDetailsPreview { background: #111821; border: 1px solid #29384d; border-radius: 9px; }
    #mainDetailsTitle { color: #f3f7fd; font-size: 18px; font-weight: 800; }
    #mainDetailsField { color: #b6c6da; font-size: 12px; padding: 2px 0; }
    #mainDetailsDescription { color: #cad6e7; background: rgba(17, 24, 34, 220); border: 1px solid #29384d; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.55; }
    #steamDetailsLink { min-height: 28px; color: #dceaff; background: #285b9d; border: 1px solid #4b82c8; border-radius: 6px; padding: 0 10px; font-size: 11px; font-weight: 700; }
    #steamDetailsLink:hover { background: #3470bc; color: white; }
    #mainConflictGroup { background: rgba(28, 31, 43, 235); border: 1px solid #a54c5a; border-radius: 10px; }
    #mainConflictGroupTitle { color: #ffc0c7; font-size: 12px; font-weight: 800; }
    #mainConflictGroupReason { color: #d4e1f3; background: #202c3d; border: 1px solid #3e506a; border-radius: 5px; padding: 5px 7px; font-size: 10px; }
    #dialogSubtitle { color: #8596af; font-size: 11px; }
    #aboutContent { background: #121924; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #aboutBrand { color: #f1f5fb; font-size: 25px; font-weight: 800; letter-spacing: 2px; }
    #aboutVersion { color: #7698d9; font-size: 12px; font-weight: 700; }
    #aboutDesigner { color: #aebbd0; font-size: 12px; font-weight: 500; padding: 2px 0; background: transparent; border: 0; }
    #aboutDescription { color: #9eacc0; font-size: 12px; line-height: 1.55; }
    #conflictBody { background: #10141c; border: 0; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #conflictScroll { background: transparent; border: 0; }
    #conflictViewport { background: #10141c; border: 0; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #conflictHost { background: transparent; border: 0; }
    #groupCardScroll, #groupCardsHost { background: #151d29; border: 0; border-radius: 7px; }
    #conflictGroup { background: #151d29; border: 1px solid #303b4d; border-radius: 10px; }
    #conflictGroupLabel { color: #e99aa2; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
    #conflictCard { background: #20202b; border: 1px solid #6d404b; border-radius: 10px; }
    #conflictCard:hover { background: #282333; border-color: #b85866; }
    #conflictCountBadge { color: #fff4f5; background: #b84752; border: 1px solid #ef7d87; border-radius: 12px; font-size: 11px; font-weight: 800; }
    #conflictPreview { background: #111821; border-radius: 7px; min-height: 104px; max-height: 104px; }
    #conflictCaption { color: #f08b96; font-size: 11px; font-weight: 700; }
    #conflictMeta { color: #aebbd0; font-size: 9px; }
    #conflictPeers { color: #abb8c9; font-size: 11px; }
    #conflictPeerButton { max-height: 28px; color: #ffd3d7; background: #3c2730; border: 1px solid #754551; border-radius: 6px; padding: 0 9px; font-size: 11px; font-weight: 700; }
    #conflictPeerButton:hover { color: white; background: #c94a54; border-color: #e26770; }
    #promptSurface { background: #18212e; border: 1px solid #3a4a61; border-radius: 12px; }
    #promptText { color: #e5edf9; font-size: 13px; line-height: 1.5; }
    #promptIconInfo, #promptIconWarning, #promptIconError { color: white; border-radius: 15px; font-size: 16px; font-weight: 800; }
    #promptIconInfo { background: #2d65d6; border: 1px solid #5b8ced; }
    #promptIconWarning { background: #a66d24; border: 1px solid #e5a34a; }
    #promptIconError { background: #a93f4c; border: 1px solid #ed7681; }
    #promptInput { min-height: 34px; background: #111a26; color: #eef5ff; border: 1px solid #3a4a61; border-radius: 7px; padding: 0 10px; }
    #promptInput:focus { background: #162131; border-color: #5486ec; }
    #promptPrimaryButton, #promptSecondaryButton { min-height: 32px; border-radius: 6px; padding: 0 15px; font-weight: 700; }
    #promptPrimaryButton { background: #2d65d6; color: white; border: 1px solid #3d78e7; }
    #promptPrimaryButton:hover { background: #3c78ee; border-color: #6297f3; }
    #promptSecondaryButton { background: #253247; color: #cfdbeb; border: 1px solid #3b4b62; }
    #promptSecondaryButton:hover { background: #30415a; border-color: #526882; }
    QToolTip { background: #202c3d; color: #e9f1ff; border: 1px solid #496282; border-radius: 5px; padding: 6px 8px; font-size: 11px; }
    QMenu { background: #18212e; color: #dfe9f8; border: 1px solid #3a4a61; border-radius: 8px; padding: 5px; }
    QMenu::item { min-height: 29px; border-radius: 5px; padding: 0 26px 0 11px; margin: 1px 0; }
    QMenu::item:selected { background: #2d65d6; color: white; }
    QMenu::item:disabled { color: #718097; background: transparent; }
    QMenu::separator { height: 1px; background: #304057; margin: 5px 8px; }
    QMenu::right-arrow { width: 8px; height: 8px; }
    QMessageBox, QInputDialog, QFileDialog { background: #18212e; color: #e8edf5; }
    QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel { color: #dce6f5; }
    QMessageBox QLabel#qt_msgbox_label { min-width: 280px; line-height: 1.45; }
    QMessageBox QPushButton, QInputDialog QPushButton, QFileDialog QPushButton { min-height: 30px; background: #2d65d6; color: white; border: 1px solid #3d78e7; border-radius: 6px; padding: 0 14px; font-weight: 700; }
    QMessageBox QPushButton:hover, QInputDialog QPushButton:hover, QFileDialog QPushButton:hover { background: #3c78ee; border-color: #6297f3; }
    QMessageBox QPushButton:pressed, QInputDialog QPushButton:pressed, QFileDialog QPushButton:pressed { background: #2455b9; }
    QMessageBox QPushButton[text="取消"], QMessageBox QPushButton[text="否"], QInputDialog QPushButton[text="取消"], QFileDialog QPushButton[text="取消"] { background: #253247; border-color: #3b4b62; color: #cfdbeb; }
    QMessageBox QPushButton[text="取消"]:hover, QMessageBox QPushButton[text="否"]:hover, QInputDialog QPushButton[text="取消"]:hover, QFileDialog QPushButton[text="取消"]:hover { background: #30415a; border-color: #526882; }
    QInputDialog QLineEdit, QFileDialog QLineEdit { min-height: 30px; background: #121a25; color: #edf4ff; border: 1px solid #3a4a61; border-radius: 6px; padding: 0 9px; }
    QInputDialog QLineEdit:focus, QFileDialog QLineEdit:focus { border-color: #5486ec; background: #162131; }
    QFileDialog QTreeView, QFileDialog QListView, QFileDialog QSidebar { background: #121a25; color: #dce6f5; border: 1px solid #303e53; outline: 0; }
    QFileDialog QTreeView::item, QFileDialog QListView::item, QFileDialog QSidebar::item { min-height: 27px; padding: 2px 7px; }
    QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected, QFileDialog QSidebar::item:selected { background: #2d65d6; color: white; }
    QFileDialog QComboBox { min-height: 28px; }
    QStatusBar { background: #151b26; color: #91a0b4; border-top: 1px solid #283242; }
""",
    "light": r"""
    QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"; }
    QMainWindow, QDialog { background: transparent; color: #24334c; }
    #appSurface { background: transparent; border-radius: 14px; }
    #header { background: transparent; border-bottom: 1px solid #b3c0d4; border-top-left-radius: 14px; border-top-right-radius: 14px; }
    #brand { color: #1f2c44; font-size: 20px; font-weight: 800; letter-spacing: 2px; }
    #brandButton { color: #1f2c44; background: transparent; border: 0; padding: 0; font-size: 20px; font-weight: 800; letter-spacing: 2px; text-align: left; }
    #brandButton:hover { color: #2d65d6; }
    #brandCredit { color: #6b7a93; font-size: 11px; font-weight: 700; }
    #brandSub, #contentSubtitle { color: #5d6c85; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
    #headerHint { color: #4c5d78; font-size: 11px; font-weight: 600; padding-right: 8px; }
    #headerButton, #headerButtonSecondary, #primaryButton, #secondaryButton, #launchButton { background: #edf1f9; color: #24334c; border: 1px solid #aab6c8; border-radius: 7px; padding: 8px 13px; font-weight: 700; }
    #headerButton:hover, #headerButtonSecondary:hover, #primaryButton:hover, #secondaryButton:hover, #launchButton:hover { background: #ffffff; color: #0b2a52; border: 2px solid #4d83eb; }
    #headerIconButton { background: #e4e9f2; border: 0; border-radius: 7px; padding: 0; }
    #headerIconButton:hover { background: #2d65d6; border: 0; }
    QToolTip { color: #22334e; background: #ffffff; border: 1px solid #c6d2e2; border-radius: 5px; padding: 5px 8px; }

    #totalModCount, #activeModCount { background: transparent; border: 0; padding: 0; font-weight: 700; }
    #totalModCount { color: #2b5fd0; }
    #activeModCount { color: #0f9e6b; }
    #totalModCount:hover, #activeModCount:hover { text-decoration: underline; }
    #totalModCount[selected="true"] { border: 1px solid #2b5fd0; border-radius: 6px; padding: 3px 6px; }
    #activeModCount[selected="true"] { border: 1px solid #0f9e6b; border-radius: 6px; padding: 3px 6px; }
    #launchButton:disabled { color: #9aa7b8; background: #eef1f6; border-color: #d8dee8; }
    #sidebar { background: transparent; border-right: 1px solid #b3c0d4; }
    #sectionLabel { color: #64748e; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
    #categorySwitchLabel { color: #5c6c86; font-size: 11px; font-weight: 700; }
    #sideHint { color: #5c6b83; font-size: 11px; line-height: 1.45; padding: 10px; background: #e9eef7; border-radius: 7px; }
    QTreeWidget { background: transparent; border: 0; color: #3c4c66; outline: none; font-size: 13px; }
    QTreeWidget::item { min-height: 24px; border-radius: 6px; padding: 2px 6px; }
    QTreeWidget::item:hover { background: #e2e9f7; color: #1c2f4e; }
    QTreeWidget::item:selected { background: #91b9ee; color: #1f4fa3; font-weight: 700; }
    QScrollArea { border: 0; background: transparent; }
    #cardsScroll, #cardsViewport, #cardsHost { background: transparent; }
    #cardsLoadingOverlay { background: rgba(240, 244, 250, 90); }
    #cardsLoadingPanel { background: #ffffff; border: 2px solid #5486ec; border-radius: 12px; }
    #cardsLoadingSpinner { color: #2d65d6; font-size: 30px; font-weight: 700; }
    #cardsLoadingLabel { color: #43536e; font-size: 13px; font-weight: 700; }
    QScrollBar:vertical { background: #dfe5ef; width: 8px; margin: 5px 0 5px 0; border-radius: 4px; }
    QScrollBar::handle:vertical { background: #c2cde0; min-height: 42px; border-radius: 4px; }
    QScrollBar::handle:vertical:hover { background: #9fb0cc; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QScrollBar:horizontal { background: #dfe5ef; height: 8px; margin: 0 4px 3px 4px; border-radius: 4px; }
    QScrollBar::handle:horizontal { background: #c2cde0; min-width: 42px; border-radius: 4px; }
    QScrollBar::handle:horizontal:hover { background: #9fb0cc; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
    #contentTitle { color: #1c2a43; font-size: 22px; font-weight: 800; }
    QLineEdit, QComboBox { min-height: 32px; background: #ffffff; color: #1d2b43; border: 1px solid #aab6c8; border-radius: 7px; padding: 0 11px; }
    QLineEdit:focus, QComboBox:focus { border-color: #2d65d6; background: #ffffff; }
    #searchInput { min-width: 235px; }
    #collectionCombo { padding-left: 12px; padding-right: 30px; font-weight: 600; }
    #collectionCombo QLineEdit { background: transparent; border: 0; padding: 0; color: #1d2b43; font-weight: 600; }
    #collectionCombo:hover { background: #f2f5fb; border-color: #9db6dc; }
    #collectionCombo::drop-down { subcontrol-origin: padding; subcontrol-position: top right; border: 0; width: 30px; }
    #collectionComboMenu { background: #ffffff; color: #22334e; border: 1px solid #aab6c8; border-radius: 8px; outline: 0; padding: 5px; selection-background-color: transparent; }
    #collectionComboMenu::item { min-height: 25px; border: 1px solid transparent; border-radius: 6px; padding: 0 12px; margin: 1px 0; }
    #collectionComboMenu::item:hover { background: #e6edf9; border-color: #a9c1e8; color: #173a6d; }
    #collectionComboMenu::item:selected { background: #2d65d6; border-color: #4d83eb; color: #ffffff; font-weight: 700; }
    #collectionComboMenu QScrollBar:vertical { background: transparent; width: 7px; margin: 7px 3px 7px 0; }
    #collectionComboMenu QScrollBar::handle:vertical { background: #c2cde0; min-height: 30px; border-radius: 3px; }
    #collectionComboMenu QScrollBar::handle:vertical:hover { background: #a3b4d1; }
    #modCard, #modCardActive, #modCardConflict { background: rgba(244, 247, 251, 250); border: 1px solid #9aa7ba; border-radius: 10px; }
    #modCard:hover { background: #ffffff; border: 2px solid #4d83eb; }
    #modCardActive { border: 2px solid #1c4fd0; background: rgba(201, 223, 252, 255); }
    #modCardActive:hover { background: #b9d4fb; border: 2px solid #143da8; }
    #modCardConflict { border: 2px solid #d8363f; background: rgba(250, 211, 216, 255); }
    #modCardConflict:hover { background: #f8c6cc; border: 2px solid #c52832; }
    #modCard[favorite="true"], #modCardActive[favorite="true"], #modCardConflict[favorite="true"] {
        border: 2px solid #d8363f;
    }
    #modCard[favorite="true"]:hover, #modCardActive[favorite="true"]:hover, #modCardConflict[favorite="true"]:hover {
        border: 2px solid #ff4757;
    }
    #preview { background: #eef2f8; border-radius: 7px; min-height: 112px; max-height: 112px; }
    #cardTitle { color: #1d2b43; font-size: 13px; font-weight: 700; line-height: 1.32; }
    #cardMeta { color: #66748c; font-size: 10px; }
    #typeSummary { color: #66748c; font-size: 9px; font-weight: 600; padding: 0; }
    #tag, #tagButton { min-height: 20px; max-height: 20px; color: #ffffff; border-radius: 4px; padding: 0 6px; font-size: 9px; font-weight: 700; }
    #cardAction, #cardActionActive { min-height: 24px; max-height: 24px; border-radius: 6px; font-size: 11px; font-weight: 700; }
    #cardAction { color: #3c4e6b; background: #e9eef7; border: 1px solid #aab6c8; }
    #cardAction:hover { color: white; background: #3c78ee; border: 2px solid #2d65d6; }
    #cardActionActive { color: #0b7a56; background: #d9f5ea; border: 1px solid #2be39a; }
    #cardActionActive:hover { color: white; background: #cf4a55; border: 2px solid #b84752; }
    #tagButton { border: 0; }
    #tagButton:hover { border: 1px solid #7fa6e2; padding: 0 5px; }
    #favoriteStar { background: transparent; border: none; color: #b3bccb; font-size: 18px; font-weight: 700; padding: 0; }
    #favoriteStar:hover { color: #ff5a6a; }
    #favoriteStar:checked { color: #ff3b4d; text-shadow: 0 0 8px rgba(240, 68, 85, 0.85); }
    #emptyText { color: #5f718e; background: transparent; border: 0; padding: 0; font-size: 15px; font-weight: 500; line-height: 1.7; letter-spacing: 0.5px; }
    #paginationBar { min-height: 22px; }
    #paginationButton { min-height: 0; max-height: 22px; color: #3c4e6b; background: #e9eef7; border: 1px solid #aab6c8; border-radius: 5px; padding: 0 9px; font-size: 11px; }
    #paginationButton:hover { color: white; background: #2d65d6; border-color: #2d65d6; }
    #paginationButton:disabled { color: #98a5b8; background: #eef1f6; border-color: #d8dee8; }
    #pageLabel { color: #66748c; min-width: 64px; font-size: 11px; qproperty-alignment: AlignCenter; }
    #steamSyncStatus { background: #e8f0fc; border: 1px solid #9db6d6; border-radius: 7px; }
    #steamSyncLabel { color: #2b5bb8; font-size: 11px; font-weight: 700; }
    #steamSyncProgress { min-height: 6px; max-height: 6px; border: 0; border-radius: 3px; background: #d6e2f5; }
    #steamSyncProgress::chunk { border-radius: 3px; background: #4c86eb; }
    #footer { background: transparent; border-top: 1px solid #9aa7ba; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #footer QLabel { color: #64738b; padding-right: 12px; }
    #conflictButton { color: #d3404d; background: transparent; border: 0; font-weight: 700; }
    #conflictButton:hover { text-decoration: underline; }
    #conflictButton:disabled { color: #9aa7ba; }
    #conflictButton[selected="true"] { border: 1px solid #f07a85; border-radius: 6px; padding: 3px 6px; }
    #closeButton { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0; border: 0; color: #7b8aa1; background: transparent; font-size: 18px; font-weight: 800; }
    #closeButton:hover { color: #ff7a85; background: transparent; }
    #windowControlButton { padding: 0; border: 0; color: #7b8aa1; background: transparent; font-size: 16px; font-weight: 700; }
    #windowControlButton:hover { color: #22334e; background: #dde5f1; border-radius: 5px; }
    #dialogHeader { background: #f0f4fa; border-bottom: 1px solid #b3c0d4; border-top-left-radius: 14px; border-top-right-radius: 14px; }
    #dialogTitle { color: #1c2a43; font-size: 17px; font-weight: 800; }
    #modDetailsContent { background: #ffffff; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #modDetailsTitle { color: #1c2a43; font-size: 16px; font-weight: 800; }
    #modDetailsKey { color: #5f7594; font-size: 11px; font-weight: 700; }
    #modDetailsValue { color: #2a3a54; font-size: 11px; }
    #modDetailsDescription { color: #40506a; background: #f3f6fb; border: 1px solid #b3c0d4; border-radius: 7px; padding: 9px; font-size: 11px; }
    #contentBackButton { color: #5f6f88; background: transparent; border: 0; padding: 0; }
    #contentBackButton:hover { color: #173a6d; background: rgba(45, 101, 214, 30); border-radius: 5px; }
    #mainDetailsHost, #mainConflictHost { background: transparent; }
    #mainDetailsPreview { background: #eef2f8; border: 1px solid #b3c0d4; border-radius: 9px; }
    #mainDetailsTitle { color: #1c2a43; font-size: 18px; font-weight: 800; }
    #mainDetailsField { color: #42536d; font-size: 12px; padding: 2px 0; }
    #mainDetailsDescription { color: #3f4f69; background: #f3f6fb; border: 1px solid #b3c0d4; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.55; }
    #steamDetailsLink { min-height: 28px; color: #ffffff; background: #285b9d; border: 1px solid #4b82c8; border-radius: 6px; padding: 0 10px; font-size: 11px; font-weight: 700; }
    #steamDetailsLink:hover { background: #3470bc; color: white; }
    #mainConflictGroup { background: rgba(255, 250, 251, 242); border: 1px solid #e0a3ab; border-radius: 10px; }
    #mainConflictGroupTitle { color: #c24552; font-size: 12px; font-weight: 800; }
    #mainConflictGroupReason { color: #4a5a73; background: #fbeef0; border: 1px solid #e5b4bb; border-radius: 5px; padding: 5px 7px; font-size: 10px; }
    #dialogSubtitle { color: #64748e; font-size: 11px; }
    #aboutContent { background: #ffffff; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #aboutBrand { color: #1c2a43; font-size: 25px; font-weight: 800; letter-spacing: 2px; }
    #aboutVersion { color: #2b5fd0; font-size: 12px; font-weight: 700; }
    #aboutDesigner { color: #5d6d87; font-size: 12px; font-weight: 500; padding: 2px 0; background: transparent; border: 0; }
    #aboutDescription { color: #5d6c85; font-size: 12px; line-height: 1.55; }
    #conflictBody { background: #ffffff; border: 0; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #conflictScroll { background: transparent; border: 0; }
    #conflictViewport { background: #ffffff; border: 0; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #conflictHost { background: transparent; border: 0; }
    #groupCardScroll, #groupCardsHost { background: #f3f6fb; border: 0; border-radius: 7px; }
    #conflictGroup { background: #ffffff; border: 1px solid #b3c0d4; border-radius: 10px; }
    #conflictGroupLabel { color: #c24552; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
    #conflictCard { background: #fff5f6; border: 1px solid #e2a9b1; border-radius: 10px; }
    #conflictCard:hover { background: #ffecef; border-color: #ef7a86; }
    #conflictCountBadge { color: #fff4f5; background: #b84752; border: 1px solid #ef7d87; border-radius: 12px; font-size: 11px; font-weight: 800; }
    #conflictPreview { background: #eef2f8; border-radius: 7px; min-height: 104px; max-height: 104px; }
    #conflictCaption { color: #c04a56; font-size: 11px; font-weight: 700; }
    #conflictMeta { color: #64748c; font-size: 9px; }
    #conflictPeers { color: #4a5a72; font-size: 11px; }
    #conflictPeerButton { max-height: 28px; color: #9c3440; background: #fbe4e7; border: 1px solid #eba8b0; border-radius: 6px; padding: 0 9px; font-size: 11px; font-weight: 700; }
    #conflictPeerButton:hover { color: white; background: #c94a54; border-color: #e26770; }
    #promptSurface { background: #ffffff; border: 1px solid #aab6c8; border-radius: 12px; }
    #promptText { color: #2a3a54; font-size: 13px; line-height: 1.5; }
    #promptIconInfo, #promptIconWarning, #promptIconError { color: white; border-radius: 15px; font-size: 16px; font-weight: 800; }
    #promptIconInfo { background: #2d65d6; border: 1px solid #5b8ced; }
    #promptIconWarning { background: #a66d24; border: 1px solid #e5a34a; }
    #promptIconError { background: #a93f4c; border: 1px solid #ed7681; }
    #promptInput { min-height: 34px; background: #ffffff; color: #1d2b43; border: 1px solid #aab6c8; border-radius: 7px; padding: 0 10px; }
    #promptInput:focus { background: #ffffff; border-color: #2d65d6; }
    #promptPrimaryButton, #promptSecondaryButton { min-height: 32px; border-radius: 6px; padding: 0 15px; font-weight: 700; }
    #promptPrimaryButton { background: #2d65d6; color: white; border: 1px solid #3d78e7; }
    #promptPrimaryButton:hover { background: #3c78ee; border-color: #6297f3; }
    #promptSecondaryButton { background: #e9eef7; color: #34455f; border: 1px solid #aab6c8; }
    #promptSecondaryButton:hover { background: #ffffff; border-color: #9db6dc; }
    QToolTip { background: #ffffff; color: #22334e; border: 1px solid #c6d2e2; border-radius: 5px; padding: 6px 8px; font-size: 11px; }
    QMenu { background: #ffffff; color: #22334e; border: 1px solid #aab6c8; border-radius: 8px; padding: 5px; }
    QMenu::item { min-height: 29px; border-radius: 5px; padding: 0 26px 0 11px; margin: 1px 0; }
    QMenu::item:selected { background: #2d65d6; color: white; }
    QMenu::item:disabled { color: #9aa7ba; background: transparent; }
    QMenu::separator { height: 1px; background: #c6d0de; margin: 5px 8px; }
    QMenu::right-arrow { width: 8px; height: 8px; }
    QMessageBox, QInputDialog, QFileDialog { background: #ffffff; color: #22334e; }
    QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel { color: #2c3c56; }
    QMessageBox QLabel#qt_msgbox_label { min-width: 280px; line-height: 1.45; }
    QMessageBox QPushButton, QInputDialog QPushButton, QFileDialog QPushButton { min-height: 30px; background: #2d65d6; color: white; border: 1px solid #3d78e7; border-radius: 6px; padding: 0 14px; font-weight: 700; }
    QMessageBox QPushButton:hover, QInputDialog QPushButton:hover, QFileDialog QPushButton:hover { background: #3c78ee; border-color: #6297f3; }
    QMessageBox QPushButton:pressed, QInputDialog QPushButton:pressed, QFileDialog QPushButton:pressed { background: #2455b9; }
    QMessageBox QPushButton[text="取消"], QMessageBox QPushButton[text="否"], QInputDialog QPushButton[text="取消"], QFileDialog QPushButton[text="取消"] { background: #e9eef7; border-color: #aab6c8; color: #34455f; }
    QMessageBox QPushButton[text="取消"]:hover, QMessageBox QPushButton[text="否"]:hover, QInputDialog QPushButton[text="取消"]:hover, QFileDialog QPushButton[text="取消"]:hover { background: #ffffff; border-color: #9db6dc; }
    QInputDialog QLineEdit, QFileDialog QLineEdit { min-height: 30px; background: #ffffff; color: #1d2b43; border: 1px solid #aab6c8; border-radius: 6px; padding: 0 9px; }
    QInputDialog QLineEdit:focus, QFileDialog QLineEdit:focus { border-color: #2d65d6; background: #ffffff; }
    QFileDialog QTreeView, QFileDialog QListView, QFileDialog QSidebar { background: #f5f7fb; color: #2c3c56; border: 1px solid #b3c0d4; outline: 0; }
    QFileDialog QTreeView::item, QFileDialog QListView::item, QFileDialog QSidebar::item { min-height: 27px; padding: 2px 7px; }
    QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected, QFileDialog QSidebar::item:selected { background: #2d65d6; color: white; }
    QFileDialog QComboBox { min-height: 28px; }
    QStatusBar { background: #f0f4fa; color: #64748e; border-top: 1px solid #9aa7ba; }
"""}


class WorkerSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    cancelled = pyqtSignal()


class TaskCancelled(Exception):
    """Signals a cooperative stop for a background task."""


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self.fn(*self.args, **self.kwargs))
        except TaskCancelled:
            self.signals.cancelled.emit()
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class TwoLineElidedLabel(QLabel):
    """A two-line label that always ends overflowing text with an ellipsis."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setWordWrap(False)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._update_text()

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._update_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_text()

    def _update_text(self) -> None:
        if self.width() <= 8:
            return
        metrics = self.fontMetrics()
        available = self.width()
        source = self._full_text.replace("\n", " ").strip()
        first, remainder = self._fit_line(source, available)
        if not remainder:
            super().setText(first)
            return
        second, overflow = self._fit_line(remainder, available)
        if overflow:
            second = metrics.elidedText(second + overflow, Qt.ElideRight, available)
        super().setText(f"{first}\n{second}")

    def _fit_line(self, text: str, width: int) -> tuple[str, str]:
        if not text:
            return "", ""
        metrics = self.fontMetrics()
        end = 0
        for index, _char in enumerate(text, start=1):
            if metrics.horizontalAdvance(text[:index]) > width:
                break
            end = index
        if end == len(text):
            return text, ""
        if end == 0:
            return "", text
        return text[:end].rstrip(), text[end:].lstrip()


def mod_type_tags(mod: Mod) -> list[tuple[str, str]]:
    """Return at most three useful type tags, preferring concrete targets."""
    categories = set(mod.categories)
    tags: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(category: str, label: str, color: str) -> None:
        if category in categories and label not in seen and len(tags) < 3:
            tags.append((label, color))
            seen.add(label)

    gun_labels = {
        "rifle_ak47": "AK-47", "rifle_m16": "M16", "rifle_desert": "SCAR", "rifle_sg552": "SG552",
        "shotgun_pump": "泵动霰弹枪", "shotgun_chrome": "铬合金霰弹枪", "shotgun_auto": "战术霰弹枪",
        "shotgun_spas": "SPAS-12", "smg_uzi": "Uzi", "smg_silenced": "消音冲锋枪", "smg_mp5": "MP5",
        "sniper_hunting": "猎枪", "sniper_military": "军用狙击枪", "sniper_awp": "AWP", "sniper_scout": "Scout",
        "pistol_p220": "P220", "pistol_dual": "双持手枪", "pistol_magnum": "马格南",
        "grenade_launcher": "榴弹发射器", "m60": "M60",
    }
    for category, label in gun_labels.items():
        add(category, label, "#365f9f")
    if not tags:
        for category, label in (("pistol", "手枪"), ("smg", "冲锋枪"), ("rifle", "步枪"), ("shotgun", "霰弹枪"), ("sniper", "狙击枪"), ("weapons", "枪械")):
            add(category, label, "#365f9f")

    melee_labels = {
        "melee_fireaxe": "消防斧", "melee_katana": "武士刀", "melee_machete": "砍刀",
        "melee_frying_pan": "平底锅", "melee_bat": "棒球棍", "melee_cricket_bat": "板球棒",
        "melee_crowbar": "撬棍", "melee_electric_guitar": "电吉他", "melee_golfclub": "高尔夫球杆",
        "melee_pitchfork": "干草叉", "melee_shovel": "铲子", "melee_tonfa": "警棍",
        "melee_chainsaw": "电锯", "melee_knife": "小刀", "melee": "近战武器",
    }
    for category, label in melee_labels.items():
        add(category, label, "#8b5a9f")

    survivor_labels = {
        "bill": "角色 · 比尔", "francis": "角色 · 弗朗西斯", "louis": "角色 · 路易斯", "zoey": "角色 · 佐伊",
        "coach": "角色 · 教练", "ellis": "角色 · 艾利斯", "nick": "角色 · 尼克", "rochelle": "角色 · 罗谢尔",
        "survivors": "角色",
    }
    for category, label in survivor_labels.items():
        add(category, label, "#3b8b78")

    infected_labels = {
        "common_infected": "感染者 · 普通", "boomer": "感染者 · Boomer", "charger": "感染者 · Charger",
        "hunter": "感染者 · Hunter", "jockey": "感染者 · Jockey", "smoker": "感染者 · Smoker",
        "spitter": "感染者 · Spitter", "tank": "感染者 · Tank", "witch": "感染者 · Witch",
        "infected": "感染者",
    }
    for category, label in infected_labels.items():
        add(category, label, "#a15a50")

    if not tags:
        fallback_labels = {
            "campaigns": "地图", "items": "物品", "throwable": "投掷物", "sounds": "声音", "music": "音乐",
            "scripts": "脚本", "ui": "界面", "models": "模型", "textures": "贴图", "miscellaneous": "杂项",
        }
        for category, label in fallback_labels.items():
            add(category, label, "#526073")
    return tags


class ModCard(QFrame):
    clicked = pyqtSignal(str)
    context_requested = pyqtSignal(str, object)
    favorite_toggled = pyqtSignal(str)

    def __init__(self, mod: Mod, collection_names: list[str] | None = None, width: int | None = None):
        super().__init__()
        self.mod = mod
        card_width = width or ui(214)
        self.setObjectName(
            "modCardConflict" if mod.active and mod.conflict_with else ("modCardActive" if mod.active else "modCard")
        )
        self.setCursor(Qt.PointingHandCursor)
        # Card height now hugs the content so there is no large empty gap below
        # the action button. The preview area is enlarged instead.
        self.setFixedSize(card_width, ui(258))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setProperty("favorite", "true" if mod.favorite else "false")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui(10), ui(10), ui(10), ui(8))
        layout.setSpacing(ui(0))
        preview = HoverPreview(mod, card_width - ui(20), ui(112), self)
        preview.setObjectName("preview")
        layout.addWidget(preview)

        title = TwoLineElidedLabel(mod.title or mod.file_name)
        title.setObjectName("cardTitle")
        title.setToolTip(mod.title or mod.file_name)
        # Two Chinese/English title lines need enough line-height to avoid the
        # second line being clipped; metadata then flows beneath the full title.
        # 40px gives two 13px lines comfortable headroom so descenders on the
        # second line are no longer truncated.
        title.setFixedHeight(ui(40))
        layout.addSpacing(ui(2))
        layout.addWidget(title)

        code = mod.workshop_id or Path(mod.file_name).stem
        meta_parts = [code]
        stats = []
        if mod.subscriptions > 0:
            stats.append(f"订阅 {mod.display_subscriptions}")
        if mod.rating > 0:
            stats.append(f"评分 {mod.rating:.1f}")
        if stats:
            meta_parts.extend(stats)
        meta = QLabel("  ·  ".join(meta_parts))
        meta.setObjectName("cardMeta")
        meta.setWordWrap(True)
        meta.setFixedHeight(ui(14))
        layout.addWidget(meta)

        type_labels = [text for text, _color in mod_type_tags(mod)]
        type_summary = QLabel(f"tags: {' '.join(type_labels)}" if type_labels else "tags: -")
        type_summary.setObjectName("typeSummary")
        type_summary.setToolTip("类型标签：" + ("、".join(type_labels) if type_labels else "暂无"))
        type_summary.setFixedHeight(ui(14))
        layout.addWidget(type_summary)

        star = self._build_favorite_star()
        tags_container = QWidget()
        tags_container.setFixedHeight(ui(28))
        tags = QHBoxLayout(tags_container)
        tags.setContentsMargins(0, 0, 0, 0)
        self.tags_layout = tags
        tags.setSpacing(ui(5))
        if mod.active:
            tags.addWidget(make_tag("已启用", "#2d65d6"))
        if mod.conflict_with:
            tags.addWidget(make_tag("存在冲突", "#ff7070"))
        self._add_source_tag(tags)
        tags.addStretch(1)
        tags.addWidget(star)
        layout.addWidget(tags_container)

        # Push the action button to the bottom so it lines up across every card
        # in the same row regardless of how many tag chips are shown above.
        layout.addStretch(1)

        action_row = QHBoxLayout()
        action_row.setSpacing(ui(6))
        button = QPushButton("禁用 Mod" if mod.active else "启用 Mod")
        button.setObjectName("cardActionActive" if mod.active else "cardAction")
        self.toggle_button = button
        button.setFixedHeight(ui(22))
        button.clicked.connect(lambda: self.clicked.emit(self.mod.id))
        action_row.addWidget(button, 1)
        layout.addLayout(action_row)


    def _build_favorite_star(self) -> QPushButton:
        star = QPushButton("★")
        star.setObjectName("favoriteStar")
        star.setCheckable(True)
        star.setChecked(self.mod.favorite)
        star.setToolTip("取消收藏" if self.mod.favorite else "收藏 Mod")
        star.setFixedSize(ui(28), ui(28))
        star.clicked.connect(self._on_favorite_clicked)
        self.favorite_star = star
        return star

    def _on_favorite_clicked(self) -> None:
        # Defer the actual toggle to the window handler so the state is flipped
        # exactly once (it also persists and updates this card's star visual).
        self.favorite_toggled.emit(self.mod.id)

    def set_favorite(self, favorite: bool) -> None:
        changed = favorite != self.mod.favorite
        self.mod.favorite = favorite
        self.setProperty("favorite", "true" if favorite else "false")
        self.favorite_star.setChecked(favorite)
        self.favorite_star.setToolTip("取消收藏" if favorite else "收藏 Mod")
        if changed:
            # Repolishing the whole subtree on every reused card during a
            # refresh is expensive and can cascade style changes under load.
            # Only restyle when the favorite state actually flips.
            self.style().unpolish(self)
            self.style().polish(self)

    def _add_source_tag(self, layout: QHBoxLayout) -> None:
        if self.mod.steam_loaded and self.mod.workshop_id:
            layout.addWidget(make_tag_button("STEAM", "#365f9f", "打开 Steam 创意工坊页面", self.open_workshop_page))
        else:
            layout.addWidget(make_tag_button("本地", "#526073", "打开 Mod 所在文件夹", self.open_mod_folder))

    def open_workshop_page(self) -> None:
        workshop_id = self.mod.workshop_id
        if workshop_id:
            QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"))

    def open_mod_folder(self) -> None:
        folder = Path(self.mod.file_path).parent
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def refresh_state(self) -> None:
        """Refresh only dynamic controls while keeping the preview image alive."""
        self.setObjectName(
            "modCardConflict" if self.mod.active and self.mod.conflict_with else ("modCardActive" if self.mod.active else "modCard")
        )
        # Remove the dynamic tag widgets but never delete the persistent
        # favorite star: it is re-added below and a queued deleteLater on it
        # would free a widget still referenced by the layout (use-after-free
        # crash). Detach everything, drop only the disposable tags, then put
        # the star back.
        detached: list[QWidget] = []
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            if item.widget() is not None:
                detached.append(item.widget())
        for widget in detached:
            if widget is self.favorite_star:
                continue
            widget.deleteLater()
        if self.mod.active:
            self.tags_layout.addWidget(make_tag("已启用", "#2d65d6"))
        if self.mod.conflict_with:
            self.tags_layout.addWidget(make_tag("存在冲突", "#ff7070"))
        self._add_source_tag(self.tags_layout)
        self.tags_layout.addStretch(1)
        if self.favorite_star.parent() is not self.tags_layout:
            self.tags_layout.addWidget(self.favorite_star)
        self.toggle_button.setText("禁用 Mod" if self.mod.active else "启用 Mod")
        self.toggle_button.setObjectName("cardActionActive" if self.mod.active else "cardAction")
        self.set_favorite(self.mod.favorite)
        self.style().unpolish(self)
        self.style().polish(self)
        self.toggle_button.style().unpolish(self.toggle_button)
        self.toggle_button.style().polish(self.toggle_button)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.mod.id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.mod.id, event.globalPos())
        event.accept()


class HoverPreview(QLabel):
    def __init__(self, mod: Mod, width: int, height: int, parent=None):
        super().__init__(parent)
        self.mod = mod
        self.base_width = width
        self.base_height = height
        self._popup: QLabel | None = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(600)
        self._hover_timer.timeout.connect(self._show_large_preview)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(height)
        self.setScaledContents(False)
        self._scaled: QPixmap | None = make_preview_pixmap(mod, width, height)
        self._refresh_preview_pixmap()

    def resizeEvent(self, event) -> None:
        self._refresh_preview_pixmap()
        super().resizeEvent(event)

    def _refresh_preview_pixmap(self) -> None:
        """Compose a blurred fill and clear foreground into one safe pixmap."""
        if self._scaled is not None and not self._scaled.isNull():
            h = self.height()
            w = self.width()
            if w > 0 and h > 0:
                target = QSize(w, h)
                # A small cover image scaled back up makes a soft backdrop,
                # without using native effects or extra child widgets.
                small = self._scaled.scaled(
                    max(1, w // 12), max(1, h // 12),
                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
                )
                backdrop = small.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                foreground = self._scaled.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                composed = QPixmap(target)
                composed.fill(Qt.transparent)
                painter = QPainter(composed)
                painter.drawPixmap((w - backdrop.width()) // 2, (h - backdrop.height()) // 2, backdrop)
                painter.drawPixmap((w - foreground.width()) // 2, (h - foreground.height()) // 2, foreground)
                painter.end()
                self.setPixmap(composed)

    def enterEvent(self, event) -> None:
        self._hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_timer.stop()
        if self._popup is not None:
            self._popup.hide()
        super().leaveEvent(event)

    def _show_large_preview(self) -> None:
        screen = QApplication.desktop().availableGeometry(self)
        max_width = min(ui(560), screen.width() - ui(40))
        max_height = min(ui(420), screen.height() - ui(80))
        pixmap = make_preview_pixmap(self.mod, max_width, max_height)
        if self._popup is None:
            popup = QLabel(None, Qt.Tool | Qt.FramelessWindowHint)
            popup.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
            popup.setAlignment(Qt.AlignCenter)
            popup.setStyleSheet(
                f"background: {theme_color('panel')};"
                f" border: 2px solid {theme_color('panel_border')}; padding: 8px;"
            )
            self._popup = popup
        popup = self._popup
        popup.setPixmap(pixmap)
        popup.adjustSize()

        source_rect = self.rect()
        source_top_left = self.mapToGlobal(source_rect.topLeft())
        source_rect = source_rect.translated(source_top_left - source_rect.topLeft())
        gap = ui(14)
        right_x = source_rect.right() + gap
        left_x = source_rect.left() - popup.width() - gap
        below_y = source_rect.bottom() + gap
        above_y = source_rect.top() - popup.height() - gap
        if right_x + popup.width() <= screen.right() + 1:
            x = right_x
            y = max(screen.top(), min(source_rect.center().y() - popup.height() // 2, screen.bottom() - popup.height() + 1))
        elif left_x >= screen.left():
            x = left_x
            y = max(screen.top(), min(source_rect.center().y() - popup.height() // 2, screen.bottom() - popup.height() + 1))
        elif below_y + popup.height() <= screen.bottom() + 1:
            x = max(screen.left(), min(source_rect.center().x() - popup.width() // 2, screen.right() - popup.width() + 1))
            y = below_y
        elif above_y >= screen.top():
            x = max(screen.left(), min(source_rect.center().x() - popup.width() // 2, screen.right() - popup.width() + 1))
            y = above_y
        else:
            # Only possible when the preview is larger than the remaining
            # screen area; clamp it as a final fallback.
            x = max(screen.left(), min(source_rect.center().x() - popup.width() // 2, screen.right() - popup.width() + 1))
            y = max(screen.top(), min(source_rect.center().y() - popup.height() // 2, screen.bottom() - popup.height() + 1))
        popup.move(x, y)
        popup.show()
        popup.raise_()


class LegacyConflictDialog(QDialog):
    def __init__(self, mods: dict[str, Mod], parent=None):
        super().__init__(parent)
        self.setWindowTitle("冲突详情")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        title = QLabel("以下已启用 Mod 存在 VPK 内部资源路径重叠")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(12)
        for index, mod in enumerate(item for item in mods.values() if item.conflict_with):
            card = QFrame()
            card.setObjectName("conflictCard")
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(QLabel(f"⚠  {mod.title}"))
            peers = [mods[mod_id].title for mod_id in mod.conflict_with if mod_id in mods]
            detail = QLabel("冲突对象：" + " · ".join(peers))
            detail.setWordWrap(True)
            card_layout.addWidget(detail)
            grid.addWidget(card, index // 2, index % 2)
        scroll.setWidget(host)
        layout.addWidget(scroll)


class DragHeader(QFrame):
    """Drag surface for a frameless window."""

    def __init__(self, target: QWidget, parent=None):
        super().__init__(parent)
        self.target = target
        self._drag_offset = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.target.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.target.move(event.globalPos() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.target.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class BackgroundSurface(QWidget):
    """Low-contrast full-window image treatment that keeps controls readable."""

    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self._background = QPixmap(str(image_path)) if image_path.exists() else QPixmap()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme_color("surface")))
        if not self._background.isNull():
            scaled = self._background.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.setOpacity(0.09)
            painter.drawPixmap(x, y, scaled)
        painter.end()


class LegacyConflictCard(QFrame):
    disable_requested = pyqtSignal(str)

    def __init__(self, mod: Mod, peers: list[str]):
        super().__init__()
        self.setObjectName("conflictCard")
        self.setMinimumSize(ui(282), ui(282))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui(12), ui(12), ui(12), ui(12))
        layout.setSpacing(ui(8))
        preview = HoverPreview(mod, ui(188), ui(104), self)
        preview.setObjectName("conflictPreview")
        layout.addWidget(preview)
        title = QLabel(mod.title or mod.file_name)
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        title.setMaximumHeight(ui(42))
        layout.addWidget(title)
        caption = QLabel("与以下 Mod 存在资源路径重叠")
        caption.setObjectName("conflictCaption")
        layout.addWidget(caption)
        peer_text = QLabel(" · ".join(peers))
        peer_text.setObjectName("conflictPeers")
        peer_text.setWordWrap(True)
        peer_text.setVisible(False)
        peer_row = QHBoxLayout()
        peer_row.setSpacing(ui(6))
        for mod_id, peer_title in zip(mod.conflict_with, peers):
            peer = QPushButton(peer_title)
            peer.setObjectName("conflictPeerButton")
            peer.setToolTip("点击禁用此 Mod")
            peer.clicked.connect(lambda _=False, target=mod_id: self.disable_requested.emit(target))
            peer_row.addWidget(peer)
        peer_row.addStretch(1)
        layout.addLayout(peer_row)


class ConflictCard(QFrame):
    """Compact conflict item. Double-clicking it disables the represented Mod."""
    disable_requested = pyqtSignal(str)

    def __init__(self, mod: Mod, width: int | None = None):
        super().__init__()
        self.mod = mod
        card_width = width or ui(208)
        self.setObjectName("conflictCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(card_width, ui(250))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui(10), ui(10), ui(10), ui(10))
        layout.setSpacing(ui(5))
        preview = HoverPreview(mod, card_width - ui(20), ui(78), self)
        preview.setObjectName("conflictPreview")
        layout.addWidget(preview)
        title = TwoLineElidedLabel(mod.title or mod.file_name)
        title.setObjectName("cardTitle")
        title.setToolTip(mod.title or mod.file_name)
        title.setFixedHeight(ui(32))
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(ui(6))
        title_row.addWidget(title, 1)
        conflict_count = len(mod.conflict_with)
        count_badge = QLabel(str(conflict_count))
        count_badge.setObjectName("conflictCountBadge")
        count_badge.setFixedSize(ui(24), ui(24))
        count_badge.setAlignment(Qt.AlignCenter)
        count_badge.setToolTip(f"Conflicts with {conflict_count} enabled Mod(s)")
        title_row.addWidget(count_badge, 0, Qt.AlignTop)
        layout.addLayout(title_row)
        code = mod.workshop_id or Path(mod.file_name).stem
        details = [f"WORKSHOP {code}"]
        if mod.subscriptions > 0:
            details.append(f"订阅 {mod.display_subscriptions}")
        meta = QLabel("  ·  ".join(details))
        meta.setObjectName("conflictMeta")
        meta.setToolTip(f"Workshop ID: {code}")
        layout.addWidget(meta)
        tags = QHBoxLayout()
        tags.setSpacing(ui(5))
        tags.addWidget(make_tag("冲突", "#b84752"))
        tags.addWidget(make_tag_button("STEAM" if mod.steam_loaded and mod.workshop_id else "本地", "#365f9f" if mod.steam_loaded and mod.workshop_id else "#526073", "打开来源", self.open_source))
        tags.addStretch(1)
        layout.addLayout(tags)
        hint = QLabel("双击卡片即可禁用")
        hint.setObjectName("conflictCaption")
        layout.addWidget(hint)

    def open_source(self) -> None:
        if self.mod.steam_loaded and self.mod.workshop_id:
            QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={self.mod.workshop_id}"))
        else:
            folder = Path(self.mod.file_path).parent
            if folder.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.disable_requested.emit(self.mod.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ConflictDialog(QDialog):
    disable_requested = pyqtSignal(str)

    @staticmethod
    def _conflict_groups(mods: dict[str, Mod]) -> list[list[Mod]]:
        """Return connected components of the active conflict graph."""
        remaining = {mod_id for mod_id, mod in mods.items() if mod.active and mod.conflict_with}
        groups: list[list[Mod]] = []
        while remaining:
            pending = [remaining.pop()]
            component: set[str] = set()
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                for peer in mods[current].conflict_with:
                    if peer in remaining:
                        remaining.remove(peer)
                        pending.append(peer)
            groups.append(
                sorted(
                    (mods[mod_id] for mod_id in component),
                    key=lambda mod: (-len(mod.conflict_with), mod.title.casefold()),
                )
            )
        return sorted(
            groups,
            key=lambda group: (
                -sum(len(mod.conflict_with) for mod in group),
                -len(group),
                group[0].title.casefold(),
            ),
        )

    @staticmethod
    def _group_label(index: int, group: list[Mod]) -> str:
        names = {
            "rifle_ak47": "AK-47", "rifle_m16": "M16", "rifle_desert": "SCAR",
            "rifle_sg552": "SG552", "shotgun_pump": "Pump Shotgun",
            "shotgun_chrome": "Chrome Shotgun", "shotgun_auto": "Auto Shotgun",
            "shotgun_spas": "SPAS-12", "smg_uzi": "Uzi", "smg_silenced": "Silenced SMG",
            "smg_mp5": "MP5", "sniper_awp": "AWP", "sniper_scout": "Scout",
            "sniper_hunting": "Hunting Rifle", "sniper_military": "Military Sniper",
            "pistol_magnum": "Magnum", "pistol_p220": "P220", "m60": "M60", "melee": "近战武器",
        }
        counts = Counter(category for mod in group for category in mod.categories if category in names)
        summary = " / ".join(names[category] for category, _count in counts.most_common(2)) or "资源文件"
        return f"冲突组 {index:02d}  ·  {summary}  ·  {len(group)} 个 Mod"

    def __init__(self, mods: dict[str, Mod], parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        # Four fixed-width cards plus their gaps and section margins need roughly
        # 960 logical pixels.  Keep the dialog wide enough so cards never require
        # a per-group horizontal scrollbar.
        self.resize(ui(980), ui(650))
        self.setMinimumSize(ui(960), ui(460))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = DragHeader(self)
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(ui(18), ui(12), ui(14), ui(12))
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("冲突报告")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("已启用 Mod 的 VPK 内部资源发生重叠")
        subtitle.setObjectName("dialogSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setText("×")
        close.setToolTip("关闭")
        close.clicked.connect(self.reject)
        header_layout.addWidget(close)
        layout.addWidget(header)

        body = QFrame()
        body.setObjectName("conflictBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("conflictScroll")
        scroll.setWidgetResizable(True)
        scroll.viewport().setObjectName("conflictViewport")
        host = QWidget()
        host.setObjectName("conflictHost")
        rows = QVBoxLayout(host)
        rows.setContentsMargins(ui(18), ui(18), ui(18), ui(18))
        rows.setSpacing(ui(18))
        for index, group in enumerate(self._conflict_groups(mods), start=1):
            section = QFrame()
            section.setObjectName("conflictGroup")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(ui(12), ui(10), ui(12), ui(12))
            section_layout.setSpacing(ui(8))
            group_label = QLabel(f"冲突组 {index:02d}  ·  {len(group)} 个 Mod")
            group_label.setObjectName("conflictGroupLabel")
            group_label.setText(self._group_label(index, group))
            section_layout.addWidget(group_label)
            section_layout.addWidget(self._make_group_grid(group))
            rows.addWidget(section)
        rows.addStretch(1)
        self._rows = rows
        self._mods = mods
        scroll.setWidget(host)
        body_layout.addWidget(scroll)
        layout.addWidget(body, 1)

    def _make_group_grid(self, group: list[Mod]) -> QWidget:
        cards_host = QWidget()
        cards_host.setObjectName("groupCardsHost")
        cards_grid = QGridLayout(cards_host)
        cards_grid.setContentsMargins(0, 0, 0, 0)
        cards_grid.setHorizontalSpacing(ui(12))
        cards_grid.setVerticalSpacing(ui(12))
        cards_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for index, mod in enumerate(sorted(group, key=lambda item: (-len(item.conflict_with), item.title.lower()))):
            card = ConflictCard(mod)
            card.disable_requested.connect(self.disable_requested)
            cards_grid.addWidget(card, index // 4, index % 4)
        return cards_host

    def refresh_after_disable(self, _mod_id: str) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        groups = self._conflict_groups(self._mods)
        if not groups:
            message = QLabel("没有剩余冲突")
            message.setObjectName("emptyText")
            message.setAlignment(Qt.AlignCenter)
            self._rows.addWidget(message)
            self._rows.addStretch(1)
            return
        for index, group in enumerate(groups, start=1):
            section = QFrame()
            section.setObjectName("conflictGroup")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(ui(12), ui(10), ui(12), ui(12))
            section_layout.setSpacing(ui(8))
            group_label = QLabel(f"冲突组 {index:02d}  ·  {len(group)} 个 Mod")
            group_label.setObjectName("conflictGroupLabel")
            group_label.setText(self._group_label(index, group))
            section_layout.addWidget(group_label)
            section_layout.addWidget(self._make_group_grid(group))
            self._rows.addWidget(section)
        self._rows.addStretch(1)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedSize(ui(480), ui(330))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = DragHeader(self)
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(ui(18), ui(10), ui(12), ui(10))
        title = QLabel("关于 L4D2 BOSS")
        title.setObjectName("dialogTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setText("×")
        close.setToolTip("关闭")
        close.clicked.connect(self.accept)
        header_layout.addWidget(close)
        layout.addWidget(header)

        content = QWidget()
        content.setObjectName("aboutContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(ui(28), ui(24), ui(28), ui(24))
        content_layout.setSpacing(ui(10))
        brand = QLabel("L4D2 BOSS")
        brand.setObjectName("aboutBrand")
        version = QLabel("L4D2 Mod Loadout Manager  ·  v1.0.0")
        version.setObjectName("aboutVersion")
        designer = QLabel("设计人：陈端云（Mr.Chen）")
        designer.setObjectName("aboutDesigner")
        description = QLabel("一个面向《求生之路 2》的本地 Mod 管理工具。\n\n支持扫描 VPK、Steam 信息同步、分类管理、组合保存，以及已启用 Mod 的资源冲突检测。")
        description.setObjectName("aboutDescription")
        description.setWordWrap(True)
        content_layout.addWidget(brand)
        content_layout.addWidget(version)
        content_layout.addWidget(designer)
        content_layout.addSpacing(ui(4))
        content_layout.addWidget(description)
        github = QLabel("GitHub：https://github.com/chenduanyun091216/L4DBoss")
        github.setObjectName("aboutLink")
        github.setCursor(Qt.PointingHandCursor)
        github.setStyleSheet("color: #2d65d6;")
        github.mouseReleaseEvent = lambda _e: QDesktopServices.openUrl(QUrl("https://github.com/chenduanyun091216/L4DBoss"))
        content_layout.addWidget(github)
        content_layout.addStretch(1)
        layout.addWidget(content, 1)


class ModDetailsDialog(QDialog):
    """Frameless, read-only detail view for a single Mod."""

    def __init__(self, mod: Mod, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.resize(ui(620), ui(560))
        self.setMinimumSize(ui(500), ui(420))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = DragHeader(self)
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(ui(18), ui(10), ui(12), ui(10))
        title = QLabel("Mod 详细信息")
        title.setObjectName("dialogTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setToolTip("关闭")
        close.clicked.connect(self.accept)
        header_layout.addWidget(close)
        root.addWidget(header)

        content = QFrame()
        content.setObjectName("modDetailsContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(ui(20), ui(18), ui(20), ui(18))
        layout.setSpacing(ui(10))
        name = QLabel(mod.title or mod.file_name)
        name.setObjectName("modDetailsTitle")
        name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(name)

        code = mod.workshop_id or Path(mod.file_name).stem
        fields = [
            ("文件", mod.file_name),
            ("编号", code),
            ("作者", mod.author or "未知"),
            ("订阅", mod.display_subscriptions if mod.subscriptions else "暂无"),
            ("评分", f"{mod.rating:.1f}" if mod.rating else "暂无"),
            ("来源", "Steam 创意工坊" if mod.steam_loaded and mod.workshop_id else "本地文件"),
            ("状态", "已启用" if mod.active else "已禁用"),
            ("分类", "、".join(mod.categories) if mod.categories else "未分类"),
            ("文件路径", mod.file_path),
        ]
        for label, value in fields:
            row = QHBoxLayout()
            row.setSpacing(ui(10))
            key = QLabel(label)
            key.setObjectName("modDetailsKey")
            key.setFixedWidth(ui(62))
            value_label = QLabel(str(value))
            value_label.setObjectName("modDetailsValue")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(key, 0, Qt.AlignTop)
            row.addWidget(value_label, 1)
            layout.addLayout(row)

        description_title = QLabel("描述")
        description_title.setObjectName("modDetailsKey")
        layout.addWidget(description_title)
        description = QLabel(mod.description.strip() or "暂无描述")
        description.setObjectName("modDetailsDescription")
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(description)
        layout.addStretch(1)
        root.addWidget(content, 1)


QtFileDialog = QFileDialog


class AppMessageDialog(QDialog):
    """Frameless, application-themed replacement for system message boxes."""

    def __init__(self, message: str, tone: str = "info", confirm: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(ui(390))
        self.setMaximumWidth(ui(520))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        surface = QFrame()
        surface.setObjectName("promptSurface")
        root.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(ui(22), ui(20), ui(22), ui(18))
        layout.setSpacing(ui(14))

        content = QHBoxLayout()
        content.setSpacing(ui(12))
        icon = QLabel("!" if tone in {"warning", "error"} else "i")
        icon.setObjectName(f"promptIcon{tone.title()}")
        icon.setFixedSize(ui(30), ui(30))
        icon.setAlignment(Qt.AlignCenter)
        content.addWidget(icon, 0, Qt.AlignTop)
        text = QLabel(message)
        text.setObjectName("promptText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.addWidget(text, 1)
        layout.addLayout(content)

        actions = QHBoxLayout()
        actions.addStretch(1)
        if confirm:
            cancel = QPushButton("\u53d6\u6d88")
            cancel.setObjectName("promptSecondaryButton")
            cancel.clicked.connect(self.reject)
            actions.addWidget(cancel)
        accept = QPushButton("\u786e\u5b9a")
        accept.setObjectName("promptPrimaryButton")
        accept.clicked.connect(self.accept)
        actions.addWidget(accept)
        layout.addLayout(actions)


class AppInputDialog(QDialog):
    """Frameless input prompt used for naming saved loadouts."""

    def __init__(self, label: str, initial_text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(ui(420))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        surface = QFrame()
        surface.setObjectName("promptSurface")
        root.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(ui(22), ui(20), ui(22), ui(18))
        layout.setSpacing(ui(12))
        prompt = QLabel(label)
        prompt.setObjectName("promptText")
        layout.addWidget(prompt)
        self.input = QLineEdit(initial_text)
        self.input.setObjectName("promptInput")
        self.input.returnPressed.connect(self.accept)
        layout.addWidget(self.input)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("\u53d6\u6d88")
        cancel.setObjectName("promptSecondaryButton")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("\u786e\u5b9a")
        confirm.setObjectName("promptPrimaryButton")
        confirm.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        layout.addLayout(actions)
        QTimer.singleShot(0, self.input.setFocus)

    @classmethod
    def get_text(cls, parent, _title: str, label: str, text: str = "") -> tuple[str, bool]:
        dialog = cls(label, text, parent)
        accepted = dialog.exec_() == QDialog.Accepted
        return dialog.input.text(), accepted


class AppMessageBox:
    """Compatibility facade for existing QMessageBox call sites."""

    Yes, No = 1, 0

    @staticmethod
    def information(parent, _title: str, message: str) -> int:
        return AppMessageDialog(message, "info", parent=parent).exec_()

    @staticmethod
    def warning(parent, _title: str, message: str) -> int:
        return AppMessageDialog(message, "warning", parent=parent).exec_()

    @staticmethod
    def critical(parent, _title: str, message: str) -> int:
        return AppMessageDialog(message, "error", parent=parent).exec_()

    @staticmethod
    def question(parent, _title: str, message: str) -> int:
        accepted = AppMessageDialog(message, "warning", confirm=True, parent=parent).exec_() == QDialog.Accepted
        return AppMessageBox.Yes if accepted else AppMessageBox.No


class AppInputBox:
    @staticmethod
    def getText(parent, title: str, label: str, text: str = "") -> tuple[str, bool]:
        return AppInputDialog.get_text(parent, title, label, text)


class AppFileDialog:
    @staticmethod
    def getOpenFileName(parent, _title: str, directory: str = "", filter: str = "") -> tuple[str, str]:
        dialog = QtFileDialog(parent)
        dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dialog.setOption(QtFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QtFileDialog.ExistingFile)
        dialog.setNameFilter(filter)
        dialog.setDirectory(directory)
        files = dialog.selectedFiles() if dialog.exec_() else []
        return (files[0], filter) if files else ("", "")

    @staticmethod
    def getExistingDirectory(parent, _title: str, directory: str = "") -> str:
        dialog = QtFileDialog(parent)
        dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dialog.setOption(QtFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QtFileDialog.Directory)
        dialog.setOption(QtFileDialog.ShowDirsOnly, True)
        dialog.setDirectory(directory)
        return dialog.selectedFiles()[0] if dialog.exec_() and dialog.selectedFiles() else ""


# Keep existing call sites concise while ensuring every application prompt is frameless.
QMessageBox = AppMessageBox
QInputDialog = AppInputBox
QFileDialog = AppFileDialog


class CollectionItemDelegate(QStyledItemDelegate):
    """Paint a compact delete affordance on the right side of each collection."""

    delete_width = 34

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        delete_rect = option.rect.adjusted(option.rect.width() - self.delete_width, 0, 0, 0)
        # This is a multi-check popup, not a single-selection list.  Remove
        # the default blue hover/selection state while keeping each item's
        # checkbox state visible.
        option.state &= ~QStyle.State_MouseOver
        option.state &= ~QStyle.State_Selected
        super().paint(painter, option, index)
        painter.save()
        # The delete affordance is painted after the default delegate, so
        # restore the normal popup background in its area as well.
        painter.fillRect(delete_rect, option.palette.base().color())
        painter.setPen(QColor(
            theme_color("tree_default") if index.data(Qt.UserRole) == "default" else theme_color("tree_favorite")
        ))
        painter.drawText(delete_rect, Qt.AlignCenter, "×")
        painter.restore()


class MultiSelectComboBox(QComboBox):
    """A checkable combo box that keeps its popup open for multi-selection."""

    selection_changed = pyqtSignal()
    collection_delete_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setCursor(Qt.PointingHandCursor)
        self.setCursor(Qt.PointingHandCursor)
        self.setItemDelegate(CollectionItemDelegate(self))
        self._popup_open = False
        self._keep_popup_open = False
        self.view().setSelectionMode(QAbstractItemView.NoSelection)
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self.view().isVisible():
                self.hidePopup()
            else:
                self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor(theme_color("tree_expand")))
        painter.setFont(QFont("Segoe UI Symbol", max(9, ui(13)), QFont.Bold))
        painter.translate(0, -ui(1))
        painter.drawText(self.rect().adjusted(0, 0, -ui(9), 0), Qt.AlignRight | Qt.AlignVCenter, "⌄")
        painter.end()

    def showPopup(self) -> None:
        if self.view().isVisible():
            return
        self._popup_open = True
        super().showPopup()

    def hidePopup(self) -> None:
        # QComboBox may request closing immediately after a popup item is
        # clicked.  A checkable item is not a final selection, so keep the
        # popup open until the user clicks outside it or toggles the combo.
        if self._keep_popup_open:
            return
        if not self.view().isVisible() and not self._popup_open:
            return
        self._popup_open = False
        super().hidePopup()

    def togglePopup(self) -> None:
        if self._popup_open or self.view().isVisible():
            self.hidePopup()
        else:
            self.showPopup()

    def eventFilter(self, source, event) -> bool:
        if source is self.lineEdit() and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                if self.view().isVisible():
                    self.hidePopup()
                else:
                    self.showPopup()
            return True
        if source is self.view().viewport() and event.type() == QEvent.MouseButtonPress:
            index = self.view().indexAt(event.pos())
            if index.isValid():
                item_rect = self.view().visualRect(index)
                if event.pos().x() >= item_rect.right() - CollectionItemDelegate.delete_width:
                    self.collection_delete_requested.emit(str(self.itemData(index.row())))
                    return True
                state = self.model().data(index, Qt.CheckStateRole)
                next_state = Qt.Unchecked if state == Qt.Checked else Qt.Checked
                self._keep_popup_open = True
                self.model().setData(index, next_state, Qt.CheckStateRole)
                self.selection_changed.emit()
                return True
        if source is self.view().viewport() and event.type() == QEvent.MouseButtonRelease:
            # Consume the release too. QComboBox normally treats it as a
            # completed single selection and closes its popup.
            if self._keep_popup_open and event.button() == Qt.LeftButton:
                QTimer.singleShot(0, self._release_popup_guard)
                return True
        return super().eventFilter(source, event)

    def _release_popup_guard(self) -> None:
        self._keep_popup_open = False
        if not self.view().isVisible() and self._popup_open:
            self.showPopup()

    def checked_values(self) -> list[str]:
        return [
            self.itemData(index)
            for index in range(self.count())
            if self.model().data(self.model().index(index, 0), Qt.CheckStateRole) == Qt.Checked
        ]


class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checked = False
        self.setFixedSize(ui(38), ui(20))
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._checked)
        event.accept()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self.rect().adjusted(0, 1, -1, -2)
        painter.setPen(QColor(theme_color("toggle_on_border") if self._checked else theme_color("toggle_off_border")))
        painter.setBrush(QColor(theme_color("toggle_on_fill") if self._checked else theme_color("toggle_off_fill")))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob_diameter = max(1, track.height() - ui(4))
        knob_x = track.right() - knob_diameter - ui(2) if self._checked else track.left() + ui(2)
        knob = track.adjusted(knob_x - track.left(), ui(2), knob_x - track.right() + knob_diameter, -ui(2))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_color("toggle_knob")))
        painter.drawEllipse(knob)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowIcon(QIcon(str(TITLE_ICON)))
        self.setWindowTitle("L4D2 Boss · 求生之路 2 Mod 管理器")
        self.resize(ui(1200), ui(820))
        self.setMinimumSize(ui(1020), ui(680))
        self.storage = AppStorage(USER_DATA_ROOT)
        self.settings = self.storage.load_settings()
        self._theme = self.settings.get("theme", "dark")
        if self._theme not in THEME_ORDER:
            self._theme = "dark"
        self._simple_category_cache: dict[str, set[str]] = {}
        self.mods = self.storage.load_mods()
        self.steam_cache = self.storage.load_steam_cache()
        if self._reclassify_loaded_mods():
            self._simple_category_cache.clear()
            self.storage.save_mods(self.mods)
        self.collections = self.storage.load_collections()
        cleaned_collections = [collection for collection in self.collections if collection.name != "default"]
        if len(cleaned_collections) != len(self.collections):
            self.collections = cleaned_collections
            self.storage.save_collections(self.collections)
        saved_collection_names = self.settings.get("selected_collection_names", [])
        known_collection_names = {collection.name for collection in self.collections}
        self._selected_collection_names: set[str] = {
            name for name in saved_collection_names if name in known_collection_names
        }
        self._updating_collection_combo = False
        self._collection_apply_timer = QTimer(self)
        self._collection_apply_timer.setSingleShot(True)
        self._collection_apply_timer.timeout.connect(self._apply_pending_collection_selection)
        self.current_category = "all"
        self._tree_rebuilding = False
        self._category_select_timer = None
        self.page_size = 100
        self.current_page = 0
        self.category_mode = "simple"
        self._active_only_filter = False
        self.thread_pool = QThreadPool.globalInstance()
        self.collection_sync_pool = QThreadPool(self)
        self.collection_sync_pool.setMaxThreadCount(1)
        self.steam_sync_in_progress = False
        self._progress_owner: str | None = None
        self._steam_cancel_event = Event()
        self._card_widgets: dict[str, ModCard] = {}
        self._card_cache: dict[str, ModCard] = {}
        self._cards_render_token = 0
        self._cards_ready_for_reflow = False
        self._mod_sort_cache: dict[str, int] = {}
        self._search_refresh_timer = QTimer(self)
        self._search_refresh_timer.setSingleShot(True)
        self._search_refresh_timer.timeout.connect(self.refresh_cards)
        self._card_refresh_pending = False
        self._content_alignment_pending = False
        self._content_mode = "mods"
        self._conflict_paths: dict[str, set[str]] = {}
        self._active_path_owners: dict[str, set[str]] = {}
        self._build_ui()
        self._apply_style()
        self._set_status_selection(self.total_label)
        self._rebuild_conflict_index()
        self.refresh_collection_combo()
        if self._selected_collection_names:
            self.restore_selected_collections_in_background()
            self.apply_selected_collections(write_addonlist=False)
        self.refresh_tree()
        self.refresh_cards()
        self.refresh_stats()
        game_exe = self.settings.get("game_exe")
        if game_exe and Path(game_exe).exists() and not self.mods:
            self.scan_mods(False)

    def _reclassify_loaded_mods(self) -> bool:
        """Apply the current source-aware rules to cached category data."""
        changed = False
        for mod in self.mods.values():
            categories = infer_categories(
                mod.title,
                mod.files,
                steam_tags=mod.steam_tags,
                description=mod.description,
                file_name=mod.file_name,
            )
            if categories != mod.categories:
                mod.categories = categories
                changed = True
        return changed

    def _apply_steam_cache(self, mods: dict[str, Mod]) -> None:
        for mod in mods.values():
            workshop_id = mod.workshop_id
            cached = self.steam_cache.get(workshop_id) if workshop_id else None
            if not cached:
                continue
            for field in ("title", "author", "subscriptions", "rating", "description", "steam_tags", "steam_loaded"):
                if field in cached:
                    setattr(mod, field, deepcopy(cached[field]))
            mod.categories = infer_categories(
                mod.title,
                mod.files,
                steam_tags=mod.steam_tags,
                description=mod.description,
                file_name=mod.file_name,
            )

    def _save_steam_cache(self) -> None:
        fields = ("title", "author", "subscriptions", "rating", "description", "steam_tags", "steam_loaded")
        for mod in self.mods.values():
            workshop_id = mod.workshop_id
            if workshop_id and mod.steam_loaded:
                self.steam_cache[workshop_id] = {field: deepcopy(getattr(mod, field)) for field in fields}
        self.storage.save_steam_cache(self.steam_cache)

    def _build_ui(self) -> None:
        central = BackgroundSurface(BACKGROUND_IMAGE)
        central.setObjectName("appSurface")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QSplitter(Qt.Horizontal)
        body.setHandleWidth(1)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(ui(230))
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(ui(16), ui(18), ui(12), ui(16))
        side_layout.setSpacing(ui(10))
        caption_row = QHBoxLayout()
        caption_row.setContentsMargins(0, 0, 0, 0)
        caption = QLabel("MOD 分类")
        caption.setObjectName("sectionLabel")
        caption_row.addWidget(caption)
        caption_row.addStretch(1)
        simple_label = QLabel("Steam 分类")
        simple_label.setObjectName("categorySwitchLabel")
        caption_row.addWidget(simple_label)
        self.category_mode_switch = ToggleSwitch()
        self.category_mode_switch.setObjectName("categoryModeSwitch")
        self.category_mode_switch.setChecked(False)
        self.category_mode_switch.toggled.connect(self.on_category_mode_switch_changed)
        caption_row.addWidget(self.category_mode_switch)
        side_layout.addLayout(caption_row)
        self.category_tree = QTreeWidget()
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setIndentation(ui(22))
        self.category_tree.setUniformRowHeights(True)
        self.category_tree.itemSelectionChanged.connect(self.on_category_selected)
        side_layout.addWidget(self.category_tree, 1)
        body.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        # Keep only a slim outer gutter; the toolbar uses the scrollbar width
        # as an inset so its controls align with the card viewport, not the bar.
        content_layout.setContentsMargins(ui(16), ui(6), ui(8), ui(18))
        content_layout.setSpacing(ui(0))
        self.content_bar = self._build_content_bar()
        self.content_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content_layout.addWidget(self.content_bar)
        content_layout.addSpacing(ui(8))
        self.scroll = QScrollArea()
        self.scroll.setObjectName("cardsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.viewport().setObjectName("cardsViewport")
        self.scroll.verticalScrollBar().rangeChanged.connect(self._schedule_content_alignment)
        self._cards_loading_overlay = QFrame(self.scroll)
        self._cards_loading_overlay.setObjectName("cardsLoadingOverlay")
        loading_layout = QVBoxLayout(self._cards_loading_overlay)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        loading_layout.setSpacing(ui(6))
        self._cards_loading_spinner = QLabel("◐")
        self._cards_loading_spinner.setObjectName("cardsLoadingSpinner")
        self._cards_loading_spinner.setAlignment(Qt.AlignCenter)
        self._cards_loading_label = QLabel("正在加载 Mod…")
        self._cards_loading_label.setObjectName("cardsLoadingLabel")
        self._cards_loading_label.setAlignment(Qt.AlignCenter)
        loading_panel = QFrame()
        loading_panel.setObjectName("cardsLoadingPanel")
        panel_layout = QVBoxLayout(loading_panel)
        panel_layout.setContentsMargins(ui(24), ui(18), ui(24), ui(18))
        panel_layout.setSpacing(ui(6))
        panel_layout.addWidget(self._cards_loading_spinner)
        panel_layout.addWidget(self._cards_loading_label)
        loading_layout.addStretch(1)
        loading_layout.addWidget(loading_panel, 0, Qt.AlignHCenter)
        loading_layout.addStretch(1)
        self._cards_loading_frames = ("◐", "◓", "◑", "◒")
        self._cards_loading_frame = 0
        self._cards_loading_timer = QTimer(self)
        self._cards_loading_timer.timeout.connect(self._advance_cards_loading_spinner)
        self._cards_loading_overlay.hide()
        self.cards_host = QWidget()
        self.cards_host.setObjectName("cardsHost")
        self.cards_layout = QGridLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(ui(11))
        self.cards_layout.setVerticalSpacing(ui(12))
        self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.cards_host)
        content_layout.addWidget(self.scroll, 1)
        self.pagination_bar = QWidget()
        self.pagination_bar.setObjectName("paginationBar")
        pagination_layout = QHBoxLayout(self.pagination_bar)
        pagination_layout.setContentsMargins(0, 0, ui(8), 0)
        pagination_layout.setSpacing(ui(8))
        self.previous_page_button = QPushButton("上一页")
        self.previous_page_button.setObjectName("paginationButton")
        self.previous_page_button.setFixedHeight(ui(20))
        self.previous_page_button.clicked.connect(lambda: self.change_page(-1))
        self.page_label = QLabel()
        self.page_label.setObjectName("pageLabel")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next_page_button = QPushButton("下一页")
        self.next_page_button.setObjectName("paginationButton")
        self.next_page_button.setFixedHeight(ui(20))
        self.next_page_button.clicked.connect(lambda: self.change_page(1))
        pagination_layout.addWidget(self.previous_page_button)
        pagination_layout.addWidget(self.page_label)
        pagination_layout.addWidget(self.next_page_button)
        pagination_layout.addStretch(1)
        self.pagination_spacer = QWidget()
        self.pagination_spacer.setFixedHeight(ui(10))
        content_layout.addWidget(self.pagination_spacer)
        content_layout.addWidget(self.pagination_bar)
        body.addWidget(content)
        body.setSizes([ui(275), ui(1045)])
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setCollapsible(0, False)
        body.setCollapsible(1, False)
        root.addWidget(body, 1)
        root.addWidget(self._build_footer())
        self.setCentralWidget(central)

    def _build_header(self) -> QWidget:
        header = DragHeader(self)
        header.setObjectName("header")
        header.setFixedHeight(ui(56))
        layout = QHBoxLayout(header)
        layout.setContentsMargins(ui(24), ui(4), ui(8), ui(4))
        layout.setSpacing(ui(11))
        brand = QVBoxLayout()
        brand.setSpacing(0)
        name_row = QHBoxLayout()
        name_row.setSpacing(ui(8))
        brand_icon = QLabel()
        brand_icon.setObjectName("brandIcon")
        brand_icon_size = ui(38)
        brand_icon.setFixedSize(brand_icon_size, brand_icon_size)
        brand_icon.setAlignment(Qt.AlignCenter)
        title_pixmap = QPixmap(str(TITLE_IMAGE))
        if title_pixmap.isNull():
            brand_icon.hide()
        else:
            # Render at device resolution first so the detailed logo stays sharp
            # on high-DPI screens while retaining a 40px logical display size.
            device_ratio = max(1.0, brand_icon.devicePixelRatioF())
            rendered_size = round(ui(36) * device_ratio)
            title_pixmap = title_pixmap.scaled(
                rendered_size, rendered_size, Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            title_pixmap.setDevicePixelRatio(device_ratio)
            brand_icon.setPixmap(title_pixmap)
        name = QPushButton("L4D2  BOSS")
        name.setObjectName("brandButton")
        name.setToolTip("查看软件信息")
        name.clicked.connect(self.show_about)
        credit = QLabel("@ by Mr.Chen")
        credit.setObjectName("brandCredit")
        credit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        credit.setContentsMargins(0, ui(3), 0, 0)
        name_row.addWidget(brand_icon, 0, Qt.AlignVCenter)
        name_row.addWidget(name, 0, Qt.AlignVCenter)
        name_row.addWidget(credit)
        name_row.addStretch(1)
        sub = QLabel("MOD LOADOUT MANAGER")
        sub.setObjectName("brandSub")
        brand.addLayout(name_row)
        brand.addWidget(sub)
        layout.addLayout(brand)
        layout.addStretch(1)
        self.header_hint = QLabel()
        self.header_hint.setObjectName("headerHint")
        self.header_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.header_hint.setMinimumWidth(ui(250))
        self.header_hint.hide()
        layout.addWidget(self.header_hint)
        self.choose_button = self._header_button(
            QStyle.SP_FileDialogNewFolder, "选择游戏", self.choose_directory,
            secondary=True, tooltip="选择游戏：定位 left4dead2.exe 并扫描 addons 文件夹",
        )
        self.refresh_button = self._header_button(
            QStyle.SP_BrowserReload, "扫描 Mod", lambda: self.scan_mods(False),
            tooltip="扫描 Mod：重新扫描本地 addons 文件夹",
        )
        self.fetch_button = self._header_button(
            QStyle.SP_ArrowDown, "同步 Steam", self.fetch_steam_info,
            tooltip="同步 Steam：获取创意工坊 Mod 的名称、订阅数和标签",
        )
        layout.addWidget(self.choose_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.fetch_button)
        for button in (self.choose_button, self.refresh_button, self.fetch_button):
            button.installEventFilter(self)
        self.toggle_all_button = self._header_button(QStyle.SP_DialogApplyButton, "全部启动", self.toggle_all_mods)
        layout.addWidget(self.toggle_all_button)
        self.theme_button = self._header_button(
            QStyle.SP_DesktopIcon, "", self._open_theme_menu,
        )
        self._update_theme_button()
        self._header_action_buttons = (
            self.choose_button, self.refresh_button, self.fetch_button, self.theme_button,
        )
        header_action_width = max(button.sizeHint().width() for button in self._header_action_buttons) + ui(4)
        for button in self._header_action_buttons:
            button.setFixedWidth(header_action_width)
        layout.addWidget(self.theme_button)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setText("×")
        self.minimize_button = self._window_control_button("−", None, self.showMinimized)
        self.maximize_button = self._window_control_button("□", None, self.toggle_maximized)
        close.setFixedSize(ui(30), ui(30))
        close.clicked.connect(self.close)
        window_controls = QHBoxLayout()
        window_controls.setContentsMargins(0, 0, 0, 0)
        window_controls.setSpacing(ui(4))
        window_controls.addWidget(self.minimize_button)
        window_controls.addWidget(self.maximize_button)
        window_controls.addWidget(close)
        layout.addLayout(window_controls)
        self.close_button = close
        for button in (self.theme_button, self.minimize_button, self.maximize_button, self.close_button):
            button.installEventFilter(self)
        return header

    def show_about(self) -> None:
        AboutDialog(self).exec_()

    def eventFilter(self, source, event) -> bool:
        hints = {
            getattr(self, "choose_button", None): "选择游戏：定位 left4dead2.exe 并扫描 addons 文件夹",
            getattr(self, "refresh_button", None): "扫描 Mod：重新扫描本地 addons 文件夹",
            getattr(self, "fetch_button", None): "同步 Steam：获取创意工坊 Mod 信息",
            getattr(self, "theme_button", None): "切换主题：点击选择界面配色",
            getattr(self, "minimize_button", None): "最小化窗口",
            getattr(self, "maximize_button", None): "最大化 / 还原窗口",
            getattr(self, "close_button", None): "关闭程序",
        }
        if source in hints and hasattr(self, "header_hint"):
            if event.type() == QEvent.Enter:
                self._show_header_hint(hints[source])
            elif event.type() == QEvent.Leave:
                self._clear_header_hint()
        return super().eventFilter(source, event)

    def _header_button(
        self, icon, text, handler, secondary: bool = False, icon_only: bool = False, tooltip: str = "",
    ) -> QPushButton:
        button = QPushButton(self.style().standardIcon(icon), text)
        if icon_only:
            button.setText("")
            button.setObjectName("headerIconButton")
            button.setFixedSize(ui(40), ui(36))
            button.setIconSize(QSize(ui(20), ui(20)))
        else:
            button.setObjectName("headerButtonSecondary" if secondary else "headerButton")
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(handler)
        return button

    @staticmethod
    def _window_control_button(symbol: str, tooltip: str, handler) -> QPushButton:
        button = QPushButton(symbol)
        button.setObjectName("windowControlButton")
        button.setFixedSize(ui(30), ui(30))
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(handler)
        return button

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.maximize_button.setText("□")
        else:
            self.showMaximized()
            self.maximize_button.setText("❐")

    def _show_header_hint(self, text: str) -> None:
        self.header_hint.setText(text)
        self.header_hint.setVisible(True)

    def _clear_header_hint(self) -> None:
        self.header_hint.setVisible(False)
        self.header_hint.clear()

    def _update_theme_button(self) -> None:
        label = THEME_LABELS.get(self._theme, self._theme)
        self.theme_button.setIcon(self._theme_icon())
        self.theme_button.setText(f" {label} ▾")

    def _theme_icon(self) -> QIcon:
        """Create a compact light/dark disc for the theme selector."""
        size = ui(22)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#39c6dc"))
        painter.drawEllipse(ui(2), ui(2), size - ui(4), size - ui(4))
        painter.setBrush(QColor("#f5f8fd"))
        painter.drawEllipse(ui(2), ui(2), (size - ui(4)) // 2, size - ui(4))
        painter.setBrush(QColor("#18344e"))
        painter.drawEllipse(ui(7), ui(7), ui(4), ui(4))
        painter.end()
        return QIcon(pixmap)

    def _open_theme_menu(self) -> None:
        from PyQt5.QtWidgets import QMenu

        menu = QMenu(self)
        menu.setObjectName("themeMenu")
        for theme_key in THEME_ORDER:
            action = menu.addAction(THEME_LABELS.get(theme_key, theme_key))
            action.setCheckable(True)
            action.setChecked(theme_key == self._theme)
            hint = THEME_HINTS.get(theme_key, "")
            action.hovered.connect(lambda h=hint: self._show_header_hint(h))
            action.triggered.connect(lambda _checked=False, key=theme_key: self._set_theme(key))
        menu.aboutToHide.connect(self._clear_header_hint)
        menu.exec_(self.theme_button.mapToGlobal(self.theme_button.rect().bottomLeft()))

    def _set_theme(self, theme_key: str) -> None:
        if theme_key == self._theme:
            return
        self._theme = theme_key
        self.settings["theme"] = theme_key
        self.storage.save_settings(self.settings)
        self._apply_style()
        self._update_theme_button()
        self.refresh_tree()
        for card in self._card_widgets.values():
            card.refresh_state()

    @staticmethod
    def _launch_icon() -> QIcon:
        """An original survivor-inspired mark for the launch action."""
        size = ui(48)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#ff8b8b"))
        painter.setBrush(QColor("#b3242f"))
        painter.drawEllipse(ui(2), ui(2), size - ui(4), size - ui(4))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Arial", max(7, ui(12)), QFont.Black))
        painter.drawText(pixmap.rect().adjusted(0, -ui(5), 0, 0), Qt.AlignCenter, "L4D")
        painter.setFont(QFont("Arial", max(7, ui(15)), QFont.Black))
        painter.drawText(pixmap.rect().adjusted(0, ui(10), 0, 0), Qt.AlignCenter, "2")
        painter.end()
        return QIcon(pixmap)

    def _build_content_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        # The vertical scrollbar is 8px wide.  Match that inset so the
        # collection picker ends on the same line as the last card.
        layout.setContentsMargins(0, 0, ui(8), 0)
        self.content_back_button = QPushButton()
        self.content_back_button.setObjectName("contentBackButton")
        self.content_back_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self.content_back_button.setIconSize(QSize(ui(18), ui(18)))
        self.content_back_button.setFixedSize(ui(30), ui(28))
        self.content_back_button.setToolTip("返回 Mod 列表")
        self.content_back_button.clicked.connect(self.show_mod_list)
        self.content_back_button.hide()
        layout.addWidget(self.content_back_button)
        self.content_title_host = QWidget()
        self.content_title_host.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        title_box = QVBoxLayout(self.content_title_host)
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(1)
        self.content_title = QLabel("全部 Mod")
        self.content_title.setObjectName("contentTitle")
        self.content_subtitle = QLabel()
        self.content_subtitle.setObjectName("contentSubtitle")
        title_box.addWidget(self.content_title)
        title_box.addWidget(self.content_subtitle)
        layout.addWidget(self.content_title_host)
        layout.addStretch(1)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("搜索名称、作者或 Workshop ID…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.on_search_changed)
        self.collection_combo = MultiSelectComboBox()
        self.collection_combo.setObjectName("collectionCombo")
        self.collection_combo.setMinimumWidth(ui(210))
        self.collection_combo.setMaxVisibleItems(7)
        self.collection_combo.view().setObjectName("collectionComboMenu")
        self.collection_combo.view().setTextElideMode(Qt.ElideRight)
        self.collection_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.collection_combo.selection_changed.connect(self.on_collection_selection_changed)
        self.collection_combo.collection_delete_requested.connect(self.delete_collection)
        self._filter_controls = QHBoxLayout()
        self._filter_controls.setContentsMargins(0, 0, 0, 0)
        self._filter_controls.setSpacing(ui(11))
        self._filter_controls.addWidget(self.search_input)
        self._filter_controls.addWidget(self.collection_combo)
        layout.addLayout(self._filter_controls)
        return bar

    def _build_footer_legacy(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(ui(24), ui(10), ui(24), ui(10))
        self.total_label = self._make_mod_count_button("totalModCount", self.show_all_mods)
        self.active_label = self._make_mod_count_button("activeModCount", self.show_active_mods)
        self.conflict_button = QPushButton()
        self.conflict_button.setObjectName("conflictButton")
        self.conflict_button.clicked.connect(self.show_conflicts)
        layout.addWidget(self.total_label)
        layout.addWidget(self.active_label)
        layout.addWidget(self.conflict_button)
        layout.addStretch(1)
        self.save_button = QPushButton(self.style().standardIcon(QStyle.SP_DialogSaveButton), "保 存")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save_collection)
        layout.addWidget(self.save_button)
        layout.insertWidget(layout.indexOf(self.save_button), self.toggle_all_button)
        return footer

    @staticmethod
    def _make_mod_count_button(object_name: str, handler) -> QPushButton:
        button = QPushButton()
        button.setObjectName(object_name)
        button.setFlat(True)
        button.setFixedWidth(ui(78))
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(handler)
        return button

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(ui(56))
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, ui(4), 0, ui(4))
        layout.setSpacing(0)

        status_host = QWidget()
        status_host.setFixedWidth(ui(277))
        status_layout = QHBoxLayout(status_host)
        status_layout.setContentsMargins(ui(24), 0, 0, 0)
        status_layout.setSpacing(ui(8))
        self.total_label = self._make_mod_count_button("totalModCount", self.show_all_mods)
        self.active_label = self._make_mod_count_button("activeModCount", self.show_active_mods)
        self.conflict_button = QPushButton()
        self.conflict_button.setObjectName("conflictButton")
        self.conflict_button.setFixedWidth(ui(78))
        self.conflict_button.clicked.connect(self.show_conflicts)
        status_layout.addWidget(self.total_label)
        status_layout.addWidget(self.active_label)
        status_layout.addWidget(self.conflict_button)
        status_layout.addStretch(1)
        layout.addWidget(status_host)

        self.action_host = QWidget()
        self.action_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        action_host = self.action_host
        action_layout = QHBoxLayout(action_host)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(self.cards_layout.horizontalSpacing())
        self.steam_sync_widget = QWidget()
        self.steam_sync_widget.setObjectName("steamSyncStatus")
        sync_layout = QHBoxLayout(self.steam_sync_widget)
        sync_layout.setContentsMargins(ui(10), 0, ui(10), 0)
        sync_layout.setSpacing(ui(8))
        sync_label = QLabel("正在同步 Steam 数据…")
        sync_label.setObjectName("steamSyncLabel")
        sync_layout.addWidget(sync_label)
        self.steam_sync_progress = QProgressBar()
        self.steam_sync_progress.setObjectName("steamSyncProgress")
        self.steam_sync_progress.setRange(0, 1)
        self.steam_sync_progress.setTextVisible(False)
        self.steam_sync_progress.setFixedWidth(ui(92))
        sync_layout.addWidget(self.steam_sync_progress)
        self.steam_sync_widget.hide()
        action_layout.addWidget(self.steam_sync_widget)
        action_layout.addStretch(1)
        action_layout.addWidget(self.toggle_all_button)
        self.save_button = QPushButton(self.style().standardIcon(QStyle.SP_DialogSaveButton), "保 存")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save_collection)
        action_layout.addWidget(self.save_button)
        self.save_as_button = QPushButton(self.style().standardIcon(QStyle.SP_FileDialogDetailedView), "另存为")
        self.save_as_button.setObjectName("secondaryButton")
        self.save_as_button.clicked.connect(self.save_collection_as_new)
        action_layout.addWidget(self.save_as_button)
        self.launch_button = QPushButton("  启动游戏")
        self.launch_button.setObjectName("launchButton")
        self.launch_button.setIcon(self._launch_icon())
        self.launch_button.setIconSize(QSize(ui(20), ui(20)))
        self.launch_button.clicked.connect(self.launch_game)
        action_layout.addWidget(self.launch_button)
        self._footer_action_buttons = (
            self.toggle_all_button, self.save_button, self.save_as_button, self.launch_button,
        )
        action_buttons = self._header_action_buttons + self._footer_action_buttons
        action_width = max(button.minimumSizeHint().width() for button in action_buttons)
        action_height = max(ui(1), max(button.minimumSizeHint().height() for button in action_buttons) - ui(2))
        for button in self._header_action_buttons:
            button.setFixedSize(action_width, action_height)
        for button in self._footer_action_buttons:
            button.setFixedSize(action_width, action_height)
        layout.addWidget(action_host, 1)
        return footer

    def _apply_style(self) -> None:
        global ACTIVE_THEME
        ACTIVE_THEME = self._theme
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(THEMES.get(self._theme, THEMES["dark"]))
        self.setStyleSheet("")
        # Re-evaluate button minimum sizes after the stylesheet has applied;
        # padding and font metrics are part of the real required width.
        QTimer.singleShot(0, self._sync_content_right_edges)

    def _rebuild_conflict_index(self) -> None:
        self._conflict_paths = {
            mod_id: {path for path in mod.files if is_conflict_relevant_path(path)}
            for mod_id, mod in self.mods.items()
        }
        self._active_path_owners = {}
        for mod_id, mod in self.mods.items():
            if mod.active:
                for path in self._conflict_paths[mod_id]:
                    self._active_path_owners.setdefault(path, set()).add(mod_id)
        self._refresh_conflicts_from_index()

    def _refresh_conflicts_from_index(self, mod_ids: set[str] | None = None) -> None:
        targets = mod_ids or set(self.mods)
        for mod_id in targets:
            mod = self.mods.get(mod_id)
            if mod is None or not mod.active:
                if mod is not None:
                    mod.conflict_with = []
                continue
            peers: set[str] = set()
            for path in self._conflict_paths.get(mod_id, set()):
                owners = self._active_path_owners.get(path, set())
                if len(owners) > 1:
                    peers.update(owners - {mod_id})
            mod.conflict_with = sorted(peers)

    def _update_conflicts_for_toggle(self, mod_id: str) -> set[str]:
        """Update only Mod records touched by the toggled Mod's resource paths."""
        affected = {mod_id}
        paths = self._conflict_paths.get(mod_id, set())
        if self.mods[mod_id].active:
            for path in paths:
                owners = self._active_path_owners.setdefault(path, set())
                affected.update(owners)
                owners.add(mod_id)
        else:
            for path in paths:
                owners = self._active_path_owners.get(path)
                if owners is None:
                    continue
                affected.update(owners)
                owners.discard(mod_id)
                if not owners:
                    self._active_path_owners.pop(path, None)
        self._refresh_conflicts_from_index(affected)
        return affected

    def _refresh_card_states(self, mod_ids: set[str] | None = None) -> None:
        for mod_id in mod_ids or set(self._card_widgets):
            card = self._card_widgets.get(mod_id)
            if card is not None:
                card.refresh_state()

    def refresh_tree(self) -> None:
        selected = self.current_category
        self._tree_rebuilding = True
        try:
            self.category_tree.clear()
            categories = SIMPLE_CATEGORIES if self.category_mode == "simple" else CATEGORIES
            for category in categories:
                self.category_tree.addTopLevelItem(self._make_tree_item(category, 0))
            self.category_tree.expandAll()
            target = self.category_tree.topLevelItem(0)
            stack = [target] if target is not None else []
            while stack:
                item = stack.pop()
                if item.data(0, Qt.UserRole) == selected:
                    target = item
                    break
                stack.extend(item.child(index) for index in range(item.childCount()))
            self.category_tree.setCurrentItem(target)
        finally:
            self._tree_rebuilding = False

    def _make_tree_item(self, entry, depth: int) -> QTreeWidgetItem:
        if isinstance(entry, tuple):
            item = QTreeWidgetItem([entry[1]])
            item.setData(0, Qt.UserRole, entry[0])
            item.setData(0, Qt.UserRole + 1, depth)
            child_font = QFont(self.category_tree.font())
            child_font.setPointSize(max(9, child_font.pointSize() - (1 if depth > 1 else 0)))
            item.setFont(0, child_font)
            # Leaf (no children): top-level stays blue; on the light theme
            # leaves use a mid gray to balance the darker parent titles.
            if depth == 0:
                item.setForeground(0, QColor(theme_color("link")))
            elif self._theme == "light":
                item.setForeground(0, QColor("#6b7589"))
            else:
                item.setForeground(0, QColor(theme_color("tree_default")))
            return item
        item = QTreeWidgetItem([entry["label"]])
        item.setData(0, Qt.UserRole, entry["id"])
        item.setData(0, Qt.UserRole + 1, depth)
        root_font = QFont(self.category_tree.font())
        root_font.setBold(depth == 0 or (self.category_mode == "simple" and depth == 1))
        root_font.setPointSize(max(10, root_font.pointSize() + (1 if depth == 0 else 0)))
        item.setFont(0, root_font)
        # Branches (have children): on the light theme every parent title uses
        # a soft dark gray; other themes keep top-level blue and deeper levels gray.
        if self._theme == "light":
            item.setForeground(0, QColor("#44506a"))
        else:
            item.setForeground(0, QColor(theme_color("link") if depth == 0 else theme_color("tree_default")))
        for child in entry.get("children", []):
            item.addChild(self._make_tree_item(child, depth + 1))
        return item

    def on_category_mode_switch_changed(self, checked: bool) -> None:
        self.category_mode = "steam" if checked else "simple"
        self.current_category = "all"
        self.category_tree.blockSignals(True)
        self.refresh_tree()
        self.category_tree.blockSignals(False)
        self._update_mod_filter_title()
        self.current_page = 0
        if self._content_mode == "mods":
            self.refresh_cards()

    def refresh_cards(self) -> None:
        self._content_mode = "mods"
        self._cards_render_token += 1
        self._cards_ready_for_reflow = False
        self._show_cards_loading()
        self.content_back_button.hide()
        self.search_input.show()
        self.collection_combo.show()
        # Detach and hide existing cards instead of destroying them. Reusing
        # cards removes the noticeable pause when switching category filters.
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
        self._card_widgets = {}
        filtered = self.filtered_mods()
        total_pages = max(1, (len(filtered) + self.page_size - 1) // self.page_size)
        self.current_page = min(self.current_page, total_pages - 1)
        self._update_pagination(len(filtered), total_pages)
        page_start = self.current_page * self.page_size
        page_mods = filtered[page_start:page_start + self.page_size]
        self.content_subtitle.setText(f"显示 {len(filtered)} 个 Mod  ·  点击卡片即可快速启用或禁用")
        if not filtered:
            columns = self.card_columns()
            self.cards_layout.setAlignment(Qt.Alignment())
            self.cards_layout.setRowStretch(0, 1)
            for column in range(max(columns, self.cards_layout.columnCount())):
                self.cards_layout.setColumnMinimumWidth(column, 0)
                self.cards_layout.setColumnStretch(column, 1)
            empty_host = QWidget()
            empty_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            empty_layout = QVBoxLayout(empty_host)
            empty_layout.setContentsMargins(0, 0, 0, 0)
            empty_layout.addStretch(1)
            empty = QLabel("没有找到匹配的 Mod\n调整搜索条件，或点击「选择游戏」扫描 addons 文件夹。")
            empty.setObjectName("emptyText")
            empty.setAlignment(Qt.AlignCenter)
            empty_layout.addWidget(empty, 0, Qt.AlignHCenter)
            empty_layout.addStretch(1)
            self.cards_layout.addWidget(empty_host, 0, 0, 1, columns)
            self._hide_cards_loading()
            return
        self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.cards_layout.setRowStretch(0, 0)
        columns = self.card_columns()
        card_width = self.card_width(columns)
        for column in range(max(columns, self.cards_layout.columnCount())):
            self.cards_layout.setColumnMinimumWidth(column, 0)
            self.cards_layout.setColumnStretch(column, 0)
        token = self._cards_render_token
        QTimer.singleShot(0, lambda: self._populate_cards_batch(
            page_mods, columns, card_width, token, 0,
        ))

    def _populate_cards_batch(
        self, page_mods: list[Mod], columns: int, card_width: int, token: int, start: int,
    ) -> None:
        if token != self._cards_render_token or self._content_mode != "mods":
            return
        current_columns = self.card_columns()
        if current_columns != columns:
            # The viewport can settle to its final width after the first batch.
            # Restart the current page so every row uses one consistent column count.
            self.refresh_cards()
            return
        # A complete page is laid out in one pass. This prevents the initial
        # load from looking like cards are sliding into place row by row.
        # Keep each event-loop slice small so large pages remain interactive.
        batch_size = 12
        for index, mod in enumerate(page_mods[start:start + batch_size], start=start):
            card = self._card_cache.get(mod.id)
            if card is None:
                card = ModCard(mod, self.collection_names_for(mod.id), card_width)
                card.clicked.connect(self.toggle_mod)
                card.context_requested.connect(self.show_card_context_menu)
                card.favorite_toggled.connect(self.toggle_favorite)
                self._card_cache[mod.id] = card
            else:
                card.mod = mod
                card.refresh_state()
                card.setFixedWidth(card_width)
            self._card_widgets[mod.id] = card
            self.cards_layout.addWidget(card, index // columns, index % columns, Qt.AlignTop)
            # Reparent through the layout first; showing it earlier would make
            # a parentless card appear as a separate native window.
            card.show()
        next_start = start + batch_size
        if next_start < len(page_mods):
            QTimer.singleShot(0, lambda: self._populate_cards_batch(
                page_mods, columns, card_width, token, next_start,
            ))
            return
        # Normalize the final grid after all cards are present. This prevents
        # a late viewport resize from leaving mixed 3/4-column rows.
        self._reflow_cards()
        self._cards_ready_for_reflow = True
        self._hide_cards_loading()
        for column in range(columns):
            self.cards_layout.setColumnMinimumWidth(column, 0)
        self._sync_content_right_edges()

    def _update_pagination(self, total: int, total_pages: int) -> None:
        visible = total_pages > 1
        self.pagination_bar.setVisible(visible)
        self.pagination_spacer.setVisible(visible)
        if not visible:
            return
        self.page_label.setText(f"第 {self.current_page + 1} / {total_pages} 页")
        self.previous_page_button.setEnabled(self.current_page > 0)
        self.next_page_button.setEnabled(self.current_page < total_pages - 1)

    def change_page(self, offset: int) -> None:
        self.current_page = max(0, self.current_page + offset)
        self.refresh_cards()

    def on_search_changed(self) -> None:
        self.current_page = 0
        self._search_refresh_timer.start(120)

    def card_columns(self) -> int:
        width = self._card_viewport_width()
        spacing = self.cards_layout.horizontalSpacing() if hasattr(self, "cards_layout") else ui(11)
        # Four compact cards should fit in the default window.  Cards expand to
        # use any extra room, so the final column never leaves a large dead area.
        return max(1, (width + spacing) // (ui(200) + spacing))

    def card_width(self, columns: int) -> int:
        width = self._card_viewport_width()
        spacing = self.cards_layout.horizontalSpacing() if hasattr(self, "cards_layout") else ui(11)
        return max(ui(160), (width - spacing * (columns - 1)) // columns)

    def _card_viewport_width(self) -> int:
        if not hasattr(self, "scroll"):
            return ui(900)
        viewport = self.scroll.viewport()
        scrollbar = self.scroll.verticalScrollBar()
        # Before the grid has enough rows, the vertical bar has not appeared
        # yet. Reserve its width now so the final rightmost card never shifts
        # beneath it once the bar is shown.
        reserved_scrollbar = 0 if scrollbar.isVisible() else scrollbar.sizeHint().width()
        return max(ui(1), viewport.width() - reserved_scrollbar)

    def _show_cards_loading(self) -> None:
        if not hasattr(self, "_cards_loading_overlay"):
            return
        self._cards_loading_overlay.setGeometry(self.scroll.rect())
        self._cards_loading_overlay.raise_()
        self._cards_loading_overlay.show()
        self._cards_loading_timer.start(120)

    def _hide_cards_loading(self) -> None:
        self._cards_loading_timer.stop()
        self._cards_loading_overlay.hide()

    def _advance_cards_loading_spinner(self) -> None:
        self._cards_loading_frame = (self._cards_loading_frame + 1) % len(self._cards_loading_frames)
        self._cards_loading_spinner.setText(self._cards_loading_frames[self._cards_loading_frame])

    def _schedule_cards_refresh(self, *_args) -> None:
        """Coalesce resize events before reflowing visible cards."""
        if (
            self._card_refresh_pending
            or not self._cards_ready_for_reflow
            or not hasattr(self, "cards_layout")
        ):
            return
        self._card_refresh_pending = True
        QTimer.singleShot(120, self._refresh_cards_after_layout)

    def _refresh_cards_after_layout(self) -> None:
        self._card_refresh_pending = False
        self._reflow_cards()
        self._sync_content_right_edges()

    def _reflow_cards(self) -> None:
        """Reposition existing cards after a resize without rebuilding previews."""
        if not self._card_widgets:
            return
        columns = self.card_columns()
        card_width = self.card_width(columns)
        existing_columns = max(columns, self.cards_layout.columnCount())
        for column in range(existing_columns):
            self.cards_layout.setColumnMinimumWidth(column, card_width if column < columns else 0)
            self.cards_layout.setColumnStretch(column, 0)
        for index, card in enumerate(self._card_widgets.values()):
            self.cards_layout.removeWidget(card)
            card.setFixedWidth(card_width)
            self.cards_layout.addWidget(card, index // columns, index % columns, Qt.AlignTop)

    def _sync_content_right_edges(self) -> None:
        """Make controls end exactly where the visible card viewport ends."""
        self._content_alignment_pending = False
        if not hasattr(self, "scroll"):
            return
        scrollbar = self.scroll.verticalScrollBar()
        # Reserve the scrollbar width even before it is visible.  This keeps
        # the toolbar, pagination and footer actions from jumping right when
        # the first page becomes tall enough to show the bar.
        inset = scrollbar.width() or scrollbar.sizeHint().width()
        self.content_bar.layout().setContentsMargins(0, 0, inset, 0)
        self.pagination_bar.layout().setContentsMargins(0, 0, inset, 0)
        # Match both toolbar controls to one card column.  As the combo box
        # remains the final item in this right-aligned layout, its right edge
        # shares the right edge of the last card as well.
        if hasattr(self, "search_input") and hasattr(self, "collection_combo"):
            control_width = self.card_width(self.card_columns())
            self.collection_combo.setFixedWidth(control_width)
            # Keep the combo box anchored to the card grid's right edge, then
            # derive the search width from the combo box's stable left edge.
            # Using the search box's previous geometry here can retain a stale
            # oversized width during the first layout pass.
            if hasattr(self, "choose_button"):
                self.content_bar.layout().activate()
                search_right = self.collection_combo.mapToGlobal(QPoint(0, 0)).x() - self.cards_layout.horizontalSpacing()
                target_left = self.choose_button.mapToGlobal(QPoint(0, 0)).x()
                content_left = self.content_bar.mapToGlobal(QPoint(0, 0)).x()
                bar_gap = self.content_bar.layout().spacing()
                self.content_title_host.setFixedWidth(max(ui(1), target_left - content_left - bar_gap))
                self.search_input.setFixedWidth(max(ui(160), search_right - target_left))
                self._filter_controls.invalidate()
                self.content_bar.layout().invalidate()
                self.content_bar.layout().activate()
                self._filter_controls.activate()
        if hasattr(self, "_footer_action_buttons") and hasattr(self, "_header_action_buttons"):
            grid_gap = self.cards_layout.horizontalSpacing()
            self.action_host.layout().setSpacing(grid_gap)
            action_buttons = self._header_action_buttons + self._footer_action_buttons
            action_width = max(button.minimumSizeHint().width() for button in action_buttons)
            action_height = max(ui(1), max(button.minimumSizeHint().height() for button in action_buttons) - ui(2))
            for button in action_buttons:
                button.setFixedSize(action_width, action_height)
        if hasattr(self, "action_host"):
            # The footer starts after the fixed sidebar.  Mirror the content
            # area's outer gutter and scrollbar inset for a shared right edge.
            # The footer itself extends to the app edge, so include that outer
            # gutter as well when aligning its final action to the viewport.
            self.action_host.layout().setContentsMargins(ui(16), 0, ui(8) + inset, 0)

    def _schedule_content_alignment(self, *_args) -> None:
        if self._content_alignment_pending or not hasattr(self, "scroll"):
            return
        self._content_alignment_pending = True
        QTimer.singleShot(0, self._sync_content_right_edges)

    def collection_names_for(self, mod_id: str) -> list[str]:
        return [item.name for item in self.collections if mod_id in item.mod_ids]

    def show_card_context_menu(self, mod_id: str, global_pos) -> None:
        menu = QMenu(self)
        mod = self.mods.get(mod_id)
        if mod is None:
            return
        details_action = menu.addAction("查看详细信息")
        details_action.triggered.connect(lambda: self.show_mod_details(mod))
        source_action = menu.addAction("查看源文件")
        source_action.setToolTip("打开该 Mod 所在文件夹")
        source_action.triggered.connect(lambda: self.open_mod_source(mod))
        steam_action = menu.addAction("同步当前 Mod Steam 信息")
        steam_action.setEnabled(bool(mod.workshop_id) and not self.steam_sync_in_progress)
        steam_action.triggered.connect(lambda: self.sync_single_mod_steam(mod_id))
        delete_action = menu.addAction("删除 Mod")
        delete_action.setToolTip("删除该 Mod 文件及其关联预览图片")
        delete_action.triggered.connect(lambda: self.delete_mod(mod_id))
        add_menu = menu.addMenu("加入已保存的组合")
        existing = set(self.collection_names_for(mod_id))
        if not self.collections:
            action = add_menu.addAction("暂无组合，请先保存当前组合")
            action.setEnabled(False)
        for collection in self.collections:
            if collection.name in existing:
                existing_action = QWidgetAction(add_menu)
                existing_label = QLabel(
                    f'<span style="color:#dfe9f8;">{escape(collection.name)}</span>'
                    ' <span style="color:#ff6f7d; font-weight:700;">· 已存在</span>'
                )
                existing_label.setFixedHeight(ui(35))
                existing_label.setContentsMargins(ui(11), 0, ui(16), 0)
                existing_action.setDefaultWidget(existing_label)
                add_menu.addAction(existing_action)
            else:
                action = add_menu.addAction(collection.name)
                action.triggered.connect(lambda _=False, name=collection.name: self.add_mod_to_collection(mod_id, name))
        menu.exec_(global_pos)

    @staticmethod
    def open_mod_source(mod: Mod) -> None:
        file_path = Path(mod.file_path)
        if file_path.is_file():
            subprocess.Popen(["explorer.exe", "/select,", str(file_path)])
        else:
            QMessageBox.warning(self, "文件不存在", f"找不到 Mod 文件：\n{file_path}")

    def delete_mod(self, mod_id: str) -> None:
        """Remove only the selected Mod file and its recorded preview image."""
        mod = self.mods.get(mod_id)
        if mod is None:
            return
        targets = [Path(mod.file_path)]
        if mod.image_path:
            image_path = Path(mod.image_path)
            if image_path not in targets:
                targets.append(image_path)
        existing = [path for path in targets if path.is_file()]
        names = "\n".join(f"• {path.name}" for path in existing) or "• 未找到本地文件"
        message = f"确定删除以下 Mod 文件吗？\n\n{names}\n\n此操作无法恢复。"
        if QMessageBox.question(self, "删除 Mod", message) != QMessageBox.Yes:
            return
        failed: list[str] = []
        for path in existing:
            try:
                path.unlink()
                PREVIEW_CACHE.pop((str(path), ui(186), ui(76)), None)
            except OSError as exc:
                failed.append(f"{path.name}：{exc}")
        if failed:
            QMessageBox.critical(self, "删除失败", "\n".join(failed))
            return
        self.mods.pop(mod_id, None)
        for collection in self.collections:
            if mod_id in collection.mod_ids:
                collection.mod_ids.remove(mod_id)
                self.sync_collection_in_background(collection)
        self.steam_cache.pop(mod.workshop_id or "", None)
        self.storage.save_mods(self.mods)
        self.storage.save_collections(self.collections)
        self.storage.save_steam_cache(self.steam_cache)
        self._rebuild_conflict_index()
        self.refresh_collection_combo()
        self.refresh_cards()
        self.refresh_stats()

    def _show_content_widget(self, title: str, subtitle: str, widget: QWidget) -> None:
        self._list_snapshot = (
            self.content_title.text(), self.content_subtitle.text(), self.current_category, self.current_page,
        )
        self._list_scroll_position = self.scroll.verticalScrollBar().value()
        self._saved_card_widgets = list(self._card_widgets.items())
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
        self._content_mode = "detail"
        self._card_widgets = {}
        self.content_title.setText(title)
        self.content_subtitle.setText(subtitle)
        self.content_back_button.show()
        self.search_input.hide()
        self.collection_combo.hide()
        self.pagination_bar.hide()
        if hasattr(self, "pagination_spacer"):
            self.pagination_spacer.hide()
        self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.cards_layout.addWidget(widget, 0, 0)

    def show_mod_details(self, mod: Mod) -> None:
        host = QWidget()
        host.setObjectName("mainDetailsHost")
        host.setMinimumWidth(max(ui(480), self.scroll.viewport().width()))
        layout = QVBoxLayout(host)
        layout.setContentsMargins(ui(12), ui(6), ui(12), ui(20))
        layout.setSpacing(ui(12))
        top = QHBoxLayout()
        info = QVBoxLayout()
        name = QLabel(mod.title or mod.file_name)
        name.setObjectName("mainDetailsTitle")
        name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.addWidget(name)
        code = mod.workshop_id or Path(mod.file_name).stem
        for label, value in (("文件", mod.file_name), ("编号", code), ("作者", mod.author or "未知"), ("订阅", mod.display_subscriptions if mod.subscriptions else "暂无"), ("评分", f"{mod.rating:.1f}" if mod.rating else "暂无"), ("来源", "Steam 创意工坊" if mod.steam_loaded and mod.workshop_id else "本地文件"), ("状态", "已启用" if mod.active else "已禁用"), ("分类", "、".join(mod.categories) if mod.categories else "未分类")):
            field = QLabel(f"{label}　{value}")
            field.setObjectName("mainDetailsField")
            field.setWordWrap(True)
            field.setTextInteractionFlags(Qt.TextSelectableByMouse)
            info.addWidget(field)
        if mod.steam_loaded and mod.workshop_id:
            steam_link = QPushButton("在 Steam 创意工坊中查看")
            steam_link.setObjectName("steamDetailsLink")
            steam_link.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
            steam_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod.workshop_id}")))
            info.addWidget(steam_link, 0, Qt.AlignLeft)
        info.addStretch(1)
        top.addLayout(info, 1)
        preview = QLabel()
        preview.setObjectName("mainDetailsPreview")
        preview.setFixedSize(ui(340), ui(220))
        preview.setAlignment(Qt.AlignCenter)
        preview.setPixmap(make_preview_pixmap(mod, ui(320), ui(200)))
        top.addWidget(preview, 0, Qt.AlignTop)
        layout.addLayout(top)
        path = QLabel(f"文件路径　{mod.file_path}")
        path.setObjectName("mainDetailsField")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(path)
        description = QLabel(mod.description.strip() or "暂无描述")
        description.setObjectName("mainDetailsDescription")
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(description)
        layout.addStretch(1)
        self._show_content_widget("Mod 详细信息", "完整 Mod 信息；内容过多时可在此区域滚动查看", host)

    def show_mod_list(self) -> None:
        if self._content_mode != "detail":
            return
        snapshot = getattr(self, "_list_snapshot", None)
        saved_cards = getattr(self, "_saved_card_widgets", [])
        self._content_mode = "mods"
        self._set_status_selection(self.active_label if self._active_only_filter else self.total_label)
        if snapshot:
            title, subtitle, self.current_category, self.current_page = snapshot
            self.content_title.setText(title)
            self.content_subtitle.setText(subtitle)
        self.category_tree.blockSignals(True)
        for index in range(self.category_tree.topLevelItemCount()):
            item = self.category_tree.topLevelItem(index)
            if item.data(0, Qt.UserRole) == self.current_category:
                self.category_tree.setCurrentItem(item)
                break
        self.category_tree.blockSignals(False)
        if not saved_cards:
            self.refresh_cards()
            return
        clear_layout(self.cards_layout)
        self._card_widgets = dict(saved_cards)
        columns = self.card_columns()
        card_width = self.card_width(columns)
        for column in range(columns):
            self.cards_layout.setColumnMinimumWidth(column, card_width)
        for index, (_mod_id, card) in enumerate(saved_cards):
            card.setFixedWidth(card_width)
            card.show()
            self.cards_layout.addWidget(card, index // columns, index % columns, Qt.AlignTop)
        self.content_back_button.hide()
        self.search_input.show()
        self.collection_combo.show()
        self._update_pagination(len(self.filtered_mods()), max(1, (len(self.filtered_mods()) + self.page_size - 1) // self.page_size))
        self._sync_content_right_edges()
        scroll_position = getattr(self, "_list_scroll_position", 0)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_position))

    def add_mod_to_collection(self, mod_id: str, collection_name: str) -> None:
        for collection in self.collections:
            if collection.name == collection_name:
                if mod_id not in collection.mod_ids:
                    collection.mod_ids.append(mod_id)
                self.storage.save_collections(self.collections)
                self.sync_collection_in_background(collection)
                return

    def sync_collection_in_background(self, collection: ModCollection) -> None:
        addon_dirs = self.configured_addon_directories()
        if not addon_dirs:
            return
        snapshot = [deepcopy(self.mods[mod_id]) for mod_id in collection.mod_ids if mod_id in self.mods]
        worker = Worker(sync_collection_files, addon_dirs[0], collection.name, snapshot)
        worker.signals.failed.connect(lambda message: self._on_collection_sync_failed(collection.name, message))
        self.collection_sync_pool.start(worker)

    def _on_collection_sync_failed(self, collection_name: str, message: str) -> None:
        QMessageBox.warning(self, "组合文件同步失败", f"组合「{collection_name}」的文件同步失败：\n{message}")

    def filtered_mods(self) -> list[Mod]:
        mods = list(self.mods.values())
        if self._active_only_filter:
            mods = [mod for mod in mods if mod.active]
        if self.current_category != "all":
            if self.category_mode == "simple":
                mods = [
                    mod for mod in mods
                    if self.current_category in self._simple_categories_for(mod)
                ]
            else:
                mods = [mod for mod in mods if self.current_category in mod.categories]
        query = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""
        if query:
            mods = [mod for mod in mods if query in " ".join([mod.title, mod.author, mod.file_name, mod.workshop_id or ""]).lower()]
        for mod in mods:
            if mod.id not in self._mod_sort_cache:
                try:
                    self._mod_sort_cache[mod.id] = Path(mod.file_path).stat().st_mtime_ns
                except OSError:
                    self._mod_sort_cache[mod.id] = 0
        return sorted(
            mods,
            key=lambda mod: (
                mod.favorite,
                mod.favorite_at if mod.favorite else self._mod_sort_cache.get(mod.id, 0),
                mod.title.casefold(),
            ),
            reverse=True,
        )

    def show_active_mods(self) -> None:
        self._content_mode = "mods"
        self._active_only_filter = True
        self._set_status_selection(self.active_label)
        self._update_mod_filter_title()
        self.current_page = 0
        self.refresh_cards()

    def show_all_mods(self) -> None:
        self._content_mode = "mods"
        self._active_only_filter = False
        self._set_status_selection(self.total_label)
        self._update_mod_filter_title()
        self.current_page = 0
        self.refresh_cards()

    def _set_status_selection(self, selected_button: QPushButton) -> None:
        for button in (self.total_label, self.active_label, self.conflict_button):
            button.setProperty("selected", button is selected_button)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _update_mod_filter_title(self) -> None:
        if self._content_mode != "mods" or self.current_category != "all":
            return
        self.content_title.setText("已激活 Mod" if self._active_only_filter else "全部 Mod")

    def _simple_categories_for(self, mod: Mod) -> set[str]:
        cached = self._simple_category_cache.get(mod.id)
        if cached is None:
            cached = simple_categories(mod.categories, mod.title, mod.files, mod.file_name)
            self._simple_category_cache[mod.id] = cached
        return cached

    @staticmethod
    def _time_sort_key(mod: Mod) -> tuple[int, str]:
        """Sort by the VPK's local modification time, with a stable name tie-breaker."""
        try:
            modified_at = Path(mod.file_path).stat().st_mtime_ns
        except OSError:
            modified_at = 0
        return modified_at, mod.title.casefold()

    def refresh_stats(self) -> None:
        conflicts = sum(1 for mod in self.mods.values() if mod.conflict_with)
        active = sum(1 for mod in self.mods.values() if mod.active)
        self.total_label.setText(f"共计{len(self.mods)}")
        self.active_label.setText(f"已启用 {active}")
        self.conflict_button.setText(f"冲突 {conflicts}")
        self.conflict_button.setEnabled(conflicts > 0)
        if hasattr(self, "toggle_all_button"):
            all_active = bool(self.mods) and all(mod.active for mod in self.mods.values())
            self.toggle_all_button.setText("全部禁用" if all_active else "全部启动")
            self.toggle_all_button.setIcon(
                self.style().standardIcon(
                    QStyle.SP_DialogCancelButton if all_active else QStyle.SP_DialogApplyButton
                )
            )

    def refresh_collection_combo(self) -> None:
        self._updating_collection_combo = True
        self.collection_combo.clear()
        known_names = {collection.name for collection in self.collections}
        self._selected_collection_names &= known_names
        for collection in self.collections:
            self.collection_combo.addItem(collection.name, collection.name)
            index = self.collection_combo.count() - 1
            checked = collection.name in self._selected_collection_names
            item = self.collection_combo.model().item(index)
            item.setCheckable(True)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self._updating_collection_combo = False
        self._update_collection_combo_label()

    def _update_collection_combo_label(self) -> None:
        selected = [
            collection.name
            for collection in self.collections
            if collection.name in self._selected_collection_names
        ]
        if not selected:
            label = "选择组合"
        elif len(selected) == 1:
            label = selected[0]
        else:
            label = f"已选 {len(selected)} 个组合"
        self.collection_combo.lineEdit().setText(label)
        self.collection_combo.setToolTip("\n".join(selected) if selected else "勾选一个或多个组合以同时加载")

    def on_collection_selection_changed(self) -> None:
        if self._updating_collection_combo:
            return
        self._selected_collection_names = set(self.collection_combo.checked_values())
        self.save_selected_collection_names()
        self._update_collection_combo_label()
        if self._selected_collection_names:
            # Apply the selected collections before rebuilding the card list;
            # otherwise the active-only view briefly sees the previous state.
            self.apply_selected_collections()
            self.show_active_mods()
            self.restore_selected_collections_in_background()
        else:
            self.show_all_mods()
            # Coalesce quick successive checks into one state/card refresh.
            self._collection_apply_timer.start(80)

    def restore_selected_collections_in_background(self) -> None:
        addon_dirs = self.configured_addon_directories()
        if not addon_dirs:
            return
        show_restore_progress = not self.steam_sync_in_progress
        if show_restore_progress:
            self._progress_owner = "restore"
        self.steam_sync_progress.setRange(0, 1)
        self.steam_sync_progress.setValue(0)
        self._set_collection_restore_status(0, 0)
        if show_restore_progress:
            self.steam_sync_widget.show()
        worker = Worker(restore_collection_files, addon_dirs[0], sorted(self._selected_collection_names), progress_callback=None)
        worker.kwargs["progress_callback"] = worker.signals.progress.emit
        worker.signals.progress.connect(self._set_collection_restore_status)
        worker.signals.finished.connect(self.on_collection_restore_finished)
        worker.signals.failed.connect(self.on_collection_restore_failed)
        self.collection_sync_pool.start(worker)

    def on_collection_restore_finished(self, restored: int) -> None:
        if self._progress_owner == "restore":
            self._progress_owner = None
            self.steam_sync_widget.hide()
        # Apply the union of every checked collection even when no file had
        # to be restored. If files were restored, on_scan_finished applies it
        # once more after the new Mods are discovered.
        self.apply_selected_collections()
        if restored:
            self.scan_mods(False)

    def on_collection_restore_failed(self, message: str) -> None:
        if self._progress_owner == "restore":
            self._progress_owner = None
            self.steam_sync_widget.hide()
        self._on_collection_sync_failed("当前组合", message)

    def _set_collection_restore_status(self, completed: int, total: int) -> None:
        if self._progress_owner != "restore":
            return
        self.steam_sync_progress.setRange(0, max(total, 1))
        self.steam_sync_progress.setValue(completed)
        percent = round(completed * 100 / total) if total else 100
        label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
        if label is not None:
            label.setText(f"正在恢复组合文件… {completed}/{total}（{percent}%）")

    def _apply_pending_collection_selection(self) -> None:
        if self._selected_collection_names:
            self.apply_selected_collections()

    def save_selected_collection_names(self) -> None:
        self.settings["selected_collection_names"] = sorted(self._selected_collection_names)
        self.storage.save_settings(self.settings)

    def apply_selected_collections(self, write_addonlist: bool = False) -> None:
        selected = [
            collection
            for collection in self.collections
            if collection.name in self._selected_collection_names
        ]
        if not selected:
            for mod in self.mods.values():
                mod.active = False
            self._rebuild_conflict_index()
            self.storage.save_mods(self.mods)
            self._refresh_card_states()
            self.refresh_stats()
            return
        active_ids = set().union(*(collection.mod_ids for collection in selected))
        for mod in self.mods.values():
            mod.active = mod.id in active_ids
        self._rebuild_conflict_index()
        self.storage.save_mods(self.mods)
        if write_addonlist:
            self.write_addonlist()
        self._refresh_card_states()
        self.refresh_stats()

    def delete_collection(self, name: str) -> None:
        if not any(collection.name == name for collection in self.collections):
            QMessageBox.information(self, "无法删除", "找不到指定组合。")
            return
        if QMessageBox.question(self, "删除组合", f"确定删除组合「{name}」吗？") != QMessageBox.Yes:
            return
        self.collections = [collection for collection in self.collections if collection.name != name]
        self._selected_collection_names.discard(name)
        self.save_selected_collection_names()
        self.storage.save_collections(self.collections)
        addon_dirs = self.configured_addon_directories()
        if addon_dirs:
            try:
                delete_collection_folder(addon_dirs[0], name)
            except OSError as exc:
                QMessageBox.warning(self, "删除组合文件失败", f"组合记录已删除，但文件夹删除失败：{exc}")
        self.refresh_collection_combo()
        self.apply_selected_collections()

    def on_category_selected(self) -> None:
        if self._tree_rebuilding:
            return
        items = self.category_tree.selectedItems()
        if not items:
            return
        self.current_category = items[0].data(0, Qt.UserRole)
        self.content_title.setText(items[0].text(0))
        self._update_mod_filter_title()
        self.current_page = 0
        # Debounce rapid clicks: coalesce a burst of selections into a single
        # refresh so we never stack multiple full card rebuilds on one another.
        if self._category_select_timer is None:
            self._category_select_timer = QTimer(self)
            self._category_select_timer.setSingleShot(True)
            self._category_select_timer.timeout.connect(self._run_category_refresh)
        self._category_select_timer.start(0)

    def _run_category_refresh(self) -> None:
        if self._tree_rebuilding:
            return
        self.refresh_cards()

    def choose_directory(self) -> None:
        steam_game = self.find_steam_game_executable()
        saved_game = Path(self.settings["game_exe"]) if self.settings.get("game_exe") else None
        preferred_game = saved_game if saved_game and saved_game.exists() else steam_game
        initial_location = str(preferred_game.parent if preferred_game else Path.home())
        game_exe, _filter = QFileDialog.getOpenFileName(
            self,
            "选择 Left 4 Dead 2 游戏程序",
            initial_location,
            "Left 4 Dead 2 (left4dead2.exe)",
        )
        if not game_exe:
            return
        executable = Path(game_exe)
        if executable.name.casefold() != "left4dead2.exe":
            QMessageBox.warning(self, "文件不正确", "请选择 left4dead2.exe，而不是其他文件。")
            return
        addon_dirs = self.addon_directories(executable)
        if not addon_dirs:
            QMessageBox.warning(self, "目录不完整", "未找到 left4dead2\\addons 目录，请确认选择的是游戏目录中的 left4dead2.exe。")
            return
        self.settings["game_exe"] = str(executable.resolve())
        self.settings["game_dir"] = str(executable.parent.resolve())
        self.settings["mod_dir"] = str(addon_dirs[0].resolve())
        self.storage.save_settings(self.settings)
        self.scan_mods(True)

    @staticmethod
    def find_steam_game_executable() -> Path | None:
        """Find the Steam-installed L4D2 executable for the file dialog default."""
        steam_roots: list[Path] = []
        if winreg is not None:
            for root, subkey in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        for value_name in ("SteamPath", "InstallPath"):
                            try:
                                value, _ = winreg.QueryValueEx(key, value_name)
                            except OSError:
                                continue
                            if value:
                                steam_roots.append(Path(value))
                except OSError:
                    continue
        steam_roots.extend(
            Path(path) for path in (
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("PROGRAMFILES"),
            ) if path
        )

        library_roots: set[Path] = set()
        for steam_root in steam_roots:
            steam_root = steam_root.resolve()
            library_roots.add(steam_root)
            library_file = steam_root / "steamapps" / "libraryfolders.vdf"
            try:
                text = library_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Supports both old and new libraryfolders.vdf layouts.
            for match in re.finditer(r'"(?:path|\d+)"\s*"([^"]+)"', text, re.IGNORECASE):
                library_roots.add(Path(match.group(1).replace("\\\\", "\\")))

        for library_root in library_roots:
            candidate = library_root / "steamapps" / "common" / "Left 4 Dead 2" / "left4dead2.exe"
            if candidate.exists():
                return candidate.resolve()
        return None

    @staticmethod
    def addon_directories(executable: Path) -> list[Path]:
        game_root = executable.parent
        nested_addons = game_root / "left4dead2" / "addons"
        direct_addons = game_root / "addons" if game_root.name.casefold() == "left4dead2" else None
        addons = nested_addons if nested_addons.exists() else direct_addons
        if addons is None or not addons.exists():
            return []
        return [addons, addons / "workshop"]

    def configured_addon_directories(self) -> list[Path]:
        game_exe = self.settings.get("game_exe")
        return self.addon_directories(Path(game_exe)) if game_exe else []

    def scan_mods(self, refresh_all: bool) -> None:
        addon_dirs = self.configured_addon_directories()
        if not addon_dirs:
            QMessageBox.information(self, "需要选择游戏", "请先选择 left4dead2.exe。")
            return
        existing_dirs = [directory for directory in addon_dirs if directory.exists()]
        if not existing_dirs:
            QMessageBox.warning(self, "目录不存在", "未找到游戏的 addons 目录。")
            return
        self.set_busy(True, "正在扫描游戏 Mod…")
        worker = Worker(scan_mod_directory, existing_dirs, self.mods, refresh_all)
        worker.signals.finished.connect(self.on_scan_finished)
        worker.signals.failed.connect(self.on_worker_failed)
        self.thread_pool.start(worker)

    def reset_mods(self) -> None:
        if QMessageBox.question(self, "重新扫描", "将清除本地 Mod 元数据并重新扫描目录，是否继续？") == QMessageBox.Yes:
            self.storage.reset_mods()
            self.mods = {}
            self.scan_mods(True)

    def on_scan_finished(self, mods: dict[str, Mod]) -> None:
        old_active = {key for key, mod in self.mods.items() if mod.active}
        self.mods = mods
        self._simple_category_cache.clear()
        self._mod_sort_cache.clear()
        for key in old_active:
            if key in self.mods:
                self.mods[key].active = True
        self._apply_steam_cache(self.mods)
        # A collection switch may have restored files just before this scan.
        # Re-apply the selected collection so every newly discovered Mod in it
        # is enabled as well.
        self.apply_selected_collections()
        self._rebuild_conflict_index()
        self.storage.save_mods(self.mods)
        self.refresh_cards(); self.refresh_stats(); self.set_busy(False)

    def launch_game(self) -> None:
        game_exe = self.settings.get("game_exe")
        if not game_exe:
            QMessageBox.information(self, "需要选择游戏", "请先点击“选择游戏”，定位到 left4dead2.exe。")
            return
        executable = Path(game_exe)
        if not executable.exists():
            QMessageBox.warning(self, "游戏不存在", f"找不到游戏程序：{executable}\n请重新选择游戏目录。")
            return
        try:
            self.storage.save_mods(self.mods)
            if not self.write_addonlist():
                return
            if self.steam_is_installed():
                os.startfile("steam://rungameid/550")
            else:
                subprocess.Popen([str(executable)], cwd=str(executable.parent))
        except OSError as exc:
            QMessageBox.critical(self, "启动失败", f"游戏启动失败：{exc}")

    @staticmethod
    def steam_is_installed() -> bool:
        if winreg is not None:
            for root, subkey in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(root, subkey):
                        return True
                except OSError:
                    continue
        return any(
            path.exists()
            for path in (
                Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Steam" / "steam.exe",
                Path(os.environ.get("PROGRAMFILES", "")) / "Steam" / "steam.exe",
            )
        )

    def fetch_steam_info(self) -> None:
        if self.steam_sync_in_progress:
            self.cancel_steam_sync()
            return
        if not self.mods:
            QMessageBox.information(self, "暂无 Mod", "请先扫描 Mod 文件夹。")
            return
        # A full rescan creates fresh Mod objects.  Hydrate those from the
        # persisted cache before deciding which items still need a request.
        self._apply_steam_cache(self.mods)
        pending_mods = steam_sync_candidates(self.mods)
        if not pending_mods:
            QMessageBox.information(self, "Steam 信息已是最新", "所有可识别的 Workshop Mod 都已有本地 Steam 数据，无需重新请求。")
            return
        if QMessageBox.question(
            self,
            "开始同步 Steam 信息",
            f"将从 Steam 获取 {len(pending_mods)} 个尚未同步的 Mod 信息。已有本地数据的 Mod 不会重新请求。点击“确定”后将在后台继续执行，你可以继续进行其他操作。",
        ) != QMessageBox.Yes:
            return
        self.steam_sync_in_progress = True
        self._progress_owner = "steam"
        self._steam_cancel_event.clear()
        self.fetch_button.setEnabled(False)
        self.fetch_button.setText("")
        self.fetch_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserStop))
        self.fetch_button.setToolTip("取消 Steam 同步")
        self.fetch_button.setEnabled(True)
        total = len(pending_mods)
        self.steam_sync_progress.setRange(0, total)
        self.steam_sync_progress.setValue(0)
        self._set_steam_sync_status(0, total)
        self.steam_sync_widget.show()
        worker = Worker(fetch_steam_for_mods, deepcopy(pending_mods), progress_callback=None, cancel_event=self._steam_cancel_event)
        worker.kwargs["progress_callback"] = worker.signals.progress.emit
        worker.signals.progress.connect(self._set_steam_sync_status)
        worker.signals.finished.connect(self.on_steam_finished)
        worker.signals.failed.connect(self.on_steam_failed)
        worker.signals.cancelled.connect(self.on_steam_cancelled)
        self.thread_pool.start(worker)

    def sync_single_mod_steam(self, mod_id: str) -> None:
        if self.steam_sync_in_progress:
            return
        mod = self.mods.get(mod_id)
        if mod is None or not mod.workshop_id:
            QMessageBox.information(self, "无法同步", "当前 Mod 没有可识别的 Workshop ID。")
            return
        pending = deepcopy(mod)
        # This action explicitly refreshes the selected Mod, even when it has
        # already been synchronized before.
        pending.steam_loaded = False
        self.steam_sync_in_progress = True
        self._progress_owner = "steam"
        self._steam_cancel_event.clear()
        self.fetch_button.setEnabled(False)
        self.fetch_button.setText("")
        self.fetch_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserStop))
        self.fetch_button.setToolTip("取消 Steam 同步")
        self.fetch_button.setEnabled(True)
        self.steam_sync_progress.setRange(0, 1)
        self.steam_sync_progress.setValue(0)
        self._set_steam_sync_status(0, 1)
        self.steam_sync_widget.show()
        worker = Worker(fetch_steam_for_mods, {mod_id: pending}, progress_callback=None, cancel_event=self._steam_cancel_event)
        worker.kwargs["progress_callback"] = worker.signals.progress.emit
        worker.signals.progress.connect(self._set_steam_sync_status)
        worker.signals.finished.connect(self.on_steam_finished)
        worker.signals.failed.connect(self.on_steam_failed)
        worker.signals.cancelled.connect(self.on_steam_cancelled)
        self.thread_pool.start(worker)

    def on_steam_finished(self, mods: dict[str, Mod]) -> None:
        for mod_id, updated in mods.items():
            local = self.mods.get(mod_id)
            if local is None:
                continue
            for field in ("title", "author", "subscriptions", "rating", "description", "steam_tags", "steam_loaded", "categories"):
                setattr(local, field, getattr(updated, field))
        self.steam_sync_in_progress = False
        self._reset_steam_sync_controls()
        if self._progress_owner == "steam":
            self._progress_owner = None
            self.steam_sync_widget.hide()
        self._simple_category_cache.clear()
        self._card_cache.clear()
        self.storage.save_mods(self.mods)
        self._save_steam_cache()
        self.refresh_cards()
        self.refresh_tree()
        self.refresh_stats()
        QMessageBox.information(self, "Steam 同步完成", "Steam 信息已获取完成，页面已刷新。")

    def on_steam_failed(self, message: str) -> None:
        self.steam_sync_in_progress = False
        self._reset_steam_sync_controls()
        if self._progress_owner == "steam":
            self._progress_owner = None
            self.steam_sync_widget.hide()
        QMessageBox.critical(self, "Steam 同步失败", message)

    def cancel_steam_sync(self) -> None:
        if not self.steam_sync_in_progress:
            return
        self._steam_cancel_event.set()
        self.fetch_button.setText("")
        self.fetch_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserStop))
        self.fetch_button.setToolTip("正在取消 Steam 同步…")
        self.fetch_button.setEnabled(False)
        label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
        if label is not None:
            label.setText("正在取消 Steam 同步…")

    def on_steam_cancelled(self) -> None:
        self.steam_sync_in_progress = False
        self._reset_steam_sync_controls()
        if self._progress_owner == "steam":
            self._progress_owner = None
            self.steam_sync_widget.hide()
        QMessageBox.information(self, "Steam 同步已取消", "已停止后续 Mod 的 Steam 数据同步。")

    def _reset_steam_sync_controls(self) -> None:
        self.fetch_button.setText("同步 Steam")
        self.fetch_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        self.fetch_button.setToolTip("同步 Steam：获取创意工坊 Mod 的名称、订阅数和标签")
        self.fetch_button.setEnabled(True)

    def _set_steam_sync_status(self, completed: int, total: int) -> None:
        if self._progress_owner != "steam":
            return
        self.steam_sync_progress.setRange(0, max(total, 1))
        self.steam_sync_progress.setValue(completed)
        percent = round(completed * 100 / total) if total else 100
        label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
        if label is not None:
            label.setText(f"正在同步 Steam 数据… {completed}/{total}（{percent}%）")

    def set_all_mods_active(self, active: bool) -> None:
        if not self.mods:
            return
        for mod in self.mods.values():
            mod.active = active
        self._rebuild_conflict_index()
        self.storage.save_mods(self.mods)
        self._refresh_card_states()
        self.refresh_stats()

    def toggle_all_mods(self) -> None:
        all_active = bool(self.mods) and all(mod.active for mod in self.mods.values())
        self.set_all_mods_active(not all_active)

    def toggle_mod(self, mod_id: str) -> None:
        if mod_id in self.mods:
            self.mods[mod_id].active = not self.mods[mod_id].active
            affected = self._update_conflicts_for_toggle(mod_id)
            self.storage.save_mods(self.mods)
            self._refresh_card_states(affected)
            self.refresh_stats()

    def toggle_favorite(self, mod_id: str) -> None:
        mod = self.mods.get(mod_id)
        if mod is None:
            return
        mod.favorite = not mod.favorite
        if mod.favorite:
            mod.favorite_at = time.time_ns()
        else:
            mod.favorite_at = 0
        # Persist the new state, but do NOT re-sort immediately. The card only
        # updates its star visual; the ordering takes effect on the next refresh.
        self.storage.save_mods(self.mods)
        card = self._card_widgets.get(mod_id)
        if card is not None:
            card.set_favorite(mod.favorite)

    def show_conflicts(self) -> None:
        conflicted = [mod for mod in self.mods.values() if mod.active and mod.conflict_with]
        if not conflicted:
            return
        self._set_status_selection(self.conflict_button)
        host = QWidget()
        host.setObjectName("mainConflictHost")
        host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        loading = QLabel("正在生成冲突报告…")
        loading.setObjectName("emptyText")
        loading.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        loading.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        loading_layout = QVBoxLayout(host)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        loading_layout.addStretch(1)
        loading_layout.addWidget(loading, 0, Qt.AlignHCenter)
        loading_layout.addStretch(1)
        self._show_content_widget("冲突报告", "正在分析冲突关系…", host)
        # Let the loading view paint before creating report cards.
        QTimer.singleShot(80, lambda: self._build_conflict_report(host))

    def _build_conflict_report(self, host: QWidget) -> None:
        conflicted = [mod for mod in self.mods.values() if mod.active and mod.conflict_with]
        if not conflicted:
            return
        report_host = QWidget()
        report_host.setObjectName("mainConflictHost")
        layout = QVBoxLayout(report_host)
        layout.setContentsMargins(ui(12), ui(6), ui(12), ui(20))
        layout.setSpacing(ui(14))
        available_width = self.scroll.viewport().width() - ui(48)
        spacing = ui(15)
        columns = min(7, max(1, (available_width + spacing) // (ui(190) + spacing)))
        card_width = max(ui(160), (available_width - spacing * (columns - 1)) // columns)
        groups = ConflictDialog._conflict_groups(self.mods)
        conflict_names = {
            "rifle_ak47": "AK-47", "rifle_m16": "M16", "rifle_desert": "SCAR", "rifle_sg552": "SG552",
            "smg_uzi": "Uzi", "smg_silenced": "消音冲锋枪", "smg_mp5": "MP5",
            "shotgun_pump": "泵动霰弹枪", "shotgun_chrome": "铬合金霰弹枪", "shotgun_auto": "战术霰弹枪", "shotgun_spas": "SPAS-12",
            "pistol_p220": "P220", "pistol_dual": "双持手枪", "pistol_magnum": "马格南",
            "sniper_hunting": "猎枪", "sniper_military": "军用狙击枪", "sniper_awp": "AWP", "sniper_scout": "Scout",
            "melee": "近战武器", "melee_katana": "武士刀", "melee_fireaxe": "消防斧", "melee_chainsaw": "电锯",
        }
        self._conflict_report_groups = groups
        self._conflict_report_context = (host, report_host, layout, conflicted, columns, card_width, conflict_names)
        QTimer.singleShot(0, lambda: self._add_conflict_report_group(0))

    def _add_conflict_report_group(self, index: int) -> None:
        if self._content_mode != "detail" or not hasattr(self, "_conflict_report_context"):
            return
        loading_host, report_host, layout, conflicted, columns, card_width, conflict_names = self._conflict_report_context
        if index >= len(self._conflict_report_groups):
            layout.addStretch(1)
            self._show_completed_conflict_report(loading_host, report_host, len(conflicted))
            return
        number = index + 1
        group = self._conflict_report_groups[index]
        
        section = QFrame()
        section.setObjectName("mainConflictGroup")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(ui(12), ui(10), ui(12), ui(12))
        section_layout.setSpacing(ui(8))
        shared_paths: set[str] = set()
        for left_index, left in enumerate(group):
            for right in group[left_index + 1:]:
                shared_paths.update(self._conflict_paths.get(left.id, set()) & self._conflict_paths.get(right.id, set()))
        common_categories = set(group[0].categories)
        for mod in group[1:]:
            common_categories.intersection_update(mod.categories)
        targets = [conflict_names[key] for key in common_categories if key in conflict_names]
        reason = f"均替换 {' / '.join(sorted(targets)[:2])}" if targets else (f"共享 {len(shared_paths)} 个资源文件" if shared_paths else "存在重叠资源文件")
        heading = QLabel(f"冲突组 {number:02d}  ·  {len(group)} 个 Mod")
        heading.setObjectName("mainConflictGroupTitle")
        section_layout.addWidget(heading)
        detail = QLabel(f"冲突原因：{reason}")
        detail.setObjectName("mainConflictGroupReason")
        detail.setToolTip("\n".join(sorted(shared_paths)) if shared_paths else reason)
        detail.setWordWrap(True)
        section_layout.addWidget(detail)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(ui(15))
        grid.setVerticalSpacing(ui(15))
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for card_index, mod in enumerate(group):
            card = ConflictCard(mod, card_width)
            card.disable_requested.connect(self.disable_conflict_mod)
            grid.addWidget(card, card_index // columns, card_index % columns, Qt.AlignTop)
        section_layout.addWidget(grid_host)
        layout.addWidget(section)
        QTimer.singleShot(0, lambda: self._add_conflict_report_group(index + 1))

    def _show_completed_conflict_report(self, loading_host: QWidget, report_host: QWidget, conflict_count: int) -> None:
        """Swap the already-built report in only after the loading state has painted."""
        if self._content_mode != "detail":
            return
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.cards_layout.addWidget(report_host, 0, 0)
        report_host.show()
        self.content_subtitle.setText(
            f"发现 {conflict_count} 个已启用 Mod 存在资源冲突；双击卡片可禁用"
        )

    def disable_conflict_mod(self, mod_id: str) -> None:
        mod = self.mods.get(mod_id)
        if mod is None or not mod.active:
            return
        mod.active = False
        affected = self._update_conflicts_for_toggle(mod_id)
        self.storage.save_mods(self.mods)
        self._refresh_card_states(affected)
        self.refresh_stats()
        # The report is a one-shot view: its cards hold the old conflict graph.
        # Rebuild it after a double-click instead of only refreshing the library
        # cards, otherwise disabled Mods remain visible in the report.
        if self._content_mode == "detail":
            if any(mod.active and mod.conflict_with for mod in self.mods.values()):
                self.show_conflicts()
            else:
                empty = QLabel("No remaining conflicts")
                empty.setObjectName("emptyText")
                empty.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
                self._show_content_widget(self.content_title.text(), "No remaining conflicts", empty)

    def save_collection(self) -> None:
        active_ids = [mod.id for mod in self.mods.values() if mod.active]
        selected = self._selected_collection_names
        if len(selected) == 1:
            current_name = next(iter(selected))
            if not self.confirm_save_collection(current_name):
                return
            for collection in self.collections:
                if collection.name == current_name:
                    collection.mod_ids = active_ids
                    self.sync_collection_in_background(collection)
                    break
            self.storage.save_collections(self.collections)
            QMessageBox.information(self, "保存完成", f"已更新组合「{current_name}」。")
            return
        if not active_ids:
            QMessageBox.information(self, "没有已启用 Mod", "请先至少启用一个 Mod。")
            return
        name, ok = QInputDialog.getText(self, "保存 Mod 组合", "组合名称：")
        if ok and name.strip():
            name = name.strip()
            if not self.confirm_save_collection(name):
                return
            self.collections = [item for item in self.collections if item.name != name]
            self.collections.append(ModCollection(name=name, mod_ids=active_ids))
            self.storage.save_collections(self.collections)
            self.sync_collection_in_background(self.collections[-1])
            self._selected_collection_names = {name}
            self.save_selected_collection_names()
            self.refresh_collection_combo()
            QMessageBox.information(self, "保存完成", f"已保存「{name}」。")

    def save_collection_as_new(self) -> None:
        active_ids = [mod.id for mod in self.mods.values() if mod.active]
        if not active_ids:
            QMessageBox.information(self, "没有已启用 Mod", "请先至少启用一个 Mod。")
            return
        name, ok = QInputDialog.getText(self, "另存为新组合", "组合名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(collection.name == name for collection in self.collections):
            QMessageBox.warning(self, "无法保存", f"组合「{name}」已存在，请换一个名称。")
            return
        if not self.confirm_save_collection(name):
            return
        collection = ModCollection(name=name, mod_ids=active_ids)
        self.collections.append(collection)
        self.storage.save_collections(self.collections)
        self.sync_collection_in_background(collection)
        self._selected_collection_names = {name}
        self.save_selected_collection_names()
        self.refresh_collection_combo()

    def confirm_save_collection(self, collection_name: str) -> bool:
        message = (
            f"是否将当前mods存入[{collection_name}]中？\n\n"
            "保存后同步会复制相关mods到组合同名文件夹！"
        )
        return QMessageBox.question(
            self,
            "保存当前组合",
            message,
        ) == QMessageBox.Yes

    def write_addonlist(self) -> bool:
        addon_dirs = self.configured_addon_directories()
        if not addon_dirs:
            QMessageBox.warning(self, "无法写入", "请先选择有效的 Left 4 Dead 2 游戏目录。")
            return False
        addon_root = addon_dirs[0].resolve()
        addonlist_path = addon_root.parent / "addonlist.txt"
        entries: list[tuple[str, bool]] = []
        for mod in self.mods.values():
            file_path = Path(mod.file_path)
            try:
                relative_path = file_path.resolve().relative_to(addon_root)
            except ValueError:
                continue
            entries.append((str(relative_path).replace("/", "\\"), mod.active))
        lines = ['"AddonList"\n', "{\n"]
        for relative_path, active in sorted(entries, key=lambda item: item[0].casefold()):
            lines.append(f'\t"{relative_path}"\t\t"{"1" if active else "0"}"\n')
        lines.append("}\n")
        try:
            addonlist_path.parent.mkdir(parents=True, exist_ok=True)
            addonlist_path.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "写入失败", f"无法写入 addonlist.txt：{exc}")
            return False
        return True

    def on_worker_failed(self, message: str) -> None:
        self.set_busy(False)
        QMessageBox.critical(self, "操作失败", message)

    def set_busy(self, busy: bool, message: str = "") -> None:
        for button in (self.choose_button, self.refresh_button, self.fetch_button, self.toggle_all_button, self.save_button, self.save_as_button, self.launch_button):
            button.setEnabled(not busy)
        self.fetch_button.setEnabled(not busy and not self.steam_sync_in_progress)

    def closeEvent(self, event) -> None:
        self.save_selected_collection_names()
        self.storage.save_mods(self.mods)
        self.storage.save_collections(self.collections)
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_cards_loading_overlay") and self._cards_loading_overlay.isVisible():
            self._cards_loading_overlay.setGeometry(self.scroll.viewport().rect())
        if hasattr(self, "cards_layout"):
            self._schedule_cards_refresh()
            QTimer.singleShot(0, self._sync_content_right_edges)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_native_window_corner()
        QTimer.singleShot(0, self._sync_content_right_edges)

    def _apply_native_window_corner(self) -> None:
        """Ask Windows DWM to render smooth native rounded window corners."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33
            # DWMWCP_ROUND = 2
            preference = ctypes.c_int(2)
            dwmapi = ctypes.WinDLL("dwmapi")
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(33),
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
        except (AttributeError, OSError, TypeError, ValueError):
            # Older Windows versions or unusual test backends may not expose DWM.
            return

def steam_sync_candidates(mods: dict[str, Mod]) -> dict[str, Mod]:
    """Return only Workshop Mods without successfully cached Steam metadata."""
    return {
        mod_id: mod
        for mod_id, mod in mods.items()
        if mod.workshop_id and not mod.steam_loaded
    }


def fetch_steam_for_mods(mods: dict[str, Mod], progress_callback=None, cancel_event: Event | None = None) -> dict[str, Mod]:
    client = SteamClient()
    total = len(mods)
    for completed, mod in enumerate(mods.values(), start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelled()
        # Keep this guard here as well as in steam_sync_candidates(), so a
        # future caller cannot accidentally re-fetch cached entries.
        if mod.workshop_id and not mod.steam_loaded:
            info = client.fetch(mod.workshop_id)
            mod.title, mod.author = info.title or mod.title, info.author or mod.author
            mod.subscriptions = info.subscriptions if info.subscriptions is not None else mod.subscriptions
            mod.rating = info.rating if info.rating is not None else mod.rating
            mod.description = info.description or mod.description
            if info.tags is not None:
                mod.steam_tags = info.tags
            mod.steam_loaded = bool(info.title or info.author or info.subscriptions or info.rating or info.tags)
            mod.categories = infer_categories(
                mod.title,
                mod.files,
                steam_tags=mod.steam_tags,
                description=mod.description,
                file_name=mod.file_name,
            )
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelled()
        if progress_callback is not None:
            progress_callback(completed, total)
    return mods


def make_tag(text: str, color: str) -> QLabel:
    tag = QLabel(text)
    tag.setObjectName("tag")
    tag.setStyleSheet(f"#tag {{ background: {color}; }}")
    return tag


def make_tag_button(text: str, color: str, tooltip: str, handler) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("tagButton")
    button.setToolTip(tooltip)
    button.setStyleSheet(f"#tagButton {{ background: {color}; }}")
    button.clicked.connect(handler)
    return button


def make_preview_pixmap(mod: Mod, max_width: int = ui(188), max_height: int = ui(104)) -> QPixmap:
    cache_key = (mod.image_path or "__placeholder__", max_width, max_height)
    cached = PREVIEW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if mod.image_path and Path(mod.image_path).exists():
        pixmap = QPixmap(mod.image_path)
        if not pixmap.isNull():
            result = pixmap.scaled(max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            PREVIEW_CACHE[cache_key] = result
            return result
    pixmap = QPixmap(max_width, max_height)
    gradient = QLinearGradient(0, 0, pixmap.width(), pixmap.height())
    gradient.setColorAt(0, QColor("#263d61")); gradient.setColorAt(1, QColor("#151c29"))
    painter = QPainter(pixmap)
    painter.fillRect(pixmap.rect(), gradient)
    painter.setPen(QColor("#91a8c9"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "L4D2\nMOD")
    painter.end()
    PREVIEW_CACHE[cache_key] = pixmap
    return pixmap


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(TITLE_ICON)))
    window = MainWindow()
    window.show()
    return app.exec_()
