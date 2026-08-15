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


def _tree_item_color(self, depth: int, is_leaf: bool) -> QColor:
    """Normal-state (unselected) text color for a category tree item.

    The titanium theme keeps its sidebar transparent over the (now stronger)
    wallpaper, so its tree text uses light tones for contrast; the light
    theme keeps dark tones on its near-white background.
    """
    if self._theme == "titanium":
        if is_leaf:
            return QColor("#9fc3ff" if depth == 0 else "#d2dbe8")
        return QColor("#e6edf6")
    if is_leaf:
        if depth == 0:
            return QColor(theme_color("link"))
        if self._theme == "light":
            return QColor("#212a36")
        return QColor(theme_color("tree_default"))
    if self._theme == "light":
        return QColor("#181f2a")
    return QColor(theme_color("link") if depth == 0 else theme_color("tree_default"))


def _refresh_tree_foregrounds(self) -> None:
    """Re-apply tree item text colors after selection changes.

    Items own an explicit foreground (setForeground), which overrides the
    stylesheet color even on the selected state, so when a row is selected
    (earth-yellow background) switch its text to a dark tone to stay
    readable, and restore the hierarchy color once deselected.

    Note: QTreeWidgetItem is not hashable in PyQt5, so the selected items
    are compared by list membership (sip `==` compares the C++ pointers)
    instead of building a set.
    """
    selected = self.category_tree.selectedItems()
    stack = [self.category_tree.topLevelItem(i) for i in range(self.category_tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item is None:
            continue
        if item in selected:
            item.setForeground(0, QColor("#241c08"))
        else:
            item.setForeground(0, self._tree_item_color(item.data(0, Qt.UserRole + 1) or 0, item.childCount() == 0))
        for index in range(item.childCount()):
            stack.append(item.child(index))


def _make_tree_item(self, entry, depth: int) -> QTreeWidgetItem:
    if isinstance(entry, tuple):
        item = QTreeWidgetItem([entry[1]])
        item.setData(0, Qt.UserRole, entry[0])
        item.setData(0, Qt.UserRole + 1, depth)
        child_font = QFont(self.category_tree.font())
        child_font.setPointSize(max(9, child_font.pointSize() - (1 if depth > 1 else 0)))
        item.setFont(0, child_font)
        item.setForeground(0, self._tree_item_color(depth, True))
        return item
    item = QTreeWidgetItem([entry["label"]])
    item.setData(0, Qt.UserRole, entry["id"])
    item.setData(0, Qt.UserRole + 1, depth)
    root_font = QFont(self.category_tree.font())
    root_font.setBold(depth == 0 or (self.category_mode == "simple" and depth == 1))
    root_font.setPointSize(max(10, root_font.pointSize() + (1 if depth == 0 else 0)))
    item.setFont(0, root_font)
    item.setForeground(0, self._tree_item_color(depth, False))
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
            card.set_card_width(card_width)
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
    self.pagination_bar.setVisible(True)
    self.pagination_spacer.setVisible(True)
    self.previous_page_button.setVisible(visible)
    self.page_label.setVisible(visible)
    self.next_page_button.setVisible(visible)
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


def _change_card_size(self, delta: int) -> None:
    self._card_size_adjustment_token = getattr(self, "_card_size_adjustment_token", 0) + 1
    adjustment_token = self._card_size_adjustment_token
    self._suppress_content_alignment = True
    old_columns = self.card_columns() if hasattr(self, "cards_layout") else 1
    candidate = max(ui(160), min(ui(300), self._card_size + delta))
    # Cards fill their row, so a small target-size change is not visible
    # until it changes the column count. Skip invisible intermediate
    # values and land on the next column threshold in one click.
    if delta > 0:
        while candidate < ui(300) and self._columns_for_card_size(candidate) == old_columns:
            candidate = min(ui(300), candidate + ui(10))
    elif delta < 0:
        while candidate > ui(160) and self._columns_for_card_size(candidate) == old_columns:
            candidate = max(ui(160), candidate - ui(10))
    self._card_size = candidate
    self.card_size_decrease.setEnabled(self._card_size > ui(160))
    self.card_size_increase.setEnabled(self._card_size < ui(300))
    size_text = f"当前卡片宽度约 {self._card_size}px；卡片保持等比例缩放"
    self.card_size_decrease.setToolTip(f"缩小卡片（{size_text}）")
    self.card_size_increase.setToolTip(f"放大卡片（{size_text}）")
    if hasattr(self, "cards_layout") and self._content_mode == "mods" and self._cards_ready_for_reflow:
        self._reflow_cards()
        self._sync_content_right_edges(force=True)
    # Keep queued scrollbar/layout callbacks from moving the controls
    # immediately after a card-size click. A newer click extends this
    # quiet period through the token check.
    QTimer.singleShot(100, lambda: self._release_card_size_alignment(adjustment_token))


def _release_card_size_alignment(self, token: int) -> None:
    if token == getattr(self, "_card_size_adjustment_token", -1):
        self._suppress_content_alignment = False


def _columns_for_card_size(self, preferred: int) -> int:
    width = self._card_viewport_width()
    spacing = self.cards_layout.horizontalSpacing() if hasattr(self, "cards_layout") else ui(11)
    return max(1, (width + spacing) // (preferred + spacing))


def card_columns(self) -> int:
    width = self._card_viewport_width()
    spacing = self.cards_layout.horizontalSpacing() if hasattr(self, "cards_layout") else ui(11)
    # Cards expand to use any extra room, so the final column never leaves a
    # large dead area. The default window shows about five cards per row and a
    # maximized window about eight, governed by _card_size below.
    preferred = getattr(self, "_card_size", ui(214))
    return max(1, (width + spacing) // (preferred + spacing))


def card_width(self, columns: int) -> int:
    width = self._card_viewport_width()
    spacing = self.cards_layout.horizontalSpacing() if hasattr(self, "cards_layout") else ui(11)
    # The size buttons select the preferred scale and therefore the number
    # of columns. The cards themselves always expand to fill the complete
    # available row, so resizing never leaves a dead area on the right.
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
    self._cards_loading_overlay.setGeometry(self.scroll.viewport().rect())
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
    # Coalesce bursts of native resize events to roughly one visual update
    # per frame. This keeps dragging responsive without building a queue of
    # expensive card-layout and style recalculations.
    QTimer.singleShot(16, self._refresh_cards_after_layout)


def _refresh_cards_after_layout(self) -> None:
    self._card_refresh_pending = False
    self._reflow_cards()


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
    previous_columns = getattr(self, "_displayed_card_columns", None)
    # During ordinary window dragging only the card width changes. Keep the
    # existing grid items in place; removing and re-adding every widget on
    # every resize is the main source of visible flashing.
    if previous_columns == columns:
        for card in self._card_widgets.values():
            card.set_card_width(card_width)
        self.cards_layout.invalidate()
        return

    self.cards_host.setUpdatesEnabled(False)
    try:
        for index, card in enumerate(self._card_widgets.values()):
            self.cards_layout.removeWidget(card)
            card.set_card_width(card_width)
            self.cards_layout.addWidget(card, index // columns, index % columns, Qt.AlignTop)
        self._displayed_card_columns = columns
    finally:
        self.cards_host.setUpdatesEnabled(True)
        self.cards_host.update()


def _sync_content_right_edges(self, force: bool = False) -> None:
    """Make controls end exactly where the visible card viewport ends."""
    if getattr(self, "_suppress_content_alignment", False) and not force:
        return
    self._content_alignment_pending = False
    if not hasattr(self, "scroll"):
        return
    scrollbar = self.scroll.verticalScrollBar()
    # Reserve the scrollbar width even before it is visible.  This keeps
    # the toolbar, pagination and footer actions from jumping right when
    # the first page becomes tall enough to show the bar.
    inset = scrollbar.width() or scrollbar.sizeHint().width()
    # Align to the calculated edge of a complete card row.  A viewport
    # width is not always divisible by the selected column count; using
    # its right edge leaves the toolbar a few pixels past the last card.
    # This is calculated from the same inputs as card_width(), so it does
    # not depend on stale widget geometry during maximize/restore.
    if self.scroll.viewport().width() > 0 and self.content_bar.width() > 0:
        bar_left = self.content_bar.mapToGlobal(QPoint(0, 0)).x()
        columns = self.card_columns()
        card_width = self.card_width(columns)
        grid_width = columns * card_width + (columns - 1) * self.cards_layout.horizontalSpacing()
        grid_right = self.scroll.viewport().mapToGlobal(QPoint(grid_width, 0)).x()
        inset = max(0, self.content_bar.width() - (grid_right - bar_left))
    self.content_bar.layout().setContentsMargins(0, 0, inset, 0)
    self.pagination_bar.layout().setContentsMargins(0, 0, inset, 0)
    # Match both toolbar controls to one card column.  As the combo box
    # remains the final item in this right-aligned layout, its right edge
    # shares the right edge of the last card as well.
    if hasattr(self, "search_input") and hasattr(self, "collection_combo"):
        control_width = self.card_width(self.card_columns())
        grid_gap = self.cards_layout.horizontalSpacing()
        self._filter_controls.setSpacing(grid_gap)
        self.collection_combo.setFixedWidth(control_width)
        # The search box spans exactly two card columns (including the gap
        # between them) and the combo occupies the last card column, so the
        # whole filter row stays right-aligned to the card grid and grows
        # together with the cards.
        if hasattr(self, "choose_button"):
            content_left = self.content_bar.mapToGlobal(QPoint(0, 0)).x()
            bar_gap = self.content_bar.layout().spacing()
            filter_gap = self._filter_controls.spacing()
            search_width = 2 * control_width + grid_gap
            search_left = grid_right - control_width - filter_gap - search_width
            # Keep the left-side title readable: when the cards are enlarged
            # so far that the two-column search would cover the title, shrink
            # the search box instead of letting it overlap the text.
            title_width = search_left - content_left - bar_gap
            title_min = ui(150)
            if title_width < title_min:
                search_width = max(ui(1), search_width - (title_min - title_width))
                title_width = title_min
            self.content_title_host.setFixedWidth(max(ui(1), title_width))
            self.search_input.setFixedWidth(max(ui(1), search_width))
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
        # 底部提示进度条（扫描/同步/恢复共用）与按钮保持同一高度。
        if hasattr(self, "steam_sync_widget"):
            self.steam_sync_widget.setFixedHeight(max(action_height, ui(1)))
    if hasattr(self, "action_host"):
        # The footer starts after the fixed sidebar.  Mirror the content
        # area's outer gutter and scrollbar inset for a shared right edge.
        # The footer itself extends to the app edge, so include that outer
        # gutter as well when aligning its final action to the viewport.
        self.action_host.layout().setContentsMargins(ui(16), 0, ui(8) + inset, 0)


def _schedule_window_state_alignment(self) -> None:
    """Reflow and align after Qt finishes a window-state transition."""
    self._window_alignment_token = getattr(self, "_window_alignment_token", 0) + 1
    token = self._window_alignment_token
    for delay in (0, 60, 180):
        QTimer.singleShot(delay, lambda t=token: self._align_window_state(t))


def _align_window_state(self, token: int) -> None:
    if token != getattr(self, "_window_alignment_token", -1):
        return
    if self._content_mode == "mods" and self._cards_ready_for_reflow:
        self._reflow_cards()
    self._sync_content_right_edges()


def _schedule_content_alignment(self, *_args) -> None:
    if (
        getattr(self, "_suppress_content_alignment", False)
        or self._content_alignment_pending
        or not hasattr(self, "scroll")
    ):
        return
    self._content_alignment_pending = True
    QTimer.singleShot(0, self._sync_content_right_edges)


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

@staticmethod
def _time_sort_key(mod: Mod) -> tuple[int, str]:
    """Sort by the VPK's local modification time, with a stable name tie-breaker."""
    try:
        modified_at = Path(mod.file_path).stat().st_mtime_ns
    except OSError:
        modified_at = 0
    return modified_at, mod.title.casefold()

