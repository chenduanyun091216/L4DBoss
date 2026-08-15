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


def show_card_context_menu(self, mod_id: str, global_pos) -> None:
    menu = QMenu(self)
    mod = self.mods.get(mod_id)
    if mod is None:
        return
    details_action = menu.addAction("查看详细信息")
    details_action.triggered.connect(lambda: self.show_mod_details(mod))
    source_action = menu.addAction("查看源文件")
    source_action.setToolTip("打开该 Mod 所在文件夹")
    source_action.triggered.connect(lambda: self.open_mod_source(mod))
    steam_action = menu.addAction("同步当前 Mod Steam 信息")
    steam_action.setEnabled(bool(mod.workshop_id) and not self.steam_sync_in_progress)
    steam_action.triggered.connect(lambda: self.sync_single_mod_steam(mod_id))
    delete_action = menu.addAction("删除 Mod")
    delete_action.setToolTip("删除该 Mod 文件及其关联预览图片")
    delete_action.triggered.connect(lambda: self.delete_mod(mod_id))
    add_menu = menu.addMenu("加入已保存的组合")
    existing = set(self.collection_names_for(mod_id))
    if not self.collections:
        action = add_menu.addAction("暂无组合，请先保存当前组合")
        action.setEnabled(False)
    for collection in self.collections:
        if collection.name in existing:
            existing_action = QWidgetAction(add_menu)
            existing_label = QLabel(
                f'<span style="color:#dfe9f8;">{escape(collection.name)}</span>'
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

@staticmethod

@staticmethod
def open_mod_source(mod: Mod) -> None:
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
    self.search_input.hide()
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
    for label, value in (("文件", mod.file_name), ("编号", code), ("作者", mod.author or "未知"), ("订阅", mod.display_subscriptions if mod.subscriptions else "暂无"), ("评分", f"{mod.rating:.1f}" if mod.rating else "暂无"), ("来源", "Steam 创意工坊" if mod.steam_loaded and mod.workshop_id else "本地文件"), ("状态", "已启用" if mod.active else "已禁用"), ("分类", "、".join(mod.categories) if mod.categories else "未分类")):
        field = QLabel(f"{label}　{value}")
        field.setObjectName("mainDetailsField")
        field.setWordWrap(True)
        field.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.addWidget(field)
    if mod.steam_loaded and mod.workshop_id:
        steam_link = QPushButton("在 Steam 创意工坊中查看")
        steam_link.setObjectName("steamDetailsLink")
        steam_link.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        steam_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod.workshop_id}")))
        info.addWidget(steam_link, 0, Qt.AlignLeft)
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
    if self._content_mode != "detail":
        return
    snapshot = getattr(self, "_list_snapshot", None)
    saved_cards = getattr(self, "_saved_card_widgets", [])
    self._content_mode = "mods"
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
    self.search_input.show()
    self.collection_combo.show()
    self._update_pagination(len(self.filtered_mods()), max(1, (len(self.filtered_mods()) + self.page_size - 1) // self.page_size))
    self._sync_content_right_edges()
    scroll_position = getattr(self, "_list_scroll_position", 0)
    QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_position))


def show_active_mods(self) -> None:
    self._content_mode = "mods"
    self._active_only_filter = True
    self._set_status_selection(self.active_label)
    self._update_mod_filter_title()
    self.current_page = 0
    self.refresh_cards()


def show_all_mods(self) -> None:
    self._content_mode = "mods"
    self._active_only_filter = False
    self._set_status_selection(self.total_label)
    self._update_mod_filter_title()
    self.current_page = 0
    self.refresh_cards()

