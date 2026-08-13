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

