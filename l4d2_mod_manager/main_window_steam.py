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
    self._set_steam_stop_mode(True)
    self.steam_sync_progress.setRange(0, 1)
    self.steam_sync_progress.setValue(0)
    self._set_steam_sync_status(0, 1)
    self._set_progress_visible(True)
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
    self._simple_category_cache.clear()
    self._card_cache.clear()
    self.storage.save_mods(self.mods)
    self._save_steam_cache()
    self.refresh_cards()
    self.refresh_tree()
    self.refresh_stats()
    self._finish_with_message("Steam 信息已获取完成，页面已刷新。", "steam")


def on_steam_failed(self, message: str) -> None:
    self.steam_sync_in_progress = False
    self._reset_steam_sync_controls()
    if self._progress_owner == "steam":
        self._progress_owner = None
        self._set_progress_visible(False)
    QMessageBox.critical(self, "Steam 同步失败", message)


def cancel_steam_sync(self) -> None:
    if not self.steam_sync_in_progress:
        return
    self._steam_cancel_event.set()
    self._set_steam_stop_mode(True)
    self.fetch_button.setEnabled(False)
    label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
    if label is not None:
        label.set_full_text("正在取消 Steam 同步…")


def on_steam_cancelled(self) -> None:
    self.steam_sync_in_progress = False
    self._reset_steam_sync_controls()
    self._finish_with_message("已停止后续 Mod 的 Steam 数据同步。", "steam")


def _reset_steam_sync_controls(self) -> None:
    self._set_steam_stop_mode(False)
    self.fetch_button.setEnabled(True)


def _set_steam_stop_mode(self, stopping: bool) -> None:
    """Keep every Steam-sync entry point on the same visible button state."""
    button = self.fetch_button
    button.setProperty("stopMode", stopping)
    button.setText("停止" if stopping else "同步Steam")
    icon_name = "stop_sync.png" if stopping else "sync_steam.png"
    button.setIcon(QIcon(str(ICON_DIR / icon_name)))
    button.style().unpolish(button)
    button.style().polish(button)
    button.update()


def _set_steam_sync_status(self, completed: int, total: int) -> None:
    if self._progress_owner != "steam":
        return
    self.steam_sync_progress.setRange(0, max(total, 1))
    self.steam_sync_progress.setValue(completed)
    percent = round(completed * 100 / total) if total else 100
    label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
    if label is not None:
        label.set_full_text(f"正在同步 Steam 数据… {completed}/{total}（{percent}%）")


def _finish_with_message(self, text: str, owner: str) -> None:
    """Replace the bottom progress bar with a completion message in its place.

    The progress bar is hidden and the label shows the result, keeping the UI
    non-blocking. After a short delay the whole bar is dismissed (unless a new
    operation has reused it).
    """
    if self._progress_owner != owner:
        return
    self._progress_owner = None
    self.steam_sync_progress.hide()
    label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
    if label is not None:
        label.set_full_text(text)
    self._set_progress_visible(True)
    QTimer.singleShot(3200, self._hide_status_message)


def _hide_status_message(self) -> None:
    if self._progress_owner is not None:
        # A new scan/sync reused the bar; leave it to that operation.
        return
    self.steam_sync_progress.show()
    self._set_progress_visible(False)

