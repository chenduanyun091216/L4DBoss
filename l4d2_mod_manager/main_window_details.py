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
from .dependencies import dependency_label
from .collection_sync import delete_collection_folder, restore_collection_files, sync_collection_files
from .models import Mod, ModCollection
from .steam_client import SteamClient
from .storage import AppStorage
from .vpk_scanner import is_conflict_relevant_path, scan_mod_directory
from .theme import *
from .components import *


def show_card_context_menu(self, mod_id: str, global_pos) -> None:
    menu = QMenu(self)
    mod = self.mods.get(mod_id)
    if mod is None:
        return
    pinned_ids = set(self.settings.get("addonlist_pinned_mod_ids", []))
    if mod_id in pinned_ids:
        pin_action = menu.addAction("取消置顶")
        pin_action.setToolTip("取消该卡片的置顶，并恢复它在 addonlist.txt 中的普通顺序")
        pin_action.triggered.connect(lambda: QTimer.singleShot(0, lambda: self.unpin_mod_from_addonlist(mod_id)))
    else:
        pin_action = menu.addAction("置顶")
        pin_action.setToolTip("将该卡片置顶；多个置顶 Mod 会依次排在 addonlist.txt 最前方")
        pin_action.triggered.connect(lambda: QTimer.singleShot(0, lambda: self.pin_mod_to_addonlist(mod_id)))
    menu.addSeparator()
    details_action = menu.addAction("查看详细信息")

    def open_details() -> None:
        # _show_content_widget snapshots the currently visible page.  When a
        # detail view is opened from the conflict report that snapshot is the
        # report itself, not the regular card list, so remember the intended
        # destination explicitly for the back button.
        self._return_to_conflicts = (
            self._content_mode == "detail" and hasattr(self, "_conflict_report_context")
        )
        self.show_mod_details(mod)

    details_action.triggered.connect(open_details)
    source_action = menu.addAction("查看源文件")
    source_action.setToolTip("打开该 Mod 所在文件夹")
    source_action.triggered.connect(lambda: self.open_mod_source(mod))
    steam_action = menu.addAction("同步Steam信息")
    steam_action.setEnabled(bool(mod.workshop_id) and not self.steam_sync_in_progress)
    steam_action.triggered.connect(lambda: self.sync_single_mod_steam(mod_id))
    dep_action = menu.addAction("管理依赖…")
    dep_action.setToolTip("设置该 Mod 依赖的其他 Mod；启用时会提示一并启用")
    dep_action.triggered.connect(lambda: self.manage_dependencies(mod_id))
    delete_action = menu.addAction("删除 Mod")
    delete_action.setToolTip("删除该 Mod 文件及其关联预览图片")
    delete_action.triggered.connect(lambda: self.delete_mod(mod_id))
    add_menu = menu.addMenu("加入组合")
    new_action = add_menu.addAction("新建组合…")
    new_action.setToolTip("创建一个新组合，并把当前 Mod 加入其中")
    new_action.triggered.connect(lambda: self.create_collection_with_mod(mod_id))
    add_menu.addSeparator()
    existing = set(self.collection_names_for(mod_id))
    if not self.collections:
        action = add_menu.addAction("暂无组合，请先保存当前组合")
        action.setEnabled(False)
    for collection in self.collections:
        if collection.name in existing:
            existing_action = QWidgetAction(add_menu)
            existing_label = QLabel(
                f'<span style="color:{theme_color("menu_text")};">{escape(collection.name)}</span>'
                ' <span style="color:#ff6f7d; font-weight:700;">· 已存在</span>'
            )
            existing_label.setFixedHeight(ui(35))
            existing_label.setContentsMargins(ui(11), 0, ui(16), 0)
            existing_action.setDefaultWidget(existing_label)
            add_menu.addAction(existing_action)
        else:
            action = add_menu.addAction(collection.name)
            action.triggered.connect(lambda _=False, name=collection.name: self.add_mod_to_collection(mod_id, name))
    menu.exec_(global_pos)


def create_collection_with_mod(self, mod_id: str) -> None:
    """Prompt for a new collection name and add the given Mod to it."""
    name, ok = QInputDialog.getText(self, "新建组合", "组合名称：")
    if not ok or not name.strip():
        return
    name = name.strip()
    if any(collection.name == name for collection in self.collections):
        QMessageBox.warning(self, "无法创建", f"组合「{name}」已存在，请换一个名称。")
        return
    collection = ModCollection(name=name, mod_ids=[mod_id])
    self.collections.append(collection)
    self.storage.save_collections(self.collections)
    self.sync_collection_in_background(collection)
    self.refresh_collection_combo()
    card = self._card_widgets.get(mod_id) or self._card_cache.get(mod_id)
    if card is not None:
        card.set_collection_context(self.collection_names_for(mod_id), self._selected_collection_names)

def open_mod_source(self, mod: Mod) -> None:
    """Reveal the Mod file in Explorer, or warn if it has been removed.

    An instance method (not a staticmethod) on purpose: the missing-file
    branch needs the window as the message-box parent.
    """
    file_path = Path(mod.file_path)
    if file_path.is_file():
        subprocess.Popen(["explorer.exe", "/select,", str(file_path)])
    else:
        QMessageBox.warning(self, "文件不存在", f"找不到 Mod 文件：\n{file_path}")


