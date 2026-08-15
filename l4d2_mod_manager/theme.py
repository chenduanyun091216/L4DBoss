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
    QAbstractItemView, QProgressBar, QPushButton, QScrollArea, QSizeGrip, QSizePolicy, QSplitter, QStyle,
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
# 使用手册（GitHub README）地址，标题右键菜单“使用手册”使用。
MANUAL_URL = "https://github.com/chenduanyun091216/L4DBoss/blob/main/README.md"
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

# QToolTip 是无父级的顶层控件，不继承窗口级样式表。该样式在应用启动时
# 一次性设置到 QApplication 上（固定深色，任何主题下都清晰可读），之后
# 切换主题只更换窗口级样式表，避免 QApplication.setStyleSheet 的全量重抛光。
TOOLTIP_QSS = (
    "QToolTip { background: #202c3d; color: #e9f1ff; border: 1px solid #496282;"
    " border-radius: 5px; padding: 6px 8px; font-size: 11px; }"
)

# Programmatically painted colors per theme. Keys are stable identifiers used
# by the custom paintEvent/draw code; each theme maps them to concrete colors.
THEME_PALETTE: dict[str, dict[str, str]] = {
    "dark": {
        "surface": "#0a0e16",
        "panel": "#121826",
        "panel_border": "#5f83b5",
        "tree_default": "#687384",
        "tree_favorite": "#f5b301",
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
        "tree_favorite": "#c98a00",
        "tree_expand": "#5a6a82",
        "toggle_off_border": "#b9c6d8",
        "toggle_off_fill": "#c7d3e4",
        "toggle_on_border": "#7fb0ff",
        "toggle_on_fill": "#2d65d6",
        "toggle_knob": "#ffffff",
        "link": "#2d65d6",
    },
    "titanium": {
        "surface": "#9a9ea4",
        "panel": "#adb1b7",
        "panel_border": "#6d7278",
        "tree_default": "#3a4149",
        "tree_favorite": "#c98a00",
        "tree_expand": "#4a5058",
        "toggle_off_border": "#8b9097",
        "toggle_off_fill": "#a7abb1",
        "toggle_on_border": "#4d83eb",
        "toggle_on_fill": "#2d65d6",
        "toggle_knob": "#ffffff",
        "link": "#1a3a7e",
    },
}


def theme_color(key: str, fallback_theme: str = "dark") -> str:
    """Look up a programmatic-paint color for the active theme."""
    palette = THEME_PALETTE.get(ACTIVE_THEME, THEME_PALETTE[fallback_theme])
    return palette.get(key, THEME_PALETTE[fallback_theme][key])


# Opacity of the full-window background image per theme. Higher values make
# the wallpaper stand out more; keep it low enough for text/panels to stay
# readable. Consumed by BackgroundSurface.paintEvent.
THEME_BG_OPACITY = {
    "dark": 0.16,
    "titanium": 0.55,
    "light": 0.14,
}


def theme_bg_opacity(fallback_theme: str = "dark") -> float:
    """Opacity used to paint the full-window background image."""
    return THEME_BG_OPACITY.get(ACTIVE_THEME, THEME_BG_OPACITY[fallback_theme])


