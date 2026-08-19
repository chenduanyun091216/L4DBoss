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

from .categories import CATEGORIES, SIMPLE_CATEGORIES, effective_tags, infer_categories, simple_categories
from .dependencies import dependency_label, dependency_status, resolve_dependents
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


def open_mods_directory(self) -> None:
    """Open the game's addons folder (where Mod VPK files are installed)."""
    addon_dirs = self.configured_addon_directories()
    existing = [directory for directory in addon_dirs if directory.exists()]
    if not existing:
        QMessageBox.information(
            self, "无法打开 Mod 目录",
            "尚未选择游戏目录，或未找到游戏的 addons 文件夹。\n请先点击「选择游戏」定位 left4dead2.exe。",
        )
        return
    try:
        os.startfile(str(existing[0]))
    except OSError as exc:
        QMessageBox.critical(self, "打开失败", f"无法打开 Mod 目录：{exc}")


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
    # Show the shared header progress bar (indeterminate) while scanning, so
    # the page stays interactive and a toast is shown when it finishes.
    self._progress_owner = "scan"
    self._set_progress_visible(True)
    self.steam_sync_progress.setRange(0, 0)
    self.steam_sync_progress.setValue(0)
    label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
    if label is not None:
        label.set_full_text("正在扫描游戏 Mod…")
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
    # Re-scan rebuilds every Mod object, so user-edited fields (custom_title,
    # favorite, dependencies, etc.) stored on the previous objects would be lost.
    # Merge them back by resolved file path before replacing self.mods.
    old_by_path = {
        str(Path(mod.file_path).resolve()): mod
        for mod in self.mods.values()
    }
    self.mods = mods
    self._simple_category_cache.clear()
    self._mod_sort_cache.clear()
    for mod in self.mods.values():
        old = old_by_path.get(str(Path(mod.file_path).resolve()))
        if old is None:
            continue
        # 分类推断会重建 Mod 对象；以下字段属于用户选择，必须完整回填。
        # 漏掉 manual_tags / excluded_auto_tags 会让编辑过的标签在扫描后消失。
        for field in (
            "custom_title", "favorite", "favorite_at", "dependencies", "conflict_pin",
            "manual_tags", "excluded_auto_tags",
        ):
            setattr(mod, field, getattr(old, field))
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
    self.fetch_button.setEnabled(True)
    total = len(pending_mods)
    self.steam_sync_progress.setRange(0, total)
    self.steam_sync_progress.setValue(0)
    self._set_steam_sync_status(0, total)
    self._set_progress_visible(True)
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
    if mod_id not in self.mods:
        return
    if self.mods[mod_id].active:
        self._deactivate_mod_with_dependency_check(mod_id)
    else:
        self._activate_mod_with_dependency_check(mod_id)


def _campaign_series_key(mod: Mod) -> str | None:
    """Return a stable key for numbered map/campaign parts, if applicable."""
    if not {"campaigns", "maps"}.intersection(effective_tags(mod)):
        return None
    title = (mod.custom_title or mod.title or Path(mod.file_name).stem).strip()
    # Only recognise explicit final part markers.  This intentionally avoids
    # grouping arbitrary titles that merely contain a number in the middle.
    key = re.sub(
        r"(?ix)\s*(?:[\[\(]\s*)?(?:part|pt\.?|episode|ep\.?|chapter)\s*"
        r"(?:\d+|[ivxlcdm]+)\s*(?:[\]\)])?\s*$",
        "",
        title,
    )
    key = re.sub(r"[\s_\-–—]+", " ", key).strip().casefold()
    return key if key and key != title.casefold() else None


def _campaign_series_peer_ids(self, mod_id: str) -> list[str]:
    mod = self.mods.get(mod_id)
    if mod is None:
        return []
    key = _campaign_series_key(mod)
    if key is None:
        return []
    return [
        peer.id for peer in self.mods.values()
        if peer.id != mod_id and _campaign_series_key(peer) == key
    ]