def delete_mod(self, mod_id: str) -> None:
    """Remove only the selected Mod file and its recorded preview image."""
    mod = self.mods.get(mod_id)
    if mod is None:
        return
    targets = [Path(mod.file_path)]
    if mod.image_path:
        image_path = Path(mod.image_path)
        if image_path not in targets:
            targets.append(image_path)
    existing = [path for path in targets if path.is_file()]
    names = "\n".join(f"• {path.name}" for path in existing) or "• 未找到本地文件"
    message = f"确定删除以下 Mod 文件吗？\n\n{names}\n\n此操作无法恢复。"
    if QMessageBox.question(self, "删除 Mod", message) != QMessageBox.Yes:
        return
    failed: list[str] = []
    for path in existing:
        try:
            path.unlink()
            PREVIEW_CACHE.pop((str(path), ui(186), ui(76)), None)
        except OSError as exc:
            failed.append(f"{path.name}：{exc}")
    if failed:
        QMessageBox.critical(self, "删除失败", "\n".join(failed))
        return
    self.mods.pop(mod_id, None)
    for collection in self.collections:
        if mod_id in collection.mod_ids:
            collection.mod_ids.remove(mod_id)
            self.sync_collection_in_background(collection)
    self.steam_cache.pop(mod.workshop_id or "", None)
    self.storage.save_mods(self.mods)
    self.storage.save_collections(self.collections)
    self.storage.save_steam_cache(self.steam_cache)
    self._rebuild_conflict_index()
    self.refresh_collection_combo()
    self.refresh_cards()
    self.refresh_stats()


def _show_content_widget(self, title: str, subtitle: str, widget: QWidget) -> None:
    # 仅在离开正常列表时记录返回快照。冲突报告内打开详情、或重建冲突
    # 报告时若再次覆盖它，返回按钮就会把“冲突报告”误当作列表标题。
    if self._content_mode == "mods":
        self._list_snapshot = (
            self.content_title.text(), self.content_subtitle.text(), self.current_category, self.current_page,
        )
        self._list_scroll_position = self.scroll.verticalScrollBar().value()
        self._saved_card_widgets = list(self._card_widgets.items())
    while self.cards_layout.count():
        item = self.cards_layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
    self._content_mode = "detail"
    self._card_widgets = {}
    self.content_title.setText(title)
    self.content_subtitle.setText(subtitle)
    self.content_back_button.show()
    self.search_box.hide()
    self.collection_combo.hide()
    self.pagination_bar.hide()
    self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    self.cards_layout.addWidget(widget, 0, 0)


def show_mod_details(self, mod: Mod) -> None:
    host = QWidget()
    host.setObjectName("mainDetailsHost")
    host.setMinimumWidth(max(ui(480), self.scroll.viewport().width()))
    layout = QVBoxLayout(host)
    layout.setContentsMargins(ui(12), ui(6), ui(12), ui(20))
    layout.setSpacing(ui(12))
    top = QHBoxLayout()
    info = QVBoxLayout()
    name = QLabel(mod.title or mod.file_name)
    name.setObjectName("mainDetailsTitle")
    name.setWordWrap(True)
    name.setTextInteractionFlags(Qt.TextSelectableByMouse)
    info.addWidget(name)
    code = mod.workshop_id or Path(mod.file_name).stem
    dep_labels = [dependency_label(self.mods, dep) for dep in mod.dependencies]
    for label, value in (("文件", mod.file_name), ("编号", code), ("作者", mod.author or "未知"), ("订阅", mod.display_subscriptions if mod.subscriptions else "暂无"), ("评分", f"{mod.rating:.1f}" if mod.rating else "暂无"), ("来源", "Steam 创意工坊" if mod.steam_loaded and mod.workshop_id else "本地文件"), ("状态", "已启用" if mod.active else "已禁用"), ("分类", "、".join(mod.categories) if mod.categories else "未分类"), ("依赖", "、".join(dep_labels) if dep_labels else "无")):
        field = QLabel(f"{label}　{value}")
        field.setObjectName("mainDetailsField")
        field.setWordWrap(True)
        field.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.addWidget(field)
    if mod.steam_loaded and mod.workshop_id:
        steam_link = QPushButton("在Steam创意工坊中查看")
        steam_link.setObjectName("steamDetailsLink")
        steam_link.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        steam_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod.workshop_id}")))
        info.addWidget(steam_link, 0, Qt.AlignLeft)
    manage_deps = QPushButton("管理依赖…")
    manage_deps.setObjectName("steamDetailsLink")
    manage_deps.setToolTip("设置该 Mod 依赖的其他 Mod；启用时会提示一并启用")
    manage_deps.clicked.connect(lambda: self.manage_dependencies(mod.id))
    info.addWidget(manage_deps, 0, Qt.AlignLeft)
    info.addStretch(1)
    top.addLayout(info, 1)
    preview = QLabel()
    preview.setObjectName("mainDetailsPreview")
    preview.setFixedSize(ui(340), ui(220))
    preview.setAlignment(Qt.AlignCenter)
    preview.setPixmap(make_preview_pixmap(mod, ui(320), ui(200)))
    top.addWidget(preview, 0, Qt.AlignTop)
    layout.addLayout(top)
    path = QLabel(f"文件路径　{mod.file_path}")
    path.setObjectName("mainDetailsField")
    path.setWordWrap(True)
    path.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(path)
    description = QLabel(mod.description.strip() or "暂无描述")
    description.setObjectName("mainDetailsDescription")
    description.setWordWrap(True)
    description.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(description)
    layout.addStretch(1)
    self._show_content_widget("Mod 详细信息", "完整 Mod 信息；内容过多时可在此区域滚动查看", host)