# Ordered list of selectable themes (label shown in the theme switcher).
THEME_ORDER = ["dark", "titanium", "light"]
THEME_LABELS = {
    "dark": "深渊蓝",
    "titanium": "钛色灰",
    "light": "晴空白",
}
THEME_HINTS = {
    "dark": "深渊蓝：深色背景，护眼低亮，适合夜间使用",
    "titanium": "钛色灰：中性灰色调，介于深蓝与晴白之间，质感内敛，背景图更突出",
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
    #hoverOverlay { background: transparent; }
    #hoverHintBox { background: #23344c; border: 1px solid #355577; border-radius: 8px; }
    #hoverHintChip { color: #bcd7ff; font-size: 11px; font-weight: 700; }
    #headerButton, #headerButtonSecondary, #primaryButton, #secondaryButton, #launchButton, #toggleAllButton { background: #273347; color: #d9e4f4; border: 1px solid #38465c; border-radius: 7px; padding: 5px 10px; font-weight: 700; }
    #headerButton:hover, #headerButtonSecondary:hover, #primaryButton:hover, #secondaryButton:hover, #launchButton:hover, #toggleAllButton:hover { background: #3a5378; color: white; border: 2px solid #6aa0ff; }
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
    #launchButton { background: #273347; color: #d9e4f4; border: 1px solid #38465c; border-radius: 7px; padding: 5px 10px; font-weight: 700; }
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
    QTreeWidget#categoryTree::item:selected { background: #c9a227; color: #241c08; font-weight: 700; }
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
    #modCard[favorite="true"] { border: 2px solid #f5b301; background: #27291b; }
    #modCardActive[favorite="true"] { border: 2px solid #f5b301; background: #203b2d; }
    #modCardConflict[favorite="true"] { border: 2px solid #f5b301; background: #4b2322; }
    #modCard[favorite="true"]:hover { border: 2px solid #ffc83d; background: #2e3122; }
    #modCardActive[favorite="true"]:hover { border: 2px solid #ffc83d; background: #274639; }
    #modCardConflict[favorite="true"]:hover { border: 2px solid #ffc83d; background: #5d2826; }
    #preview { background: #111821; border-radius: 7px; }
    #cardTitle { color: #f2f6fc; font-size: 13px; font-weight: 700; line-height: 1.32; }
    #cardMeta { color: #91a0b4; font-size: 10px; }
    #typeSummary { color: #91a0b4; font-size: 9px; font-weight: 600; padding: 0; }
    #tag, #tagButton { min-height: 20px; max-height: 20px; color: #ffffff; border-radius: 4px; padding: 0 6px; font-size: 9px; font-weight: 700; }
    #cardAction, #cardActionActive { min-height: 24px; max-height: 24px; border-radius: 6px; font-size: 11px; font-weight: 700; }
    #cardAction { color: #cbd7e8; background: #253247; border: 1px solid #34445c; }
    #cardAction:hover { color: white; background: #3c78ee; border: 2px solid #5b8ced; }
    #cardActionActive { color: #cbd7e8; background: #253247; border: 1px solid #34445c; }
    #cardActionActive:hover { color: white; background: #3c78ee; border: 2px solid #5b8ced; }
    #tagButton { border: 0; }
    #tagButton:hover { border: 1px solid #d8e7ff; padding: 0 5px; }
    #favoriteStar { background: transparent; border: none; color: #6c7c93; font-size: 18px; font-weight: 700; padding: 0; }
    #favoriteStar:hover { color: #ffc83d; }
    #favoriteStar:checked { color: #f5b301; }
    #emptyText { color: #9db2d0; background: transparent; border: 0; padding: 0; font-size: 15px; font-weight: 500; line-height: 1.7; letter-spacing: 0.5px; }
    #paginationBar { min-height: 22px; }
    #paginationButton { min-height: 0; max-height: 22px; color: #cbd7e8; background: #253247; border: 1px solid #34445c; border-radius: 5px; padding: 0 6px; font-size: 11px; }
    #paginationButton:hover { color: white; background: #2d65d6; border-color: #2d65d6; }
    #paginationButton:disabled { color: #687384; background: #1b222d; border-color: #2d3747; }
    #pageLabel { color: #91a0b4; min-width: 40px; font-size: 11px; qproperty-alignment: AlignCenter; }
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
    #appToast { background: #18212e; border: 1px solid #3a4a61; border-radius: 10px; }
    #toastIcon { color: white; background: #2f9e63; border-radius: 11px; font-size: 13px; font-weight: 800; }
    #toastText { color: #e5edf9; font-size: 13px; line-height: 1.5; }
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
    #hoverOverlay { background: transparent; }
    #hoverHintBox { background: #f5f9ff; border: 1px solid #9db6d6; border-radius: 8px; }
    #hoverHintChip { color: #2b5bb8; font-size: 11px; font-weight: 700; }
    #headerButton, #headerButtonSecondary, #primaryButton, #secondaryButton, #launchButton, #toggleAllButton { background: #edf1f9; color: #24334c; border: 1px solid #aab6c8; border-radius: 7px; padding: 5px 10px; font-weight: 700; }
    #headerButton:hover, #headerButtonSecondary:hover, #primaryButton:hover, #secondaryButton:hover, #launchButton:hover, #toggleAllButton:hover { background: #ffffff; color: #0b2a52; border: 2px solid #4d83eb; }
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
    QTreeWidget#categoryTree::item:selected { background: #c9a227; color: #241c08; font-weight: 700; }
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
    #modCardActive { border: 2px solid #1c4fd0; background: rgba(170, 200, 247, 255); }
    #modCardActive:hover { background: rgba(159, 192, 242, 255); border: 2px solid #143da8; }
    #modCardConflict { border: 2px solid #d8363f; background: rgba(242, 188, 196, 255); }
    #modCardConflict:hover { background: rgba(235, 176, 186, 255); border: 2px solid #c52832; }
    #modCard[favorite="true"] { border: 2px solid #c98a00; background: #fdf5d7; }
    #modCardActive[favorite="true"] { border: 2px solid #c98a00; background: #e2e0ba; }
    #modCardConflict[favorite="true"] { border: 2px solid #c98a00; background: #f4dcbd; }
    #modCard[favorite="true"]:hover { border: 2px solid #f5b301; background: #fdf1c2; }
    #modCardActive[favorite="true"]:hover { border: 2px solid #f5b301; background: #dad8ad; }
    #modCardConflict[favorite="true"]:hover { border: 2px solid #f5b301; background: #efcda4; }
    #preview { background: #eef2f8; border-radius: 7px; }
    #cardTitle { color: #1d2b43; font-size: 13px; font-weight: 700; line-height: 1.32; }
    #cardMeta { color: #66748c; font-size: 10px; }
    #typeSummary { color: #66748c; font-size: 9px; font-weight: 600; padding: 0; }
    #tag, #tagButton { min-height: 20px; max-height: 20px; color: #ffffff; border-radius: 4px; padding: 0 6px; font-size: 9px; font-weight: 700; }
    #cardAction, #cardActionActive { min-height: 24px; max-height: 24px; border-radius: 6px; font-size: 11px; font-weight: 700; }
    #cardAction { color: #3c4e6b; background: #e9eef7; border: 1px solid #aab6c8; }
    #cardAction:hover { color: white; background: #3c78ee; border: 2px solid #2d65d6; }
    #cardActionActive { color: #3c4e6b; background: #e9eef7; border: 1px solid #aab6c8; }
    #cardActionActive:hover { color: white; background: #3c78ee; border: 2px solid #2d65d6; }
    #tagButton { border: 0; }
    #tagButton:hover { border: 1px solid #7fa6e2; padding: 0 5px; }
    #favoriteStar { background: transparent; border: none; color: #b3bccb; font-size: 18px; font-weight: 700; padding: 0; }
    #favoriteStar:hover { color: #f5b301; }
    #favoriteStar:checked { color: #c98a00; }
    #emptyText { color: #5f718e; background: transparent; border: 0; padding: 0; font-size: 15px; font-weight: 500; line-height: 1.7; letter-spacing: 0.5px; }
    #paginationBar { min-height: 22px; }
    #paginationButton { min-height: 0; max-height: 22px; color: #3c4e6b; background: #e9eef7; border: 1px solid #aab6c8; border-radius: 5px; padding: 0 6px; font-size: 11px; }
    #paginationButton:hover { color: white; background: #2d65d6; border-color: #2d65d6; }
    #paginationButton:disabled { color: #98a5b8; background: #eef1f6; border-color: #d8dee8; }
    #pageLabel { color: #66748c; min-width: 40px; font-size: 11px; qproperty-alignment: AlignCenter; }
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
    #appToast { background: #ffffff; border: 1px solid #aab6c8; border-radius: 10px; }
    #toastIcon { color: white; background: #2f9e63; border-radius: 11px; font-size: 13px; font-weight: 800; }
    #toastText { color: #2a3a54; font-size: 13px; line-height: 1.5; }
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
""",
    "titanium": r"""
    QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"; }
    QMainWindow, QDialog { background: transparent; color: #e3eaf4; }
    #appSurface { background: transparent; border-radius: 14px; }
    #header { background: transparent; border-bottom: 1px solid #7e848b; border-top-left-radius: 14px; border-top-right-radius: 14px; }
    #brand { color: #f2f6fc; font-size: 20px; font-weight: 800; letter-spacing: 2px; }
    #brandButton { color: #f2f6fc; background: transparent; border: 0; padding: 0; font-size: 20px; font-weight: 800; letter-spacing: 2px; text-align: left; }
    #brandButton:hover { color: #9fc3ff; }
    #brandCredit { color: #b9c6d6; font-size: 11px; font-weight: 700; }
    #brandSub, #contentSubtitle { color: #a9b6c6; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
    #headerHint { color: #c2cddb; font-size: 11px; font-weight: 600; padding-right: 8px; }
    #hoverOverlay { background: transparent; }
    #hoverHintBox { background: #3d4753; border: 1px solid #aab1ba; border-radius: 8px; }
    #hoverHintChip { color: #dbe4ef; font-size: 11px; font-weight: 700; }
    #headerButton, #headerButtonSecondary { background: transparent; color: #e3eaf4; border: 1px solid #aab1ba; border-radius: 7px; padding: 5px 10px; font-weight: 700; }
    #headerButton:hover, #headerButtonSecondary:hover { background: #2d65d6; color: #ffffff; border: 2px solid #2d65d6; }
    /* 底部四个操作按钮（全部启动/保存/另存为/启动游戏）：透明底 + 国网绿描边，悬浮时背景变为国网绿 */
    #toggleAllButton, #primaryButton, #secondaryButton, #launchButton { background: transparent; color: #e3eaf4; border: 1px solid #aab1ba; border-radius: 7px; padding: 5px 10px; font-weight: 700; }
    #toggleAllButton:hover, #primaryButton:hover, #secondaryButton:hover, #launchButton:hover { background: #2d65d6; color: #ffffff; border: 2px solid #2d65d6; }
    #headerIconButton { background: #39414c; border: 0; border-radius: 7px; padding: 0; }
    #headerIconButton:hover { background: #2d65d6; border: 0; }
    QToolTip { color: #e3eaf4; background: #2e3640; border: 1px solid #4a545f; border-radius: 5px; padding: 5px 8px; }
    #launchButton:disabled { color: #8a94a0; background: transparent; border-color: #4a545f; }
    #totalModCount, #activeModCount { background: transparent; border: 0; padding: 0; font-weight: 700; }
    #totalModCount { color: #9fc3ff; }
    #activeModCount { color: #9fc3ff; }
    #totalModCount:hover, #activeModCount:hover { text-decoration: underline; }
    #totalModCount[selected="true"] { border: 1px solid #9fc3ff; border-radius: 6px; padding: 3px 6px; }
    #activeModCount[selected="true"] { border: 1px solid #9fc3ff; border-radius: 6px; padding: 3px 6px; }
    #sidebar { background: transparent; border-right: 1px solid #7e848b; }
    #sectionLabel { color: #c7d1de; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
    #categorySwitchLabel { color: #d0d9e5; font-size: 11px; font-weight: 700; }
    #sideHint { color: #c8d3e0; font-size: 11px; line-height: 1.45; padding: 10px; background: #39414c; border-radius: 7px; }
    QTreeWidget { background: transparent; border: 0; color: #dce5f0; outline: none; font-size: 13px; }
    QTreeWidget::item { min-height: 24px; border-radius: 6px; padding: 2px 6px; font-weight: 600; }
    QTreeWidget::item:hover { background: #3a434e; color: #ffffff; }
    QTreeWidget::item:selected { background: #2d65d6; color: white; font-weight: 700; }
    QTreeWidget#categoryTree::item:selected { background: #c9a227; color: #241c08; font-weight: 700; }
    QScrollArea { border: 0; background: transparent; }
    #cardsScroll, #cardsViewport, #cardsHost { background: transparent; }
    #cardsLoadingOverlay { background: rgba(24, 30, 38, 110); }
    #cardsLoadingPanel { background: #39414c; border: 2px solid #5486ec; border-radius: 12px; }
    #cardsLoadingSpinner { color: #9fc3ff; font-size: 30px; font-weight: 700; }
    #cardsLoadingLabel { color: #c2cddb; font-size: 13px; font-weight: 700; }
    QScrollBar:vertical { background: #3a424d; width: 8px; margin: 5px 0 5px 0; border-radius: 4px; }
    QScrollBar::handle:vertical { background: #656d78; min-height: 42px; border-radius: 4px; }
    QScrollBar::handle:vertical:hover { background: #7a838f; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QScrollBar:horizontal { background: #3a424d; height: 8px; margin: 0 4px 3px 4px; border-radius: 4px; }
    QScrollBar::handle:horizontal { background: #656d78; min-width: 42px; border-radius: 4px; }
    QScrollBar::handle:horizontal:hover { background: #7a838f; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
    #contentTitle { color: #f2f6fc; font-size: 22px; font-weight: 800; }
    QLineEdit, QComboBox { min-height: 32px; background: #3f4854; color: #e3eaf4; border: 1px solid #56606c; border-radius: 7px; padding: 0 11px; }
    QLineEdit:focus, QComboBox:focus { border-color: #2d65d6; background: #47505d; }
    #searchInput { min-width: 235px; }
    #collectionCombo { padding-left: 12px; padding-right: 30px; font-weight: 600; }
    #collectionCombo QLineEdit { background: transparent; border: 0; padding: 0; color: #e3eaf4; font-weight: 600; }
    #collectionCombo:hover { background: #47505d; border-color: #2d65d6; }
    #collectionCombo::drop-down { subcontrol-origin: padding; subcontrol-position: top right; border: 0; width: 30px; }
    #collectionComboMenu { background: #39414c; color: #e3eaf4; border: 1px solid #56606c; border-radius: 8px; outline: 0; padding: 5px; selection-background-color: transparent; }
    #collectionComboMenu::item { min-height: 25px; border: 1px solid transparent; border-radius: 6px; padding: 0 12px; margin: 1px 0; }
    #collectionComboMenu::item:hover { background: #4a5462; border-color: #2d65d6; color: #ffffff; }
    #collectionComboMenu::item:selected { background: #2d65d6; border-color: #4d83eb; color: #ffffff; font-weight: 700; }
    #collectionComboMenu QScrollBar:vertical { background: transparent; width: 7px; margin: 7px 3px 7px 0; }
    #collectionComboMenu QScrollBar::handle:vertical { background: #656d78; min-height: 30px; border-radius: 3px; }
    #collectionComboMenu QScrollBar::handle:vertical:hover { background: #7a838f; }
    #modCard, #modCardActive, #modCardConflict { background: rgba(210, 214, 220, 112); border: 1px solid #8a919b; border-radius: 10px; }
    #modCard:hover { background: rgba(223, 226, 231, 140); border: 2px solid #7089c0; }
    #modCardActive { border: 2px solid #3f6fce; background: rgba(150, 182, 232, 242); }
    #modCardActive:hover { background: rgba(134, 170, 228, 248); border: 2px solid #2f5cb0; }
    #modCardConflict { border: 2px solid #cf4a58; background: rgba(244, 180, 192, 242); }
    #modCardConflict:hover { background: rgba(238, 164, 178, 248); border: 2px solid #b03a47; }
    #modCard[favorite="true"] { border: 2px solid #a8893f; background: rgba(216, 210, 172, 112); }
    #modCardActive[favorite="true"] { border: 2px solid #a8893f; background: rgba(178, 184, 148, 242); }
    #modCardConflict[favorite="true"] { border: 2px solid #a8893f; background: rgba(240, 194, 152, 242); }
    #modCard[favorite="true"]:hover { border: 2px solid #c5a74e; background: rgba(224, 218, 180, 140); }
    #modCardActive[favorite="true"]:hover { border: 2px solid #c5a74e; background: rgba(166, 178, 136, 248); }
    #modCardConflict[favorite="true"]:hover { border: 2px solid #c5a74e; background: rgba(232, 180, 138, 248); }
    #preview { background: #bcc1c9; border-radius: 7px; }
    #cardTitle { color: #3c4652; font-size: 13px; font-weight: 700; line-height: 1.32; }
    #cardMeta { color: #5f6a76; font-size: 10px; }
    #typeSummary { color: #5f6a76; font-size: 9px; font-weight: 600; padding: 0; }
    #tag, #tagButton { min-height: 20px; max-height: 20px; color: #ffffff; border-radius: 4px; padding: 0 6px; font-size: 9px; font-weight: 700; }
    #cardAction, #cardActionActive { min-height: 24px; max-height: 24px; border-radius: 6px; font-size: 11px; font-weight: 700; }
    #cardAction { color: #3c4652; background: #acb1b8; border: 1px solid #8b929c; }
    #cardAction:hover { color: white; background: #7d93c4; border: 2px solid #7089c0; }
    #cardActionActive { color: #3c4652; background: #acb1b8; border: 1px solid #8b929c; }
    #cardActionActive:hover { color: white; background: #7d93c4; border: 2px solid #7089c0; }
    #tagButton { border: 0; }
    #tagButton:hover { border: 1px solid #8b929c; padding: 0 5px; }
    #favoriteStar { background: transparent; border: none; color: #969da7; font-size: 18px; font-weight: 700; padding: 0; }
    #favoriteStar:hover { color: #c5a74e; }
    #favoriteStar:checked { color: #a8893f; }
    #emptyText { color: #c2cddb; background: transparent; border: 0; padding: 0; font-size: 15px; font-weight: 500; line-height: 1.7; letter-spacing: 0.5px; }
    #paginationBar { min-height: 22px; }
    #paginationButton { min-height: 0; max-height: 22px; color: #e3eaf4; background: transparent; border: 1px solid #aab1ba; border-radius: 5px; padding: 0 6px; font-size: 11px; }
    #paginationButton:hover { color: white; background: #2d65d6; border-color: #2d65d6; }
    #paginationButton:disabled { color: #8a94a0; background: transparent; border-color: #4a545f; }
    #pageLabel { color: #b9c6d6; min-width: 40px; font-size: 11px; qproperty-alignment: AlignCenter; }
    #steamSyncStatus { background: transparent; border: 1px solid #aab1ba; border-radius: 7px; }
    #steamSyncLabel { color: #dbe4ef; font-size: 11px; font-weight: 700; }
    #steamSyncProgress { min-height: 6px; max-height: 6px; border: 0; border-radius: 3px; background: #c3c9d1; }
    #steamSyncProgress::chunk { border-radius: 3px; background: #9fc3ff; }
    #footer { background: transparent; border-top: 1px solid #7e848b; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #footer QLabel { color: #b9c6d6; padding-right: 12px; }
    #conflictButton { color: #ff9aa3; background: transparent; border: 0; font-weight: 700; }
    #conflictButton:hover { text-decoration: underline; }
    #conflictButton:disabled { color: #8a94a0; }
    #conflictButton[selected="true"] { border: 1px solid #ff8f99; border-radius: 6px; padding: 3px 6px; }
    #closeButton { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0; border: 0; color: #dbe4ef; background: transparent; font-size: 18px; font-weight: 800; }
    #closeButton:hover { color: #ff7a85; background: transparent; }
    #windowControlButton { padding: 0; border: 0; color: #dbe4ef; background: transparent; font-size: 16px; font-weight: 700; }
    #windowControlButton:hover { color: #ffffff; background: #3f4854; border-radius: 5px; }
    #dialogHeader { background: #39414c; border-bottom: 1px solid #56606c; border-top-left-radius: 14px; border-top-right-radius: 14px; }
    #dialogTitle { color: #f2f6fc; font-size: 17px; font-weight: 800; }
    #modDetailsContent { background: #363e48; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #modDetailsTitle { color: #f2f6fc; font-size: 16px; font-weight: 800; }
    #modDetailsKey { color: #b9c6d6; font-size: 11px; font-weight: 700; }
    #modDetailsValue { color: #dbe4ef; font-size: 11px; }
    #modDetailsDescription { color: #c8d3e0; background: #313945; border: 1px solid #4a545f; border-radius: 7px; padding: 9px; font-size: 11px; }
    #contentBackButton { color: #b9c6d6; background: transparent; border: 0; padding: 0; }
    #contentBackButton:hover { color: #9fc3ff; background: rgba(122, 170, 255, 40); border-radius: 5px; }
    #mainDetailsHost, #mainConflictHost { background: transparent; }
    #mainDetailsPreview { background: #3a424d; border: 1px solid #4a545f; border-radius: 9px; }
    #mainDetailsTitle { color: #f2f6fc; font-size: 18px; font-weight: 800; }
    #mainDetailsField { color: #c2cddb; font-size: 12px; padding: 2px 0; }
    #mainDetailsDescription { color: #c8d3e0; background: rgba(54, 62, 72, 235); border: 1px solid #4a545f; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.55; }
    #steamDetailsLink { min-height: 28px; color: #ffffff; background: #285b9d; border: 1px solid #4b82c8; border-radius: 6px; padding: 0 10px; font-size: 11px; font-weight: 700; }
    #steamDetailsLink:hover { background: #3470bc; color: white; }
    #mainConflictGroup { background: rgba(250, 240, 242, 115); border: 1px solid #d99aa2; border-radius: 10px; }
    #mainConflictGroupTitle { color: #b0404c; font-size: 12px; font-weight: 800; }
    #mainConflictGroupReason { color: #4a3a42; background: rgba(251, 238, 240, 125); border: 1px solid #e0aab1; border-radius: 5px; padding: 5px 7px; font-size: 10px; }
    #dialogSubtitle { color: #b9c6d6; font-size: 11px; }
    #aboutContent { background: #363e48; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #aboutBrand { color: #f2f6fc; font-size: 25px; font-weight: 800; letter-spacing: 2px; }
    #aboutVersion { color: #9fc3ff; font-size: 12px; font-weight: 700; }
    #aboutDesigner { color: #b9c6d6; font-size: 12px; font-weight: 500; padding: 2px 0; background: transparent; border: 0; }
    #aboutDescription { color: #c2cddb; font-size: 12px; line-height: 1.55; }
    #conflictBody { background: rgba(201, 205, 212, 96); border: 0; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #conflictScroll { background: transparent; border: 0; }
    #conflictViewport { background: rgba(201, 205, 212, 96); border: 0; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
    #conflictHost { background: transparent; border: 0; }
    #groupCardScroll, #groupCardsHost { background: rgba(201, 205, 212, 96); border: 0; border-radius: 7px; }
    #conflictGroup { background: rgba(214, 218, 224, 108); border: 1px solid #9aa2ac; border-radius: 10px; }
    #conflictGroupLabel { color: #b0404c; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
    #conflictCard { background: rgba(247, 230, 233, 115); border: 1px solid #d99aa2; border-radius: 10px; }
    #conflictCard:hover { background: rgba(251, 238, 240, 135); border-color: #e0757f; }
    #conflictCountBadge { color: #fff4f5; background: #a53a44; border: 1px solid #e2626c; border-radius: 12px; font-size: 11px; font-weight: 800; }
    #conflictPreview { background: rgba(227, 230, 235, 110); border-radius: 7px; min-height: 104px; max-height: 104px; }
    #conflictCaption { color: #b0404c; font-size: 11px; font-weight: 700; }
    #conflictMeta { color: #5a6575; font-size: 9px; }
    #conflictPeers { color: #3c4757; font-size: 11px; }
    #conflictPeerButton { max-height: 28px; color: #9c3440; background: rgba(247, 230, 233, 115); border: 1px solid #d99aa2; border-radius: 6px; padding: 0 9px; font-size: 11px; font-weight: 700; }
    #conflictPeerButton:hover { color: white; background: rgba(201, 74, 84, 235); border-color: #e26770; }
    #promptSurface { background: #39414c; border: 1px solid #56606c; border-radius: 12px; }
    #promptText { color: #dbe4ef; font-size: 13px; line-height: 1.5; }
    #promptIconInfo, #promptIconWarning, #promptIconError { color: white; border-radius: 15px; font-size: 16px; font-weight: 800; }
    #promptIconInfo { background: #2d65d6; border: 1px solid #5b8ced; }
    #promptIconWarning { background: #a66d24; border: 1px solid #e5a34a; }
    #promptIconError { background: #a93f4c; border: 1px solid #ed7681; }
    #promptInput { min-height: 34px; background: #3f4854; color: #e3eaf4; border: 1px solid #56606c; border-radius: 7px; padding: 0 10px; }
    #promptInput:focus { background: #47505d; border-color: #2d65d6; }
    #appToast { background: #39414c; border: 1px solid #56606c; border-radius: 10px; }
    #toastIcon { color: white; background: #2f9e63; border-radius: 11px; font-size: 13px; font-weight: 800; }
    #toastText { color: #dbe4ef; font-size: 13px; line-height: 1.5; }
    #promptPrimaryButton, #promptSecondaryButton { min-height: 32px; border-radius: 6px; padding: 0 15px; font-weight: 700; }
    #promptPrimaryButton { background: #2d65d6; color: white; border: 1px solid #3d78e7; }
    #promptPrimaryButton:hover { background: #3c78ee; border-color: #6297f3; }
    #promptSecondaryButton { background: transparent; color: #e3eaf4; border: 1px solid #aab1ba; }
    #promptSecondaryButton:hover { background: #2d65d6; border-color: #2d65d6; color: #ffffff; }
    QToolTip { background: #2e3640; color: #e3eaf4; border: 1px solid #4a545f; border-radius: 5px; padding: 6px 8px; font-size: 11px; }
    QMenu { background: #39414c; color: #e3eaf4; border: 1px solid #56606c; border-radius: 8px; padding: 5px; }
    QMenu::item { min-height: 29px; border-radius: 5px; padding: 0 26px 0 11px; margin: 1px 0; }
    QMenu::item:selected { background: #2d65d6; color: white; }
    QMenu::item:disabled { color: #8a94a0; background: transparent; }
    QMenu::separator { height: 1px; background: #4a545f; margin: 5px 8px; }
    QMenu::right-arrow { width: 8px; height: 8px; }
    QMessageBox, QInputDialog, QFileDialog { background: #39414c; color: #e3eaf4; }
    QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel { color: #c2cddb; }
    QMessageBox QLabel#qt_msgbox_label { min-width: 280px; line-height: 1.45; }
    QMessageBox QPushButton, QInputDialog QPushButton, QFileDialog QPushButton { min-height: 30px; background: #2d65d6; color: white; border: 1px solid #3d78e7; border-radius: 6px; padding: 0 14px; font-weight: 700; }
    QMessageBox QPushButton:hover, QInputDialog QPushButton:hover, QFileDialog QPushButton:hover { background: #3c78ee; border-color: #6297f3; }
    QMessageBox QPushButton:pressed, QInputDialog QPushButton:pressed, QFileDialog QPushButton:pressed { background: #2455b9; }
    QMessageBox QPushButton[text="取消"], QMessageBox QPushButton[text="否"], QInputDialog QPushButton[text="取消"], QFileDialog QPushButton[text="取消"] { background: transparent; border-color: #aab1ba; color: #e3eaf4; }
    QMessageBox QPushButton[text="取消"]:hover, QMessageBox QPushButton[text="否"]:hover, QInputDialog QPushButton[text="取消"]:hover, QFileDialog QPushButton[text="取消"]:hover { background: #2d65d6; border-color: #2d65d6; color: #ffffff; }
    QInputDialog QLineEdit, QFileDialog QLineEdit { min-height: 30px; background: #3f4854; color: #e3eaf4; border: 1px solid #56606c; border-radius: 6px; padding: 0 9px; }
    QInputDialog QLineEdit:focus, QFileDialog QLineEdit:focus { border-color: #2d65d6; background: #47505d; }
    QFileDialog QTreeView, QFileDialog QListView, QFileDialog QSidebar { background: #363e48; color: #c8d3e0; border: 1px solid #4a545f; outline: 0; }
    QFileDialog QTreeView::item, QFileDialog QListView::item, QFileDialog QSidebar::item { min-height: 27px; padding: 2px 7px; }
    QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected, QFileDialog QSidebar::item:selected { background: #2d65d6; color: white; }
    QFileDialog QComboBox { min-height: 28px; }
    QStatusBar { background: #363e48; color: #a9b6c6; border-top: 1px solid #4a545f; }
"""}