def _activate_mod_with_dependency_check(self, mod_id: str) -> None:
    """Turn a Mod on, offering related campaign parts and dependencies."""
    mod = self.mods[mod_id]
    targets = {mod_id}
    series_inactive = [
        peer_id for peer_id in self._campaign_series_peer_ids(mod_id)
        if not self.mods[peer_id].active
    ]
    if series_inactive:
        titles = "\n".join(
            f"• {self.mods[peer_id].title or self.mods[peer_id].file_name}"
            for peer_id in sorted(series_inactive, key=lambda item: (self.mods[item].title or self.mods[item].file_name).casefold())
        )
        box = QMessageBox(self)
        box.setWindowTitle("启用同系列地图")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"「{mod.title or mod.file_name}」属于一个分段地图/战役系列：\n\n{titles}\n\n"
            "是否一并启用同系列的其他部分？"
        )
        all_button = box.addButton("全部启用", QMessageBox.YesRole)
        current_button = box.addButton("仅启用当前", QMessageBox.NoRole)
        cancel_button = box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is cancel_button:
            return
        if box.clickedButton() is all_button:
            targets.update(series_inactive)

    inactive_ids: set[str] = set()
    missing_ids: set[str] = set()
    for target_id in targets:
        inactive, missing = dependency_status(self.mods, target_id)
        inactive_ids.update(inactive)
        missing_ids.update(missing)
    if not inactive_ids and not missing_ids:
        self._set_mods_active(targets, True)
        return
    lines = []
    for dep_id in sorted(inactive_ids, key=lambda dep: (self.mods[dep].title or self.mods[dep].file_name).casefold()):
        lines.append(f"• {dependency_label(self.mods, dep_id)}（当前已禁用）")
    for dep_id in sorted(missing_ids):
        lines.append(f"• {dependency_label(self.mods, dep_id)}")
    box = QMessageBox(self)
    box.setWindowTitle("启用依赖 Mod")
    box.setIcon(QMessageBox.Question)
    box.setText(
        f"待启用 Mod 依赖以下项目：\n\n"
        + "\n".join(lines)
        + "\n\n是否一并启用已安装的依赖 Mod？"
    )
    yes_button = box.addButton("一并启用", QMessageBox.YesRole)
    no_button = box.addButton("仅启用当前", QMessageBox.NoRole)
    cancel_button = box.addButton("取消", QMessageBox.RejectRole)
    box.exec_()
    clicked = box.clickedButton()
    if clicked is cancel_button:
        return
    if clicked is yes_button:
        targets.update(inactive_ids)
    self._set_mods_active(targets, True)


def _deactivate_mod_with_dependency_check(self, mod_id: str) -> None:
    """Turn a Mod off, warning when other active Mods depend on it."""
    dependents = resolve_dependents(self.mods, mod_id)
    if dependents:
        lines = "\n".join(
            f"• {self.mods[dep].title or self.mods[dep].file_name}"
            for dep in sorted(dependents, key=lambda dep: (self.mods[dep].title or self.mods[dep].file_name).casefold())
        )
        box = QMessageBox(self)
        box.setWindowTitle("停用被依赖的 Mod")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            f"以下已启用的 Mod 依赖「{self.mods[mod_id].title or self.mods[mod_id].file_name}」：\n\n{lines}\n\n"
            "停用后它们可能无法正常工作，仍要停用吗？"
        )
        yes_button = box.addButton("仍要停用", QMessageBox.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is not yes_button:
            return
    self._set_mods_active({mod_id}, False)


def _set_mods_active(self, mod_ids: set[str], active: bool) -> None:
    """Apply an active-state change to several Mods and refresh once."""
    affected: set[str] = set()
    for mod_id in mod_ids:
        mod = self.mods.get(mod_id)
        if mod is None or mod.active == active:
            continue
        mod.active = active
        affected.update(self._update_conflicts_for_toggle(mod_id))
    if not affected:
        return
    self.storage.save_mods(self.mods)
    self._refresh_card_states(affected)
    self.refresh_stats()


def manage_dependencies(self, mod_id: str) -> None:
    """Open the dependency editor for one Mod and persist the result."""
    mod = self.mods.get(mod_id)
    if mod is None:
        return
    dialog = DependencyDialog(mod, self.mods, self)
    if dialog.exec_() != QDialog.Accepted:
        return
    mod.dependencies = dialog.dependency_ids()
    self.storage.save_mods(self.mods)
    card = self._card_widgets.get(mod_id)
    if card is not None:
        card.refresh_state()
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
    if self._favorite_only_filter and not mod.favorite:
        # “只看收藏”视图下取消收藏：卡片应立即从列表中移除。
        self.refresh_cards()

