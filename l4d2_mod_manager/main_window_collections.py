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
from .collection_sync import (
    delete_collection_folder,
    rename_collection_folder,
    restore_collection_files,
    sanitize_collection_name,
    sync_collection_files,
)
from .models import Mod, ModCollection
from .steam_client import SteamClient
from .storage import AppStorage
from .vpk_scanner import is_conflict_relevant_path, scan_mod_directory
from .theme import *
from .components import *


def collection_names_for(self, mod_id: str) -> list[str]:
    return [item.name for item in self.collections if mod_id in item.mod_ids]


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


def rename_collection(self, name: str) -> None:
    collection = next((item for item in self.collections if item.name == name), None)
    if collection is None:
        QMessageBox.information(self, "无法修改", "找不到指定组合。")
        return
    new_name, ok = QInputDialog.getText(self, "修改组合名称", "新的组合名称：", text=collection.name)
    if not ok:
        return
    new_name = new_name.strip()
    if not new_name:
        QMessageBox.warning(self, "无法修改", "组合名称不能为空。")
        return
    if new_name == collection.name:
        return
    if any(item.name == new_name for item in self.collections):
        QMessageBox.warning(self, "无法修改", f"组合「{new_name}」已存在，请换一个名称。")
        return
    if sanitize_collection_name(new_name) is None:
        QMessageBox.warning(self, "无法修改", f"名称「{new_name}」不能作为组合名称。")
        return
    addon_dirs = self.configured_addon_directories()
    if addon_dirs:
        ok_rename, message = rename_collection_folder(addon_dirs[0], collection.name, new_name)
        if not ok_rename:
            QMessageBox.warning(self, "无法修改", message)
            return
    old_name = collection.name
    collection.name = new_name
    was_selected = old_name in self._selected_collection_names
    self._selected_collection_names.discard(old_name)
    if was_selected:
        self._selected_collection_names.add(new_name)
    self.save_selected_collection_names()
    self.storage.save_collections(self.collections)
    self.refresh_collection_combo()
    self.sync_collection_in_background(collection)
    show_toast(f"已重命名组合「{old_name}」为「{new_name}」。", self)


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

