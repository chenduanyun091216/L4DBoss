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
from .custom_mod import CUSTOM_MOD_FILENAME
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
            card = self._card_widgets.get(mod_id) or self._card_cache.get(mod_id)
            if card is not None:
                card.set_collection_context(self.collection_names_for(mod_id), self._selected_collection_names)
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
    if self._favorite_only_filter:
        mods = [mod for mod in mods if mod.favorite]
    if self._custom_title_only_filter:
        mods = [mod for mod in mods if mod.custom_title]
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
        mods = [mod for mod in mods if query in " ".join([mod.custom_title, mod.title, mod.author, mod.file_name, mod.workshop_id or ""]).lower()]
    for mod in mods:
        if mod.id not in self._mod_sort_cache:
            try:
                self._mod_sort_cache[mod.id] = Path(mod.file_path).stat().st_mtime_ns
            except OSError:
                self._mod_sort_cache[mod.id] = 0
    # First show user-pinned cards in their addonlist priority order, then keep
    # the normal library sort for every other card.
    ordered = sorted(
        mods,
        key=lambda mod: (
            self._mod_sort_cache.get(mod.id, 0),
            mod.title.casefold(),
        ),
        reverse=True,
    )
    by_id = {mod.id: mod for mod in ordered}
    pinned_ids = [mod_id for mod_id in self.settings.get("addonlist_pinned_mod_ids", []) if mod_id in by_id]
    pinned_set = set(pinned_ids)
    return [by_id[mod_id] for mod_id in pinned_ids] + [mod for mod in ordered if mod.id not in pinned_set]


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
        self._set_progress_visible(True)
    worker = Worker(restore_collection_files, addon_dirs[0], sorted(self._selected_collection_names), progress_callback=None)
    worker.kwargs["progress_callback"] = worker.signals.progress.emit
    worker.signals.progress.connect(self._set_collection_restore_status)
    worker.signals.finished.connect(self.on_collection_restore_finished)
    worker.signals.failed.connect(self.on_collection_restore_failed)
    self.collection_sync_pool.start(worker)


def on_collection_restore_finished(self, restored: int) -> None:
    if self._progress_owner == "restore":
        self._progress_owner = None
        self._set_progress_visible(False)
    # Apply the union of every checked collection even when no file had
    # to be restored. If files were restored, on_scan_finished applies it
    # once more after the new Mods are discovered.
    self.apply_selected_collections()
    if restored:
        self.scan_mods(False)


def on_collection_restore_failed(self, message: str) -> None:
    if self._progress_owner == "restore":
        self._progress_owner = None
        self._set_progress_visible(False)
    self._on_collection_sync_failed("当前组合", message)


def _set_collection_restore_status(self, completed: int, total: int) -> None:
    if self._progress_owner != "restore":
        return
    self.steam_sync_progress.setRange(0, max(total, 1))
    self.steam_sync_progress.setValue(completed)
    percent = round(completed * 100 / total) if total else 100
    label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
    if label is not None:
        label.set_full_text(f"正在恢复组合文件… {completed}/{total}（{percent}%）")


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
    # Keep the game's existing order intact.  Re-sorting every entry makes the
    # file hard to audit and changes the precedence of unrelated add-ons.
    known_entries: dict[str, tuple[str, bool]] = {}
    mod_entry_keys: dict[str, str] = {}
    known_filenames: set[str] = set()
    for mod in self.mods.values():
        file_path = Path(mod.file_path)
        try:
            relative_path = file_path.resolve().relative_to(addon_root)
        except ValueError:
            continue
        relative = str(relative_path).replace("/", "\\")
        known_entries[relative.casefold()] = (relative, mod.active)
        mod_entry_keys[mod.id] = relative.casefold()
        known_filenames.add(Path(relative).name.casefold())

    custom_filename = self.settings.get("custom_mod_filename", CUSTOM_MOD_FILENAME)
    custom_relative = custom_filename.replace("/", "\\")
    entries: list[tuple[str, bool]] = []
    seen: set[str] = set()
    if addonlist_path.exists():
        try:
            existing = addonlist_path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            existing = ""
        for relative, enabled in re.findall(r'"([^"\r\n]+\.vpk)"\s+"([01])"', existing, flags=re.I):
            key = relative.casefold()
            if key in seen:
                continue
            seen.add(key)
            current = known_entries.get(key)
            # A Mod can exist both in addons/ and addons/workshop/. The scanner
            # deliberately selects one physical file; never preserve the other
            # stale path, otherwise its old "1" state silently re-enables it.
            if current is None and Path(relative.replace("\\", "/")).name.casefold() in known_filenames:
                continue
            entries.append(current if current is not None else (relative, enabled == "1"))

    # New files not present in the old list are appended without disturbing it.
    for key, entry in known_entries.items():
        if key not in seen:
            entries.append(entry)
            seen.add(key)

    # Do not force the custom Mod to the top. Its precedence is managed by the
    # existing conflict pin action, just like every other Mod.
    if (addon_root / custom_filename).exists() and custom_relative.casefold() not in seen:
        # A generated custom VPK may exist before the next scan adds it to
        # self.mods. Presence on disk is never permission to enable it.
        custom_mod = next(
            (mod for mod in self.mods.values()
             if Path(mod.file_path).name.casefold() == custom_filename.casefold()),
            None,
        )
        entries.append((custom_filename, bool(custom_mod and custom_mod.active)))

    # User-pinned cards always occupy the first entries. Newer pins come first;
    # every non-pinned entry keeps its original relative order.
    pinned_ids = [mod_id for mod_id in self.settings.get("addonlist_pinned_mod_ids", []) if mod_id in self.mods]
    pinned_keys = [mod_entry_keys[mod_id] for mod_id in pinned_ids if mod_id in mod_entry_keys]
    pinned_set = set(pinned_keys)
    entry_by_key = {path.casefold(): (path, active) for path, active in entries}
    # Pinning changes only placement. Always recover the activation bit from
    # the corresponding Mod rather than from an older addonlist entry.
    ordered_entries = [
        (entry_by_key[mod_entry_keys[mod_id]][0], bool(self.mods[mod_id].active))
        for mod_id in pinned_ids
        if mod_id in mod_entry_keys and mod_entry_keys[mod_id] in entry_by_key
    ]
    ordered_entries.extend(entry for entry in entries if entry[0].casefold() not in pinned_set)
    entries = ordered_entries

    lines = ['"AddonList"\n', "{\n"]
    for relative_path, active in entries:
        lines.append(f'\t"{relative_path}"\t\t"{"1" if active else "0"}"\n')
    lines.append("}\n")
    try:
        addonlist_path.parent.mkdir(parents=True, exist_ok=True)
        addonlist_path.write_text("".join(lines), encoding="utf-8")
    except OSError as exc:
        QMessageBox.critical(self, "写入失败", f"无法写入 addonlist.txt：{exc}")
        return False
    return True


