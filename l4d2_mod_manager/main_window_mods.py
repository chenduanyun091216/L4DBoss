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
    # Only block actions that would re-trigger a scan or clobber the in-flight
    # result (toggling active state). Everything else (launch, save, sync
    # Steam, open details...) stays usable so the UI never feels frozen.
    self.choose_button.setEnabled(False)
    self.refresh_button.setEnabled(False)
    self.toggle_all_button.setEnabled(False)
    # Show the shared bottom progress bar (indeterminate) while scanning, so
    # the page stays interactive and a toast is shown when it finishes.
    self._progress_owner = "scan"
    self.steam_sync_widget.show()
    self.steam_sync_progress.setRange(0, 0)
    self.steam_sync_progress.setValue(0)
    label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
    if label is not None:
        label.setText("正在扫描游戏 Mod…")
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
    total = len(self.mods)
    active = sum(1 for mod in self.mods.values() if mod.active)
    self._finish_with_message(f"已扫描完成，共发现 {total} 个 Mod（其中 {active} 个已启用）。", "scan")


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
        show_toast("请先扫描 Mod 文件夹。", self)
        return
    # A full rescan creates fresh Mod objects.  Hydrate those from the
    # persisted cache before deciding which items still need a request.
    self._apply_steam_cache(self.mods)
    pending_mods = steam_sync_candidates(self.mods)
    if not pending_mods:
        show_toast("所有可识别的 Workshop Mod 都已有本地 Steam 数据，无需重新请求。", self)
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

