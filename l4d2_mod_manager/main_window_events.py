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
from .theme import *
from .components import *


def on_worker_failed(self, message: str) -> None:
    self.set_busy(False)
    if getattr(self, "_progress_owner", None) == "scan":
        self._progress_owner = None
        self._set_progress_visible(False)
    QMessageBox.critical(self, "操作失败", message)


def set_busy(self, busy: bool, message: str = "") -> None:
    for button in (self.choose_button, self.refresh_button, self.fetch_button, self.toggle_all_button, self.save_button, self.save_as_button, self.launch_button):
        button.setEnabled(not busy)
    self.fetch_button.setEnabled(not busy and not self.steam_sync_in_progress)


def closeEvent(self, event) -> None:
    self.save_selected_collection_names()
    self.storage.save_mods(self.mods)
    self.storage.save_collections(self.collections)
    super(type(self), self).closeEvent(event)


def resizeEvent(self, event) -> None:
    super(type(self), self).resizeEvent(event)
    if hasattr(self, "_size_grip"):
        self._size_grip.move(
            self.centralWidget().width() - self._size_grip.width(),
            self.centralWidget().height() - self._size_grip.height(),
        )
    if hasattr(self, "_cards_loading_overlay") and self._cards_loading_overlay.isVisible():
        self._cards_loading_overlay.setGeometry(self.scroll.viewport().rect())
        self._cards_loading_overlay.raise_()
    if hasattr(self, "cards_layout"):
        self._schedule_cards_refresh()
    # 窗口尺寸变化时，让置顶提示跟随锚点按钮重新定位。
    if (
        getattr(self, "hover_overlay", None) is not None
        and self.hover_overlay.isVisible()
        and getattr(self, "_hover_anchor", None) is not None
    ):
        self._show_hover_hint(self._hover_text, self._hover_anchor)


def moveEvent(self, event) -> None:
    super(type(self), self).moveEvent(event)
    # 拖动窗口时，让置顶提示跟随锚点按钮一起移动。
    if (
        getattr(self, "hover_overlay", None) is not None
        and self.hover_overlay.isVisible()
        and getattr(self, "_hover_anchor", None) is not None
    ):
        self._show_hover_hint(self._hover_text, self._hover_anchor)


def changeEvent(self, event) -> None:
    super(type(self), self).changeEvent(event)
    if event.type() == QEvent.WindowStateChange and self.isMinimized():
        overlay = getattr(self, "hover_overlay", None)
        if overlay is not None:
            overlay.hide()


def showEvent(self, event) -> None:
    super(type(self), self).showEvent(event)
    self._apply_native_window_corner()
    # The first show/maximize pass can change the viewport after the
    # normal resize event. Reflow once more after Qt settles the splitter
    # and scrollbar geometry so cards and controls share one right edge.
    self._schedule_cards_refresh()
    self._schedule_window_state_alignment()


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

