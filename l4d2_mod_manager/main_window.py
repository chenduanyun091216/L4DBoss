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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowIcon(QIcon(str(TITLE_ICON)))
        self.setWindowTitle("L4D2 Boss · 求生之路 2 Mod 管理器")
        self.resize(ui(1250), ui(730))
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
        self._favorite_only_filter = False
        self.thread_pool = QThreadPool.globalInstance()
        self.collection_sync_pool = QThreadPool(self)
        self.collection_sync_pool.setMaxThreadCount(1)
        self.steam_sync_in_progress = False
        self._progress_owner: str | None = None
        self._progress_visible = False
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

    def show_about(self) -> None:
        AboutDialog(self).exec_()

    def eventFilter(self, source, event) -> bool:
        hints = {
            getattr(self, "choose_button", None): "选择游戏：定位 left4dead2.exe 并扫描 addons 文件夹",
            getattr(self, "refresh_button", None): "扫描 Mod：重新扫描本地 addons 文件夹",
            getattr(self, "fetch_button", None): "同步 Steam：获取创意工坊 Mod 的名称、订阅数和标签",
            getattr(self, "theme_button", None): "切换主题：点击选择界面配色",
            getattr(self, "minimize_button", None): "最小化窗口",
            getattr(self, "maximize_button", None): "最大化 / 还原窗口",
            getattr(self, "close_button", None): "关闭程序",
            getattr(self, "favorite_filter_button", None): "只看收藏：仅显示收藏的 Mod",
            getattr(self, "toggle_all_button", None): "全部启动：启动当前所有 Mod",
            getattr(self, "save_button", None): "保存：将当前激活 Mod 存入组合",
            getattr(self, "save_as_button", None): "另存为：将当前激活 Mod 另存为",
            getattr(self, "launch_button", None): "启动游戏：启动游戏 L4D",
        }
        if source is getattr(self, "search_input", None) and getattr(self, "search_box", None) is not None:
            # 搜索框容器跟随输入框焦点切换“聚焦”样式。
            if event.type() in (QEvent.FocusIn, QEvent.FocusOut):
                self.search_box.setProperty("focused", event.type() == QEvent.FocusIn)
                self.search_box.style().unpolish(self.search_box)
                self.search_box.style().polish(self.search_box)
                self.search_box.update()
        if source in hints:
            is_footer = source in getattr(self, "_footer_action_buttons", ())
            if event.type() == QEvent.Enter:
                if source is getattr(self, "save_button", None):
                    # 保存按钮的说明需要动态带上当前选中的组合名称。
                    text = self._save_hint_text()
                else:
                    text = hints[source]
                if source is getattr(self, "favorite_filter_button", None):
                    # 收藏按钮提示显示在搜索框左侧的空白区域，样式与其他按钮一致。
                    if not getattr(self, "_progress_visible", False):
                        self._show_hover_hint(text, self.search_box)
                elif is_footer:
                    self._show_footer_hint(text)
                else:
                    self._show_header_hint(text)
            elif event.type() == QEvent.Leave:
                if is_footer:
                    self._clear_footer_hint()
                else:
                    self._clear_header_hint()
            elif event.type() == QEvent.MouseButtonPress:
                # 点击按钮即收起悬停说明，避免与随后出现的进度条叠在一起。
                if is_footer:
                    self._clear_footer_hint()
                else:
                    self._clear_header_hint()
        return super().eventFilter(source, event)

    def _save_hint_text(self) -> str:
        """保存按钮的悬停说明：拼接当前选中的组合名称。"""
        names = sorted(self._selected_collection_names)
        if not names:
            target = "未选择组合"
        elif len(names) == 1:
            target = names[0]
        else:
            target = f"已选 {len(names)} 个组合"
        return f"保存：将当前激活 Mod 存入组合「{target}」"

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


from .main_window_build import (
    _build_ui,
    _build_header,
    _header_button,
    _window_control_button,
    toggle_maximized,
    restore_default_window,
    _show_header_hint,
    _clear_header_hint,
    _show_footer_hint,
    _clear_footer_hint,
    _show_hover_hint,
    _clear_hover_hint,
    _update_theme_button,
    _theme_icon,
    _open_theme_menu,
    _set_theme,
    _launch_icon,
    _build_content_bar,
    _build_footer_legacy,
    _make_mod_count_button,
    _build_footer,
    _apply_style,
    _set_progress_visible,
    _show_title_menu,
)

