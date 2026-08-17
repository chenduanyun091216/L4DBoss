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


def show_conflicts(self) -> None:
    conflicted = [mod for mod in self.mods.values() if mod.active and mod.conflict_with]
    if not conflicted:
        return
    self._set_status_selection(self.conflict_button)
    host = QWidget()
    host.setObjectName("mainConflictHost")
    host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    loading = QLabel("正在生成冲突报告…")
    loading.setObjectName("emptyText")
    loading.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
    loading.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    loading_layout = QVBoxLayout(host)
    loading_layout.setContentsMargins(0, 0, 0, 0)
    loading_layout.addStretch(1)
    loading_layout.addWidget(loading, 0, Qt.AlignHCenter)
    loading_layout.addStretch(1)
    self._show_content_widget("冲突报告", "正在分析冲突关系…", host)
    # Let the loading view paint before creating report cards.
    QTimer.singleShot(80, lambda: self._build_conflict_report(host))


def _build_conflict_report(self, host: QWidget) -> None:
    conflicted = [mod for mod in self.mods.values() if mod.active and mod.conflict_with]
    if not conflicted:
        return
    report_host = QWidget()
    report_host.setObjectName("mainConflictHost")
    layout = QVBoxLayout(report_host)
    layout.setContentsMargins(ui(12), ui(6), ui(12), ui(20))
    layout.setSpacing(ui(14))
    available_width = self.scroll.viewport().width() - ui(48)
    spacing = ui(15)
    # Use the same calculated card width as the normal library view. The
    # report may wrap at a different column count because of its margins, but
    # individual cards always remain visually identical in size.
    card_width = self.card_width(self.card_columns())
    columns = max(1, (available_width + spacing) // (card_width + spacing))
    groups = ConflictDialog._conflict_groups(self.mods)
    conflict_names = {
        "rifle_ak47": "AK-47", "rifle_m16": "M16", "rifle_desert": "SCAR", "rifle_sg552": "SG552",
        "smg_uzi": "Uzi", "smg_silenced": "消音冲锋枪", "smg_mp5": "MP5",
        "shotgun_pump": "泵动霰弹枪", "shotgun_chrome": "铬合金霰弹枪", "shotgun_auto": "战术霰弹枪", "shotgun_spas": "SPAS-12",
        "pistol_p220": "P220", "pistol_dual": "双持手枪", "pistol_magnum": "马格南",
        "sniper_hunting": "猎枪", "sniper_military": "军用狙击枪", "sniper_awp": "AWP", "sniper_scout": "Scout",
        "melee": "近战武器", "melee_katana": "武士刀", "melee_fireaxe": "消防斧", "melee_chainsaw": "电锯",
    }
    self._conflict_report_groups = groups
    self._conflict_report_sections: dict[int, QFrame] = {}
    self._conflict_report_context = (host, report_host, layout, conflicted, columns, card_width, conflict_names)
    QTimer.singleShot(0, lambda: self._add_conflict_report_group(0))


def _add_conflict_report_group(self, index: int) -> None:
    if self._content_mode != "detail" or not hasattr(self, "_conflict_report_context"):
        return
    loading_host, report_host, layout, conflicted, columns, card_width, conflict_names = self._conflict_report_context
    if index >= len(self._conflict_report_groups):
        layout.addStretch(1)
        self._show_completed_conflict_report(loading_host, report_host, len(conflicted))
        return
    number = index + 1
    group = self._conflict_report_groups[index]
    
    section = self._build_conflict_group_section(group, number, columns, card_width, conflict_names)
    self._conflict_report_sections[index] = section
    layout.addWidget(section)
    QTimer.singleShot(0, lambda: self._add_conflict_report_group(index + 1))


def _build_conflict_group_section(
    self, group: list[Mod], number: int, columns: int, card_width: int, conflict_names: dict[str, str],
) -> QFrame:
    """构建单个冲突组的分区卡片（冲突报告内使用）。

    number 是显示用的组号（从 1 开始）；卡片顺序即组内 Mod 顺序，
    置顶的 Mod 位于最前，与 addonlist.txt 的写入顺序保持一致。
    """
    section = QFrame()
    section.setObjectName("mainConflictGroup")
    section_layout = QVBoxLayout(section)
    section_layout.setContentsMargins(ui(12), ui(10), ui(12), ui(12))
    section_layout.setSpacing(ui(8))
    shared_paths: set[str] = set()
    for left_index, left in enumerate(group):
        for right in group[left_index + 1:]:
            shared_paths.update(self._conflict_paths.get(left.id, set()) & self._conflict_paths.get(right.id, set()))
    common_categories = set(group[0].categories)
    for mod in group[1:]:
        common_categories.intersection_update(mod.categories)
    targets = [conflict_names[key] for key in common_categories if key in conflict_names]
    reason = f"均替换 {' / '.join(sorted(targets)[:2])}" if targets else (f"共享 {len(shared_paths)} 个资源文件" if shared_paths else "存在重叠资源文件")
    heading_row = QHBoxLayout()
    heading_row.setContentsMargins(0, 0, 0, 0)
    heading_row.setSpacing(ui(8))
    heading = QLabel(f"冲突组 {number:02d}  ·  {len(group)} 个 Mod")
    heading.setObjectName("mainConflictGroupTitle")
    heading_row.addWidget(heading)
    heading_row.addStretch(1)
    section_layout.addLayout(heading_row)
    detail = QLabel(f"冲突原因：{reason}")
    detail.setObjectName("mainConflictGroupReason")
    detail.setToolTip("\n".join(sorted(shared_paths)) if shared_paths else reason)
    detail.setWordWrap(True)
    section_layout.addWidget(detail)
    grid_host = QWidget()
    grid = QGridLayout(grid_host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(ui(15))
    grid.setVerticalSpacing(ui(15))
    grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    for card_index, mod in enumerate(group):
        card = ConflictCard(mod, card_width)
        card.disable_requested.connect(self.disable_conflict_mod)
        card.context_requested.connect(self.show_card_context_menu)
        card.custom_title_changed.connect(self.on_card_custom_title_changed)
        grid.addWidget(card, card_index // columns, card_index % columns, Qt.AlignTop)
    section_layout.addWidget(grid_host)
    return section


def _show_completed_conflict_report(self, loading_host: QWidget, report_host: QWidget, conflict_count: int) -> None:
    """Swap the already-built report in only after the loading state has painted."""
    if self._content_mode != "detail":
        return
    while self.cards_layout.count():
        item = self.cards_layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()
    self.cards_layout.addWidget(report_host, 0, 0)
    report_host.show()
    self.content_subtitle.setText(
        f"发现 {conflict_count} 个已启用 Mod 存在资源冲突；右键卡片可置顶，双击卡片禁用"
    )


def _rebuild_conflict_group_section(self, index: int) -> None:
    """置顶/取消置顶后就地重建指定冲突组的分区，保持滚动位置不跳变。"""
    if self._content_mode != "detail" or not hasattr(self, "_conflict_report_context"):
        return
    _loading_host, _report_host, layout, _conflicted, columns, card_width, conflict_names = self._conflict_report_context
    old = self._conflict_report_sections.get(index)
    if old is None:
        return
    new = self._build_conflict_group_section(self._conflict_report_groups[index], index + 1, columns, card_width, conflict_names)
    layout.replaceWidget(old, new)
    old.deleteLater()
    self._conflict_report_sections[index] = new


def _rebuild_conflict_report_after_pin(self) -> None:
    """冲突组内置顶顺序变化后，重算分组并重绘所有分区，使置顶卡片前移、
    红色图钉即时出现/消失，同时保留当前滚动位置。"""
    if self._content_mode != "detail" or not hasattr(self, "_conflict_report_context"):
        return
    self._conflict_report_groups = ConflictDialog._conflict_groups(self.mods)
    for index in list(self._conflict_report_sections.keys()):
        self._rebuild_conflict_group_section(index)


def _show_conflict_toast(self, message: str) -> None:
    """把置顶反馈显示在「冲突报告」标题右侧，而非窗口底部。"""
    target = getattr(self, "content_toast", None)
    if target is None:
        show_toast(message, self)
        return
    target.setText(message)
    target.setVisible(True)
    target.adjustSize()
    if getattr(self, "_conflict_toast_timer", None) is None:
        self._conflict_toast_timer = QTimer(self)
        self._conflict_toast_timer.setSingleShot(True)
    self._conflict_toast_timer.stop()
    self._conflict_toast_timer.timeout.connect(lambda: target.setVisible(False))
    self._conflict_toast_timer.start(3200)


def disable_conflict_mod(self, mod_id: str) -> None:
    mod = self.mods.get(mod_id)
    if mod is None or not mod.active:
        return
    mod.active = False
    affected = self._update_conflicts_for_toggle(mod_id)
    self.storage.save_mods(self.mods)
    self._refresh_card_states(affected)
    self.refresh_stats()
    # The report is a one-shot view: its cards hold the old conflict graph.
    # Rebuild it after a double-click instead of only refreshing the library
    # cards, otherwise disabled Mods remain visible in the report.
    if self._content_mode == "detail":
        if any(mod.active and mod.conflict_with for mod in self.mods.values()):
            self.show_conflicts()
        else:
            empty = QLabel("No remaining conflicts")
            empty.setObjectName("emptyText")
            empty.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            self._show_content_widget(self.content_title.text(), "No remaining conflicts", empty)