def show_mod_list(self) -> None:
    if self._content_mode == "custom":
        self._content_mode = "detail"
        self.show_mod_list()
        self.scan_mods(True)
        return
    if self._content_mode != "detail":
        return
    if getattr(self, "_return_to_conflicts", False):
        self._return_to_conflicts = False
        self.show_conflicts()
        return
    snapshot = getattr(self, "_list_snapshot", None)
    saved_cards = getattr(self, "_saved_card_widgets", [])
    self._content_mode = "mods"
    if self._favorite_only_filter:
        self._set_status_selection(None)
    else:
        self._set_status_selection(self.active_label if self._active_only_filter else self.total_label)
    if snapshot:
        title, subtitle, self.current_category, self.current_page = snapshot
        self.content_title.setText(title)
        self.content_subtitle.setText(subtitle)
    self.category_tree.blockSignals(True)
    for index in range(self.category_tree.topLevelItemCount()):
        item = self.category_tree.topLevelItem(index)
        if item.data(0, Qt.UserRole) == self.current_category:
            self.category_tree.setCurrentItem(item)
            break
    self.category_tree.blockSignals(False)
    if not saved_cards:
        self.refresh_cards()
        return
    clear_layout(self.cards_layout)
    self._card_widgets = dict(saved_cards)
    columns = self.card_columns()
    card_width = self.card_width(columns)
    for column in range(columns):
        self.cards_layout.setColumnMinimumWidth(column, card_width)
    for index, (_mod_id, card) in enumerate(saved_cards):
        card.set_card_width(card_width)
        card.show()
        self.cards_layout.addWidget(card, index // columns, index % columns, Qt.AlignTop)
    self.content_back_button.hide()
    self.search_box.show()
    self.collection_combo.show()
    self._update_pagination(len(self.filtered_mods()), max(1, (len(self.filtered_mods()) + self.page_size - 1) // self.page_size))
    # Keep the already-established toolbar widths when returning to the list.
    # They are recalculated only on initial show or window-state transitions.
    scroll_position = getattr(self, "_list_scroll_position", 0)
    QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_position))


def _reset_favorite_filter_button(self) -> None:
    """取消“只看收藏”按钮的选中态（收藏过滤随状态标签切换一起重置）。"""
    button = getattr(self, "favorite_filter_button", None)
    if button is not None:
        button.setChecked(False)


def show_active_mods(self) -> None:
    self._content_mode = "mods"
    self._active_only_filter = True
    self._favorite_only_filter = False
    self._custom_title_only_filter = False
    self._reset_favorite_filter_button()
    self._reset_custom_title_filter_button()
    self._set_status_selection(self.active_label)
    self._update_mod_filter_title()
    self.current_page = 0
    self.refresh_cards()


def show_all_mods(self) -> None:
    self._content_mode = "mods"
    self._active_only_filter = False
    self._favorite_only_filter = False
    self._custom_title_only_filter = False
    self._reset_favorite_filter_button()
    self._reset_custom_title_filter_button()
    self._set_status_selection(self.total_label)
    self._update_mod_filter_title()
    self.current_page = 0
    self.refresh_cards()


def toggle_favorite_filter(self) -> None:
    """切换“只看收藏”过滤：仅显示收藏的 Mod 卡片，再点一次恢复全部。"""
    self._content_mode = "mods"
    self._favorite_only_filter = not self._favorite_only_filter
    if self._favorite_only_filter:
        self._active_only_filter = False
    self.favorite_filter_button.setChecked(self._favorite_only_filter)
    self._set_status_selection(None)
    self._update_mod_filter_title()
    self.current_page = 0
    self.refresh_cards()


def toggle_custom_title_filter(self) -> None:
    """切换“只看改名”过滤：仅显示改过名字的 Mod 卡片，再点一次恢复。"""
    self._content_mode = "mods"
    self._custom_title_only_filter = not self._custom_title_only_filter
    if self._custom_title_only_filter:
        self._active_only_filter = False
    self.custom_title_filter_button.setChecked(self._custom_title_only_filter)
    self._set_status_selection(None)
    self._update_mod_filter_title()
    self.current_page = 0
    self.refresh_cards()


def _reset_custom_title_filter_button(self) -> None:
    """取消“只看改名”按钮的选中态（改名过滤随状态标签切换一起重置）。"""
    button = getattr(self, "custom_title_filter_button", None)
    if button is not None:
        button.setChecked(False)