MainWindow._build_ui = _build_ui
MainWindow._build_header = _build_header
MainWindow._header_button = _header_button
MainWindow._window_control_button = _window_control_button
MainWindow.toggle_maximized = toggle_maximized
MainWindow.restore_default_window = restore_default_window
MainWindow._show_header_hint = _show_header_hint
MainWindow._clear_header_hint = _clear_header_hint
MainWindow._show_footer_hint = _show_footer_hint
MainWindow._clear_footer_hint = _clear_footer_hint
MainWindow._show_hover_hint = _show_hover_hint
MainWindow._clear_hover_hint = _clear_hover_hint
MainWindow._update_theme_button = _update_theme_button
MainWindow._theme_icon = _theme_icon
MainWindow._open_theme_menu = _open_theme_menu
MainWindow._set_theme = _set_theme
MainWindow._launch_icon = _launch_icon
MainWindow._build_content_bar = _build_content_bar
MainWindow._build_footer_legacy = _build_footer_legacy
MainWindow._make_mod_count_button = _make_mod_count_button
MainWindow._build_footer = _build_footer
MainWindow._apply_style = _apply_style
MainWindow._set_progress_visible = _set_progress_visible
MainWindow._show_title_menu = _show_title_menu

from .main_window_cards import (
    _rebuild_conflict_index,
    _refresh_conflicts_from_index,
    _update_conflicts_for_toggle,
    _refresh_card_states,
    refresh_tree,
    _make_tree_item,
    _tree_item_color,
    _refresh_tree_foregrounds,
    on_category_mode_switch_changed,
    refresh_cards,
    _populate_cards_batch,
    _update_pagination,
    change_page,
    on_search_changed,
    _change_card_size,
    _release_card_size_alignment,
    _columns_for_card_size,
    _effective_card_size,
    card_columns,
    card_width,
    _card_viewport_width,
    _show_cards_loading,
    _hide_cards_loading,
    _advance_cards_loading_spinner,
    _schedule_cards_refresh,
    _refresh_cards_after_layout,
    _reflow_cards,
    _sync_content_right_edges,
    _schedule_window_state_alignment,
    _align_window_state,
    _schedule_content_alignment,
    _set_status_selection,
    _update_mod_filter_title,
    _simple_categories_for,
    _time_sort_key,
)

MainWindow._rebuild_conflict_index = _rebuild_conflict_index
MainWindow._refresh_conflicts_from_index = _refresh_conflicts_from_index
MainWindow._update_conflicts_for_toggle = _update_conflicts_for_toggle
MainWindow._refresh_card_states = _refresh_card_states
MainWindow.refresh_tree = refresh_tree
MainWindow._make_tree_item = _make_tree_item
MainWindow._tree_item_color = _tree_item_color
MainWindow._refresh_tree_foregrounds = _refresh_tree_foregrounds
MainWindow.on_category_mode_switch_changed = on_category_mode_switch_changed
MainWindow.refresh_cards = refresh_cards
MainWindow._populate_cards_batch = _populate_cards_batch
MainWindow._update_pagination = _update_pagination
MainWindow.change_page = change_page
MainWindow.on_search_changed = on_search_changed
MainWindow._change_card_size = _change_card_size
MainWindow._release_card_size_alignment = _release_card_size_alignment
MainWindow._columns_for_card_size = _columns_for_card_size
MainWindow._effective_card_size = _effective_card_size
MainWindow.card_columns = card_columns
MainWindow.card_width = card_width
MainWindow._card_viewport_width = _card_viewport_width
MainWindow._show_cards_loading = _show_cards_loading
MainWindow._hide_cards_loading = _hide_cards_loading
MainWindow._advance_cards_loading_spinner = _advance_cards_loading_spinner
MainWindow._schedule_cards_refresh = _schedule_cards_refresh
MainWindow._refresh_cards_after_layout = _refresh_cards_after_layout
MainWindow._reflow_cards = _reflow_cards
MainWindow._sync_content_right_edges = _sync_content_right_edges
MainWindow._schedule_window_state_alignment = _schedule_window_state_alignment
MainWindow._align_window_state = _align_window_state
MainWindow._schedule_content_alignment = _schedule_content_alignment
MainWindow._set_status_selection = _set_status_selection
MainWindow._update_mod_filter_title = _update_mod_filter_title
MainWindow._simple_categories_for = _simple_categories_for
MainWindow._time_sort_key = _time_sort_key

from .main_window_mods import (
    choose_directory,
    find_steam_game_executable,
    addon_directories,
    configured_addon_directories,
    scan_mods,
    reset_mods,
    on_scan_finished,
    launch_game,
    steam_is_installed,
    fetch_steam_info,
    set_all_mods_active,
    toggle_all_mods,
    toggle_mod,
    toggle_favorite,
    open_mods_directory,
)

MainWindow.choose_directory = choose_directory
MainWindow.find_steam_game_executable = find_steam_game_executable
MainWindow.addon_directories = addon_directories
MainWindow.configured_addon_directories = configured_addon_directories
MainWindow.scan_mods = scan_mods
MainWindow.reset_mods = reset_mods
MainWindow.on_scan_finished = on_scan_finished
MainWindow.launch_game = launch_game
MainWindow.steam_is_installed = steam_is_installed
MainWindow.fetch_steam_info = fetch_steam_info
MainWindow.set_all_mods_active = set_all_mods_active
MainWindow.toggle_all_mods = toggle_all_mods
MainWindow.toggle_mod = toggle_mod
MainWindow.toggle_favorite = toggle_favorite
MainWindow.open_mods_directory = open_mods_directory