def pin_mod_to_addonlist(self, mod_id: str) -> None:
    """Pin a card and move all pinned cards to the front of addonlist.txt."""
    mod = self.mods.get(mod_id)
    if mod is None:
        return
    pinned_ids = [item for item in self.settings.get("addonlist_pinned_mod_ids", []) if item in self.mods and item != mod_id]
    pinned_ids.insert(0, mod_id)
    self.settings["addonlist_pinned_mod_ids"] = pinned_ids
    self.storage.save_settings(self.settings)
    if not self.write_addonlist():
        return
    self.refresh_addonlist_pinned_state()
    self._after_pin_change(mod_id, pinned=True)


def unpin_mod_from_addonlist(self, mod_id: str) -> None:
    pinned_ids = list(self.settings.get("addonlist_pinned_mod_ids", []))
    if mod_id not in pinned_ids:
        return
    self.settings["addonlist_pinned_mod_ids"] = [item for item in pinned_ids if item != mod_id]
    self.storage.save_settings(self.settings)
    if not self.write_addonlist():
        return
    self.refresh_addonlist_pinned_state()
    self._after_pin_change(mod_id, pinned=False)


def _after_pin_change(self, mod_id: str, *, pinned: bool) -> None:
    """Refresh whichever view currently shows the cards and surface feedback."""
    mod = self.mods.get(mod_id)
    title = mod.title if mod else "该 Mod"
    if self._content_mode == "mods":
        self.current_page = 0
        self.refresh_cards()
        self.show_pin_status(f"{'已置顶' if pinned else '已取消置顶'}「{title}」")
    elif self._content_mode == "detail" and hasattr(self, "_conflict_report_context"):
        # Rebuilding individual sections while their child cards are still
        # owned by the report can make Qt delete/reparent widgets during the
        # context-menu event.  Recreate the report on the next event-loop turn
        # so the menu and its native popup have fully unwound first.
        QTimer.singleShot(0, self.show_conflicts)
        self._show_conflict_toast(f"{'已置顶' if pinned else '已取消置顶'}「{title}」")
        self.show_pin_status(f"{'已置顶' if pinned else '已取消置顶'}「{title}」")
    else:
        self.show_pin_status(f"{'已置顶' if pinned else '已取消置顶'}「{title}」")


def show_pin_status(self, message: str) -> None:
    """Show a brief pin/unpin confirmation to the left of the 选择游戏 button."""
    self.pin_status_label.setText(message)
    self.pin_status_widget.show()
    self._position_header_status_widgets()
    self._pin_status_timer.start(1800)


def refresh_addonlist_pinned_state(self) -> None:
    """Reflect the persistent multi-card pin selection in card overlays."""
    pinned_ids = set(self.settings.get("addonlist_pinned_mod_ids", []))
    for mod in self.mods.values():
        mod.addonlist_pinned = mod.id in pinned_ids