from .main_window_collections import (
    collection_names_for,
    add_mod_to_collection,
    sync_collection_in_background,
    _on_collection_sync_failed,
    filtered_mods,
    refresh_collection_combo,
    _update_collection_combo_label,
    on_collection_selection_changed,
    restore_selected_collections_in_background,
    on_collection_restore_finished,
    on_collection_restore_failed,
    _set_collection_restore_status,
    _apply_pending_collection_selection,
    save_selected_collection_names,
    apply_selected_collections,
    delete_collection,
    rename_collection,
    on_category_selected,
    _run_category_refresh,
    save_collection,
    save_collection_as_new,
    confirm_save_collection,
    write_addonlist,
)

MainWindow.collection_names_for = collection_names_for
MainWindow.add_mod_to_collection = add_mod_to_collection
MainWindow.sync_collection_in_background = sync_collection_in_background
MainWindow._on_collection_sync_failed = _on_collection_sync_failed
MainWindow.filtered_mods = filtered_mods
MainWindow.refresh_collection_combo = refresh_collection_combo
MainWindow._update_collection_combo_label = _update_collection_combo_label
MainWindow.on_collection_selection_changed = on_collection_selection_changed
MainWindow.restore_selected_collections_in_background = restore_selected_collections_in_background
MainWindow.on_collection_restore_finished = on_collection_restore_finished
MainWindow.on_collection_restore_failed = on_collection_restore_failed
MainWindow._set_collection_restore_status = _set_collection_restore_status
MainWindow._apply_pending_collection_selection = _apply_pending_collection_selection
MainWindow.save_selected_collection_names = save_selected_collection_names
MainWindow.apply_selected_collections = apply_selected_collections
MainWindow.delete_collection = delete_collection
MainWindow.rename_collection = rename_collection
MainWindow.on_category_selected = on_category_selected
MainWindow._run_category_refresh = _run_category_refresh
MainWindow.save_collection = save_collection
MainWindow.save_collection_as_new = save_collection_as_new
MainWindow.confirm_save_collection = confirm_save_collection
MainWindow.write_addonlist = write_addonlist

from .main_window_steam import (
    sync_single_mod_steam,
    on_steam_finished,
    on_steam_failed,
    cancel_steam_sync,
    on_steam_cancelled,
    _reset_steam_sync_controls,
    _set_steam_sync_status,
    _finish_with_message,
    _hide_status_message,
)

MainWindow.sync_single_mod_steam = sync_single_mod_steam
MainWindow.on_steam_finished = on_steam_finished
MainWindow.on_steam_failed = on_steam_failed
MainWindow.cancel_steam_sync = cancel_steam_sync
MainWindow.on_steam_cancelled = on_steam_cancelled
MainWindow._reset_steam_sync_controls = _reset_steam_sync_controls
MainWindow._set_steam_sync_status = _set_steam_sync_status
MainWindow._finish_with_message = _finish_with_message
MainWindow._hide_status_message = _hide_status_message

from .main_window_conflicts import (
    show_conflicts,
    _build_conflict_report,
    _add_conflict_report_group,
    _show_completed_conflict_report,
    disable_conflict_mod,
)

MainWindow.show_conflicts = show_conflicts
MainWindow._build_conflict_report = _build_conflict_report
MainWindow._add_conflict_report_group = _add_conflict_report_group
MainWindow._show_completed_conflict_report = _show_completed_conflict_report
MainWindow.disable_conflict_mod = disable_conflict_mod

from .main_window_details import (
    show_card_context_menu,
    open_mod_source,
    delete_mod,
    _show_content_widget,
    show_mod_details,
    show_mod_list,
    show_active_mods,
    show_all_mods,
    toggle_favorite_filter,
    _reset_favorite_filter_button,
)

MainWindow.show_card_context_menu = show_card_context_menu
MainWindow.open_mod_source = open_mod_source
MainWindow.delete_mod = delete_mod
MainWindow._show_content_widget = _show_content_widget
MainWindow.show_mod_details = show_mod_details
MainWindow.show_mod_list = show_mod_list
MainWindow.show_active_mods = show_active_mods
MainWindow.show_all_mods = show_all_mods
MainWindow.toggle_favorite_filter = toggle_favorite_filter
MainWindow._reset_favorite_filter_button = _reset_favorite_filter_button

from .main_window_events import (
    on_worker_failed,
    set_busy,
    closeEvent,
    resizeEvent,
    moveEvent,
    changeEvent,
    showEvent,
    _apply_native_window_corner,
)

MainWindow.on_worker_failed = on_worker_failed
MainWindow.set_busy = set_busy
MainWindow.closeEvent = closeEvent
MainWindow.resizeEvent = resizeEvent
MainWindow.moveEvent = moveEvent
MainWindow.changeEvent = changeEvent
MainWindow.showEvent = showEvent
MainWindow._apply_native_window_corner = _apply_native_window_corner

