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

from PyQt5.QtCore import QEvent, QMimeData, QObject, QPoint, QRect, QRunnable, QSize, QTimer, QUrl, Qt, QThreadPool, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QDrag, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAction, QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QAbstractItemView, QProgressBar, QPushButton, QScrollArea, QSizeGrip, QSizePolicy, QSplitter, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QWidgetAction,
    QWidgetItem,
)

from .categories import (
    CATEGORIES,
    SIMPLE_CATEGORIES,
    collect_all_category_ids,
    effective_tags,
    infer_categories,
    iter_category_tree,
    simple_categories,
)
from .dependencies import extract_workshop_ids
from .collection_sync import delete_collection_folder, restore_collection_files, sync_collection_files
from .models import Mod, ModCollection
from .steam_client import SteamClient
from .storage import AppStorage
from .vpk_scanner import is_conflict_relevant_path, scan_mod_directory
from .theme import *
from .components import *

class WorkerSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    cancelled = pyqtSignal()


class TaskCancelled(Exception):
    """Signals a cooperative stop for a background task."""


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self.fn(*self.args, **self.kwargs))
        except TaskCancelled:
            self.signals.cancelled.emit()
        except Exception as exc:
            self.signals.failed.emit(str(exc))
class TwoLineElidedLabel(QLabel):
    """A two-line label that always ends overflowing text with an ellipsis."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setWordWrap(False)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._update_text()

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._update_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_text()

    def _update_text(self) -> None:
        if self.width() <= 8:
            return
        metrics = self.fontMetrics()
        available = self.width()
        source = self._full_text.replace("\n", " ").strip()
        first, remainder = self._fit_line(source, available)
        if not remainder:
            super().setText(first)
            return
        second, overflow = self._fit_line(remainder, available)
        if overflow:
            second = metrics.elidedText(second + overflow, Qt.ElideRight, available)
        super().setText(f"{first}\n{second}")

    def _fit_line(self, text: str, width: int) -> tuple[str, str]:
        if not text:
            return "", ""
        metrics = self.fontMetrics()
        end = 0
        for index, _char in enumerate(text, start=1):
            if metrics.horizontalAdvance(text[:index]) > width:
                break
            end = index
        if end == len(text):
            return text, ""
        if end == 0:
            return "", text
        return text[:end].rstrip(), text[end:].lstrip()


class SingleLineElidedLabel(QLabel):
    """A single-line label used by the header status bar.

    It grows with its text so the surrounding box stretches to fit the
    full message (up to ``max_width``) instead of wrapping; it only
    elides with an ellipsis when the window is too narrow.  The text is
    kept vertically centered inside the box.
    """

    def __init__(self, text: str = "", parent=None, max_width: int = 360):
        super().__init__(parent)
        self._full_text = text
        self._max_width = max(0, int(max_width))
        self.setWordWrap(False)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._update_text()

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._update_text()
        # 全文变化后通知父布局重算尺寸（setText 只在可见文字变化时触发）。
        self.updateGeometry()

    def setText(self, text: str) -> None:
        # 兼容直接调用 QLabel.setText 的调用方：同步维护内部全文，
        # 否则布局触发的 resizeEvent 会用空的 _full_text 把文字清空。
        self._full_text = text
        self._update_text()

    def clear(self) -> None:
        # QLabel.clear() 内部不走 setText，需要在此同步清理全文，
        # 避免旧文字在下次布局时被重新显示出来。
        self._full_text = ""
        super().clear()

    def set_max_width(self, width: int) -> None:
        self._max_width = max(0, int(width))
        self._update_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_text()

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        text = self._full_text.replace("\n", " ").strip()
        ideal = metrics.horizontalAdvance(text) + ui(4) if text else ui(4)
        # max_width <= 0 表示不设上限，宽度完全由文字内容决定。
        width = ideal if self._max_width <= 0 else min(ideal, self._max_width)
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().sizeHint().height())

    def _update_text(self) -> None:
        text = self._full_text.replace("\n", " ").strip()
        available = self.width()
        metrics = self.fontMetrics()
        # 宽度未知（未布局/隐藏）时先显示完整文字，布局后再按真实宽度省略。
        if available > 8 and metrics.horizontalAdvance(text) > available:
            super().setText(metrics.elidedText(text, Qt.ElideRight, available))
        else:
            super().setText(text)


class HintOverlay(QWidget):
    """A top-level, always-on-top hint chip.

    Lives in its own frameless tool window, so nothing inside the app can
    cover it: it floats above the main window next to the hovered button,
    takes no layout space, never steals focus or mouse input, and closes
    together with the main window.
    """

    def __init__(self, main_window: QWidget):
        super().__init__(
            main_window,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setObjectName("hoverOverlay")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # 可见的圆角框：作为子控件绘制背景/边框（顶层窗口不吃样式表背景），
        # 内边距（左右 14 / 上下 9）即框内文字与边框的间距。
        self.box = QFrame(self)
        self.box.setObjectName("hoverHintBox")
        box_layout = QHBoxLayout(self.box)
        box_layout.setContentsMargins(ui(14), ui(3), ui(14), ui(3))
        # 宽度完全由提示文字内容决定（max_width=0 表示不设上限，
        # 文字多宽提示框就多宽，不做省略号截断）。
        self.label = SingleLineElidedLabel("", max_width=0)
        self.label.setObjectName("hoverHintChip")
        box_layout.addWidget(self.label)
        outer.addWidget(self.box)
        self.hide()

    def set_hint_text(self, text: str) -> None:
        self.label.set_full_text(text)
        # 宽度跟随文字内容：不依赖布局缓存的 totalSizeHint（隐藏状态下布局
        # 不会随文字同步失效），直接按“文字 + 框内边距 + 左右 1px 边框”
        # 计算整个提示框的尺寸。
        self.label.adjustSize()
        margins = self.box.layout().contentsMargins()
        width = self.label.width() + margins.left() + margins.right() + 2
        height = self.label.height() + margins.top() + margins.bottom() + 2
        self.resize(width, height)

    def show_near(self, anchor_global: QPoint, anchor_height: int) -> None:
        # Match the hovered button exactly; the compact vertical padding keeps
        # the single-line hint readable at the same height.
        self.setFixedHeight(anchor_height)
        self.box.setFixedHeight(anchor_height)
        x = anchor_global.x() - self.width() - ui(8)
        x = max(4, x)
        y = anchor_global.y() + (anchor_height - self.height()) // 2
        self.move(x, y)
        self.show()
        self.raise_()


def mod_type_tags(mod: Mod) -> list[tuple[str, str]]:
    """Return at most three useful type tags, preferring concrete targets."""
    categories = set(effective_tags(mod))
    tags: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(category: str, label: str, color: str) -> None:
        if category in categories and label not in seen and len(tags) < 3:
            tags.append((label, color))
            seen.add(label)

    gun_labels = {
        "rifle_ak47": "AK-47", "rifle_m16": "M16", "rifle_desert": "SCAR", "rifle_sg552": "SG552",
        "shotgun_pump": "泵动霰弹枪", "shotgun_chrome": "铬合金霰弹枪", "shotgun_auto": "战术霰弹枪",
        "shotgun_spas": "SPAS-12", "smg_uzi": "Uzi", "smg_silenced": "消音冲锋枪", "smg_mp5": "MP5",
        "sniper_hunting": "猎枪", "sniper_military": "军用狙击枪", "sniper_awp": "AWP", "sniper_scout": "Scout",
        "pistol_p220": "P220", "pistol_dual": "双持手枪", "pistol_magnum": "马格南",
        "grenade_launcher": "榴弹发射器", "m60": "M60",
    }
    for category, label in gun_labels.items():
        add(category, label, "#365f9f")
    if not tags:
        for category, label in (("pistol", "手枪"), ("smg", "冲锋枪"), ("rifle", "步枪"), ("shotgun", "霰弹枪"), ("sniper", "狙击枪"), ("weapons", "枪械")):
            add(category, label, "#365f9f")

    melee_labels = {
        "melee_fireaxe": "消防斧", "melee_katana": "武士刀", "melee_machete": "砍刀",
        "melee_frying_pan": "平底锅", "melee_bat": "棒球棍", "melee_cricket_bat": "板球棒",
        "melee_crowbar": "撬棍", "melee_electric_guitar": "电吉他", "melee_golfclub": "高尔夫球杆",
        "melee_pitchfork": "干草叉", "melee_shovel": "铲子", "melee_tonfa": "警棍",
        "melee_chainsaw": "电锯", "melee_knife": "小刀", "melee": "近战武器",
    }
    for category, label in melee_labels.items():
        add(category, label, "#8b5a9f")

    survivor_labels = {
        "bill": "角色 · 比尔", "francis": "角色 · 弗朗西斯", "louis": "角色 · 路易斯", "zoey": "角色 · 佐伊",
        "coach": "角色 · 教练", "ellis": "角色 · 艾利斯", "nick": "角色 · 尼克", "rochelle": "角色 · 罗谢尔",
        "survivors": "角色",
    }
    for category, label in survivor_labels.items():
        add(category, label, "#3b8b78")

    infected_labels = {
        "common_infected": "感染者 · 普通", "boomer": "感染者 · Boomer", "charger": "感染者 · Charger",
        "hunter": "感染者 · Hunter", "jockey": "感染者 · Jockey", "smoker": "感染者 · Smoker",
        "spitter": "感染者 · Spitter", "tank": "感染者 · Tank", "witch": "感染者 · Witch",
        "infected": "感染者",
    }
    for category, label in infected_labels.items():
        add(category, label, "#a15a50")

    if not tags:
        fallback_labels = {
            "campaigns": "地图", "items": "物品", "throwable": "投掷物", "sounds": "声音", "music": "音乐",
            "scripts": "脚本", "ui": "界面", "models": "模型", "textures": "贴图", "miscellaneous": "杂项",
        }
        for category, label in fallback_labels.items():
            add(category, label, "#526073")
    return tags
class CustomTitleMixin:
    """卡片自定义名称共用逻辑：右上角修改笔 + 左侧“改”角标。

    ModCard 与 ConflictCard 共用。使用方需在 __init__ 中先创建 preview 与
    title_label（TwoLineElidedLabel），再调用 _setup_custom_title(preview, title)；
    并自行声明 custom_title_changed = pyqtSignal(str, str) 与
    mod_info_changed = pyqtSignal(str, str, list, list) 信号。
    """
    mod_info_changed = pyqtSignal(str, str, list, list)  # (mod_id, custom_title, manual_tags, excluded_auto_tags)

    def _setup_custom_title(self, preview: QWidget, title_label: QLabel) -> None:
        self._custom_title = self.mod.custom_title or ""
        self._show_custom = True
        self.title_label = title_label
        # 右上角紧凑编辑按钮：不增加尺寸，提供更清晰的悬停/按下层级。
        self._rename_button = QPushButton("✎", preview)
        self._rename_button.setObjectName("cardRenameButton")
        self._rename_button.setFixedSize(ui(18), ui(18))
        self._rename_button.setCursor(Qt.PointingHandCursor)
        self._rename_button.setToolTip("编辑名称和标签")
        self._rename_button.setStyleSheet(
            f"QPushButton {{ background: rgba(12, 20, 34, 0.78); color: #eef5ff;"
            f" border-radius: {ui(9)}px; border: 1px solid rgba(145, 181, 239, 0.62);"
            ' font-family: "Segoe UI Symbol", "Segoe UI"; font-size: 12px; font-weight: 600; padding: 0; }'
            "QPushButton:hover { background: #2d65d6; border-color: #91b5ef; color: white; }"
            "QPushButton:pressed { background: #1f4eaa; border-color: #6e9bea; }"
        )
        self._rename_button.clicked.connect(self._on_rename_clicked)
        # “改”红色圆圈（位于修改笔左侧）：点击在自定义名称与原始名称间切换。
        self._custom_badge = QPushButton("改", preview)
        self._custom_badge.setObjectName("customNameBadge")
        self._custom_badge.setFixedSize(ui(18), ui(18))
        self._custom_badge.setCursor(Qt.PointingHandCursor)
        self._custom_badge.clicked.connect(self._toggle_custom_title)
        self._custom_badge.hide()
        self._refresh_title()
        self._layout_corner_buttons()

    def _effective_title(self) -> str:
        """当前应展示的标题：优先自定义名称（可切换），否则用原始名称。"""
        if self._custom_title and self._show_custom:
            return self._custom_title
        return self.mod.title or self.mod.file_name

    def _refresh_title(self) -> None:
        self.title_label.set_full_text(self._effective_title())
        self.title_label.setToolTip(self._effective_title())
        badge = self._custom_badge
        badge.setVisible(bool(self._custom_title))
        # 显示修改后的名称时角标为红色；切换回原始名称时变为主题感知的灰色。
        if self._show_custom:
            bg, bg_hover = "#e0393e", "#f0555a"
            tip = "当前显示：修改后的名称（点击切换为原始名称）"
        else:
            bg, bg_hover = theme_color("badge_original"), theme_color("badge_original_hover")
            tip = "当前显示：原始名称（点击切换为修改后的名称）"
        badge.setToolTip(tip)
        badge.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: #ffffff;"
            f" border-radius: {ui(9)}px; border: none;"
            " font-size: 10px; font-weight: 700; }"
            f"QPushButton:hover {{ background: {bg_hover}; }}"
        )

    def _sync_custom_title(self) -> None:
        """状态刷新时同步持久化的自定义名称，但保留用户当前的显示切换状态。

        只有整页刷新（_populate_cards_batch 重建卡片）才通过
        show_custom_title_by_default() 重置为展示修改后的名称。
        """
        self._custom_title = self.mod.custom_title or ""
        self._refresh_title()

    def show_custom_title_by_default(self) -> None:
        """整页刷新/重建后默认展示修改后的名称。"""
        self._show_custom = True
        self._refresh_title()

    def _on_rename_clicked(self) -> None:
        # 复用原“修改卡片名称”入口，扩展为同时编辑名称与标签。
        # 编辑按钮可能在组合筛选下拉框刚完成一次点击时触发；Qt 会在下一
        # 个事件循环才收起该原生 popup，导致它短暂盖在编辑窗上。打开模态窗
        # 前显式关闭主窗口的下拉层，避免这个闪烁的小框。
        host_window = self.window()
        for combo in host_window.findChildren(QComboBox):
            dismiss = getattr(combo, "dismiss_popup", None)
            if dismiss is not None:
                dismiss()
            else:
                combo.hidePopup()
        available = sorted(set(collect_all_category_ids()) | set(getattr(self.mod, "manual_tags", []) or []))
        dialog = EditModInfoDialog(self.mod, available, self)
        dialog.mod_info_changed.connect(self.mod_info_changed)
        if dialog.exec_() == QDialog.Accepted and self.mod.id == dialog.mod.id:
            # 名称部分：同步到 mixin 内部状态并刷新角标。
            self._custom_title = self.mod.custom_title or ""
            self._show_custom = True
            self._refresh_title()

    def _toggle_custom_title(self) -> None:
        if not self._custom_title:
            return
        self._show_custom = not self._show_custom
        self._refresh_title()

    def _layout_corner_buttons(self) -> None:
        margin = ui(4)
        gap = ui(4)
        self._rename_button.move(
            self.preview.width() - self._rename_button.width() - margin, margin
        )
        # “改”角标紧挨在修改笔按钮的左侧。
        self._custom_badge.move(
            self._rename_button.x() - self._custom_badge.width() - gap, margin
        )


class ModCard(CustomTitleMixin, QFrame):
    clicked = pyqtSignal(str)
    context_requested = pyqtSignal(str, object)
    favorite_toggled = pyqtSignal(str)
    custom_title_changed = pyqtSignal(str, str)  # (mod_id, custom_title)
    mod_info_changed = pyqtSignal(str, str, list, list)  # (mod_id, custom_title, manual_tags, excluded_auto_tags)
    BASE_WIDTH = ui(214)
    BASE_HEIGHT = ui(258)

    def __init__(self, mod: Mod, collection_names: list[str] | None = None, width: int | None = None):
        super().__init__()
        self.mod = mod
        target_width = width or self.BASE_WIDTH
        # Build every widget from the same base geometry, then apply the
        # requested scale once the complete card exists. Previously a newly
        # created non-default-width card kept BASE_HEIGHT while cached cards
        # used their scaled height, producing uneven rows.
        card_width = self.BASE_WIDTH
        self.setObjectName(
            "modCardConflict" if mod.active and mod.conflict_with else ("modCardActive" if mod.active else "modCard")
        )
        self.setCursor(Qt.PointingHandCursor)
        # Card height now hugs the content so there is no large empty gap below
        # the action button. The preview area is enlarged instead.
        self._card_width = card_width
        self.setFixedSize(card_width, self.BASE_HEIGHT)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setProperty("favorite", "true" if mod.favorite else "false")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui(10), ui(10), ui(10), 0)
        # 注意不能用 ui(0)：ui() 会把 0 抬升为 1，导致卡片内每项之间
        # 多出 1px 间距，内容总高超过固定卡片高度后标题会压到预览图上。
        layout.setSpacing(0)
        preview = HoverPreview(mod, card_width - ui(20), ui(112), self)
        preview.setObjectName("preview")
        self.preview = preview
        layout.addWidget(preview)
        # 组合标签：多选组合时叠加在预览图右上角，每个已选组合一个独立彩色圆角小标签。
        # 标签按预览图可用宽度自动换行、右对齐排列，防止超出卡片范围。
        self._collection_names: set[str] = set(collection_names or ())
        self._selected_collection_names: set[str] = set()
        self._collection_tag_host = QWidget(preview)
        self._collection_tag_host.setObjectName("collectionTagHost")
        self._collection_tag_host.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._collection_tag_host.setStyleSheet("background: transparent;")
        self._collection_chips: list[QLabel] = []
        # Default chip font until set_card_width()/_apply_scaled_fonts() runs.
        self._collection_chip_font_size = 9
        self._collection_tag_host.hide()

        title = TwoLineElidedLabel("")
        title.setObjectName("cardTitle")
        self._setup_custom_title(preview, title)
        self._pin_icon = QLabel("📌", preview)
        self._pin_icon.setObjectName("cardPinnedIcon")
        self._pin_icon.setFixedSize(ui(22), ui(22))
        self._pin_icon.setAlignment(Qt.AlignCenter)
        self._pin_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.set_addonlist_pinned(bool(getattr(self.mod, "addonlist_pinned", False)))
        self._layout_pinned_icon()
        # Two Chinese/English title lines need enough line-height to avoid the
        # second line being clipped; metadata then flows beneath the full title.
        # 40px gives two 13px lines comfortable headroom so descenders on the
        # second line are no longer truncated.
        title.setFixedHeight(ui(40))
        self.title_label = title
        layout.addSpacing(ui(2))
        layout.addWidget(title)

        code = mod.workshop_id or Path(mod.file_name).stem
        meta_parts = [code]
        stats = []
        if mod.subscriptions > 0:
            stats.append(f"订阅 {mod.display_subscriptions}")
        if mod.rating > 0:
            stats.append(f"评分 {mod.rating:.1f}")
        if stats:
            meta_parts.extend(stats)
        meta = QLabel("  ·  ".join(meta_parts))
        meta.setObjectName("cardMeta")
        meta.setWordWrap(True)
        meta.setFixedHeight(ui(14))
        self.meta_label = meta
        # 标题与上方预览图、与下方文字保持相同间距。
        layout.addSpacing(ui(2))
        layout.addWidget(meta)

        type_labels = [text for text, _color in mod_type_tags(mod)]
        type_summary = QLabel(f"tags: {' '.join(type_labels)}" if type_labels else "tags: -")
        type_summary.setObjectName("typeSummary")
        type_summary.setToolTip("类型标签：" + ("、".join(type_labels) if type_labels else "暂无"))
        type_summary.setFixedHeight(ui(14))
        self.type_summary_label = type_summary
        layout.addWidget(type_summary)

        star = self._build_favorite_star()
        tags_container = QWidget()
        tags_container.setFixedHeight(ui(28))
        self.tags_container = tags_container
        tags = QHBoxLayout(tags_container)
        tags.setContentsMargins(0, 0, 0, 0)
        self.tags_layout = tags
        tags.setSpacing(ui(5))
        if mod.active:
            tags.addWidget(make_tag("已启用", "#2d65d6"))
        if mod.conflict_with:
            tags.addWidget(make_tag("冲突", "#ff7070"))
        if mod.dependencies:
            dep_tag = make_tag(f"依赖 {len(mod.dependencies)}", "#c9a227")
            dep_tag.setToolTip("启用此 Mod 时会提示一并启用其依赖")
            tags.addWidget(dep_tag)
        self._add_source_tag(tags)
        tags.addStretch(1)
        tags.addWidget(star)
        layout.addWidget(tags_container)

        # Push the action button to the bottom so it lines up across every card
        # in the same row regardless of how many tag chips are shown above.
        layout.addStretch(1)

        action_row = QHBoxLayout()
        action_row.setSpacing(ui(6))
        button = QPushButton("禁用Mod" if mod.active else "启用Mod")
        button.setObjectName("cardActionActive" if mod.active else "cardAction")
        self.toggle_button = button
        button.setFixedHeight(ui(22))
        button.clicked.connect(lambda: self.clicked.emit(self.mod.id))
        action_row.addWidget(button, 1)
        layout.addLayout(action_row)
        # 按钮在标签行与卡片下边界之间上下居中：上下各一个等比例 stretch。
        layout.addStretch(1)
        self.set_card_width(target_width)

    def set_card_width(self, width: int) -> None:
        """Resize the complete card using one scale factor.

        Keeping all geometry derived from the original 214x258 design prevents
        the preview, title, tags and action row from becoming distorted when
        the window or the card-size slider changes.
        """
        width = max(ui(160), int(width))
        if width == self._card_width:
            return
        scale = width / self.BASE_WIDTH
        # 卡片 QSS 的 1px 边框会让 contentsRect 上下各少 1px，可用高度 = 卡片高 - 2。
        # 不补偿时小尺寸卡片内容会溢出 1px，布局被迫压缩预览图，导致标题与图片
        # 的间隙比标题与下方文字的间隙少 1px。
        height = max(ui(190), round(self.BASE_HEIGHT * scale) + 2)
        self._card_width = width
        self.setFixedSize(width, height)
        self.layout().setContentsMargins(
            round(ui(10) * scale), round(ui(10) * scale),
            round(ui(10) * scale), 0,
        )
        self.preview.setFixedSize(max(1, width - round(ui(20) * scale)), round(ui(112) * scale))
        self._layout_corner_buttons()
        self._layout_pinned_icon()
        self.title_label.setFixedHeight(round(ui(40) * scale))
        self.meta_label.setFixedHeight(round(ui(14) * scale))
        self.type_summary_label.setFixedHeight(round(ui(14) * scale))
        self.tags_container.setFixedHeight(round(ui(28) * scale))
        self.favorite_star.setFixedSize(round(ui(28) * scale), round(ui(28) * scale))
        self.toggle_button.setFixedHeight(round(ui(22) * scale))
        self._apply_scaled_fonts(scale)
        # Layout the collection tags only after the chip font has been scaled,
        # otherwise _layout_collection_tag measures widths with the unscaled
        # 9px font and wraps/aligns incorrectly on non-default-size cards.
        self._layout_collection_tag()

    @staticmethod
    def _scaled_px(base: int, scale: float) -> int:
        return max(7, round(base * scale))

    def _apply_scaled_fonts(self, scale: float) -> None:
        """Scale text and chip metrics together with the card geometry."""
        title_size = self._scaled_px(13, scale)
        meta_size = self._scaled_px(10, scale)
        summary_size = self._scaled_px(9, scale)
        action_size = self._scaled_px(11, scale)
        star_size = self._scaled_px(18, scale)
        tag_size = self._scaled_px(9, scale)
        # Collection chips share the tag scale but keep their own key so the
        # chip font is always aligned with whatever the chips were rebuilt with.
        chip_size = tag_size
        font_key = (title_size, meta_size, summary_size, action_size, star_size, tag_size, chip_size)
        if getattr(self, "_font_scale_key", None) == font_key:
            return
        self._font_scale_key = font_key
        self._collection_chip_font_size = chip_size
        self.title_label.setStyleSheet(f"font-size: {title_size}px;")
        self.meta_label.setStyleSheet(f"font-size: {meta_size}px;")
        self.type_summary_label.setStyleSheet(f"font-size: {summary_size}px;")
        self.favorite_star.setStyleSheet(f"font-size: {star_size}px;")
        self.toggle_button.setStyleSheet(
            f"font-size: {action_size}px; min-height: {round(ui(24) * scale)}px;"
            f" max-height: {round(ui(24) * scale)}px;"
        )
        for index in range(self.tags_layout.count()):
            widget = self.tags_layout.itemAt(index).widget()
            if widget is None or widget is self.favorite_star:
                continue
            object_name = widget.objectName()
            if object_name not in ("tag", "tagButton"):
                continue
            base_style = getattr(widget, "_base_style", widget.styleSheet())
            widget._base_style = base_style
            widget.setStyleSheet(
                f"{base_style} #{object_name} {{ font-size: {tag_size}px;"
                f" min-height: {round(ui(20) * scale)}px; max-height: {round(ui(20) * scale)}px; }}"
            )


    def set_collection_context(self, collection_names, selected_names) -> None:
        """Refresh which selected collections this card's Mod belongs to."""
        self._collection_names = set(collection_names or ())
        self._selected_collection_names = set(selected_names or ())
        self._update_collection_tag()

    def _update_collection_tag(self) -> None:
        host = self._collection_tag_host
        if host is None:
            return
        for chip in self._collection_chips:
            chip.deleteLater()
        self._collection_chips = []
        relevant = sorted(self._selected_collection_names & self._collection_names)
        # 仅在选择多个组合时叠加标签；未命中任何已选组合则隐藏。
        if len(self._selected_collection_names) < 2 or not relevant:
            host.hide()
            return
        for name in relevant[:3]:
            self._collection_chips.append(self._make_collection_chip(name, host))
        extra = len(relevant) - 3
        if extra > 0:
            self._collection_chips.append(self._make_collection_chip(f"+{extra}", host, accent=True))
        self._layout_collection_tag()
        host.show()
        host.raise_()

    def _layout_collection_tag(self) -> None:
        """按预览图宽度流式换行并右对齐组合标签，超长文本省略号截断。"""
        host = self._collection_tag_host
        if host is None:
            return
        margin = ui(6)
        spacing = ui(4)
        max_width = max(1, self.preview.width() - 2 * margin)
        rows: list[list[QLabel]] = []
        row: list[QLabel] = []
        row_width = 0
        for chip in self._collection_chips:
            full = chip.property("fullText") or chip.text()
            chip.setText(full)
            chip.adjustSize()
            if chip.width() > max_width:
                chip.setText(chip.fontMetrics().elidedText(full, Qt.ElideRight, max_width))
                chip.adjustSize()
            if row and row_width + chip.width() > max_width:
                rows.append(row)
                row = []
                row_width = 0
            row.append(chip)
            row_width += chip.width() + spacing
        if row:
            rows.append(row)
        y = 0
        for chips_row in rows:
            width = sum(c.width() for c in chips_row) + spacing * (len(chips_row) - 1)
            x = max_width - width  # 每行右对齐
            row_h = 0
            for chip in chips_row:
                chip.move(x, y)
                x += chip.width() + spacing
                row_h = max(row_h, chip.height())
            y += row_h + spacing
        host.resize(max_width, max(0, y - spacing))
        host.move(margin, margin + self._rename_button.height() + spacing)

    def _make_collection_chip(self, name: str, parent: QWidget, accent: bool = False) -> QLabel:
        chip = QLabel(name, parent)
        chip.setObjectName("collectionChip")
        chip.setProperty("fullText", name)
        chip.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # 每个组合用其名称的稳定哈希派生一种专属颜色；超出展示上限的
        # 计数角标（+N）使用中性灰色，避免颜色过多造成视觉噪音。
        if accent:
            color = "hsla(220, 15%, 32%, 0.92)"
        else:
            color = self._collection_chip_color(name)
        chip.setStyleSheet(
            f"background: {color}; color: #ffffff;"
            f" border-radius: {ui(8)}px;"
            f" min-height: {ui(16)}px; max-height: {ui(16)}px;"
            f" padding: 0 6px; font-size: {self._collection_chip_font_size}px;"
            " font-weight: 700;"
        )
        return chip

    @staticmethod
    def _collection_chip_color(name: str) -> str:
        """稳定的组合专属色：同一名称在任何会话、任何主题下颜色一致。"""
        h = 0
        for byte in name.encode("utf-8"):
            h = (h * 31 + byte) & 0xFFFFFFFF
        return f"hsla({h % 360}, 72%, 45%, 0.92)"

    def _build_favorite_star(self) -> QPushButton:
        star = QPushButton("★")
        star.setObjectName("favoriteStar")
        star.setCheckable(True)
        star.setChecked(self.mod.favorite)
        star.setToolTip("取消收藏" if self.mod.favorite else "收藏 Mod")
        star.setFixedSize(ui(28), ui(28))
        star.clicked.connect(self._on_favorite_clicked)
        self.favorite_star = star
        return star

    def _layout_pinned_icon(self) -> None:
        if not hasattr(self, "_pin_icon"):
            return
        self._pin_icon.move(ui(4), ui(4))

    def set_addonlist_pinned(self, pinned: bool) -> None:
        self._pin_icon.setVisible(pinned)
        self._pin_icon.setToolTip("当前已置顶到 addonlist.txt 最上方")

    def _on_favorite_clicked(self) -> None:
        # Defer the actual toggle to the window handler so the state is flipped
        # exactly once (it also persists and updates this card's star visual).
        self.favorite_toggled.emit(self.mod.id)

    def set_favorite(self, favorite: bool) -> None:
        # Compare against the QSS property, not self.mod.favorite: the window
        # handler may flip mod.favorite before calling this, so comparing the
        # model would always report "unchanged" and skip the live border update.
        changed = favorite != (self.property("favorite") == "true")
        self.mod.favorite = favorite
        self.setProperty("favorite", "true" if favorite else "false")
        self.favorite_star.setChecked(favorite)
        self.favorite_star.setToolTip("取消收藏" if favorite else "收藏 Mod")
        if changed:
            # Repolishing the whole subtree on every reused card during a
            # refresh is expensive and can cascade style changes under load.
            # Only restyle when the favorite state actually flips.
            self.style().unpolish(self)
            self.style().polish(self)

    def _add_source_tag(self, layout: QHBoxLayout) -> None:
        if self.mod.steam_loaded and self.mod.workshop_id:
            layout.addWidget(make_tag_button("STEAM", "#365f9f", "打开 Steam 创意工坊页面", self.open_workshop_page))
        else:
            layout.addWidget(make_tag_button("本地", "#526073", "打开 Mod 所在文件夹", self.open_mod_folder))

    def open_workshop_page(self) -> None:
        workshop_id = self.mod.workshop_id
        if workshop_id:
            QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"))

    def open_mod_folder(self) -> None:
        folder = Path(self.mod.file_path).parent
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def refresh_state(self) -> None:
        """Refresh only dynamic controls while keeping the preview image alive."""
        new_card_name = (
            "modCardConflict" if self.mod.active and self.mod.conflict_with else ("modCardActive" if self.mod.active else "modCard")
        )
        # QSS descendant selectors (e.g. #modCardActive #cardTitle) are resolved
        # per child at polish time, so a card-state flip needs the whole subtree
        # re-polished.  Only do that when the state actually changed: re-polishing
        # every reused card on every refresh is the dominant cost under load.
        subtree_dirty = self.objectName() != new_card_name
        self.setObjectName(new_card_name)
        if getattr(self.preview, "_image_path_key", None) != (self.mod.image_path or ""):
            self.preview.refresh_image(self.mod)
        self._sync_custom_title()
        self.set_addonlist_pinned(bool(getattr(self.mod, "addonlist_pinned", False)))
        # 类型标签跟随用户编辑（增删标签）实时刷新。
        type_labels = [text for text, _color in mod_type_tags(self.mod)]
        self.type_summary_label.setText(f"tags: {' '.join(type_labels)}" if type_labels else "tags: -")
        self.type_summary_label.setToolTip("类型标签：" + ("、".join(type_labels) if type_labels else "暂无"))
        # Remove the dynamic tag widgets but never delete the persistent
        # favorite star: it is re-added below and a queued deleteLater on it
        # would free a widget still referenced by the layout (use-after-free
        # crash). Detach everything, drop only the disposable tags, then put
        # the star back.
        detached: list[QWidget] = []
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            if item.widget() is not None:
                detached.append(item.widget())
        for widget in detached:
            if widget is self.favorite_star:
                continue
            widget.deleteLater()
        if self.mod.active:
            self.tags_layout.addWidget(make_tag("已启用", "#2d65d6"))
        if self.mod.conflict_with:
            self.tags_layout.addWidget(make_tag("冲突", "#ff7070"))
        if self.mod.dependencies:
            dep_tag = make_tag(f"依赖 {len(self.mod.dependencies)}", "#c9a227")
            dep_tag.setToolTip("启用此 Mod 时会提示一并启用其依赖")
            self.tags_layout.addWidget(dep_tag)
        self._add_source_tag(self.tags_layout)
        self.tags_layout.addStretch(1)
        if self.favorite_star.parent() is not self.tags_layout:
            self.tags_layout.addWidget(self.favorite_star)
        self.toggle_button.setText("禁用Mod" if self.mod.active else "启用Mod")
        self.toggle_button.setObjectName("cardActionActive" if self.mod.active else "cardAction")
        self.set_favorite(self.mod.favorite)
        # objectName 变化会影响子控件的后代选择器（如 #modCardActive #cardTitle），
        # 只重抛光卡片本身不够：子标签的样式是按各自缓存的，必须整棵子树
        # 重新 unpolish/polish，标题/元信息文字颜色才会跟着激活状态切换。
        if subtree_dirty:
            for widget in self.findChildren(QWidget):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
            self.style().unpolish(self)
            self.style().polish(self)
        self._apply_scaled_fonts(self._card_width / self.BASE_WIDTH)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.mod.id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.mod.id, event.globalPos())
        event.accept()
class HoverPreview(QLabel):
    def __init__(self, mod: Mod, width: int, height: int, parent=None):
        super().__init__(parent)
        self.mod = mod
        self.base_width = width
        self.base_height = height
        self._popup: QLabel | None = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(600)
        self._hover_timer.timeout.connect(self._show_large_preview)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(width, height)
        self.setScaledContents(False)
        self._image_path_key = mod.image_path or ""
        self._scaled: QPixmap | None = make_preview_pixmap(mod, width, height)
        self._compose_key: tuple | None = None
        self._refresh_preview_pixmap()

    def refresh_image(self, mod: Mod) -> None:
        """卡片复用/图片变化时按最新 Mod 重新生成预览图。"""
        self.mod = mod
        self._image_path_key = mod.image_path or ""
        self._scaled = make_preview_pixmap(mod, self.base_width, self.base_height)
        self._refresh_preview_pixmap()

    def resizeEvent(self, event) -> None:
        self._refresh_preview_pixmap()
        super().resizeEvent(event)

    def _refresh_preview_pixmap(self) -> None:
        """Compose a blurred fill and clear foreground into one safe pixmap."""
        if self._scaled is not None and not self._scaled.isNull():
            h = self.height()
            w = self.width()
            if w > 0 and h > 0:
                # The composed result depends only on the source pixmap and the
                # current size.  resizeEvent fires repeatedly while the card is
                # laid out; recomposing on every pass means three smooth
                # scaled() calls per resize, so cache the last composition and
                # bail out when nothing relevant changed.
                compose_key = (self._scaled, w, h)
                if self._compose_key == compose_key:
                    return
                self._compose_key = compose_key
                target = QSize(w, h)
                # A small cover image scaled back up makes a soft backdrop,
                # without using native effects or extra child widgets.
                small = self._scaled.scaled(
                    max(1, w // 12), max(1, h // 12),
                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
                )
                backdrop = small.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                foreground = self._scaled.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                composed = QPixmap(target)
                composed.fill(Qt.transparent)
                painter = QPainter(composed)
                painter.drawPixmap((w - backdrop.width()) // 2, (h - backdrop.height()) // 2, backdrop)
                painter.drawPixmap((w - foreground.width()) // 2, (h - foreground.height()) // 2, foreground)
                painter.end()
                self.setPixmap(composed)

    def enterEvent(self, event) -> None:
        self._hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_timer.stop()
        if self._popup is not None:
            self._popup.hide()
        super().leaveEvent(event)

    def _show_large_preview(self) -> None:
        screen = QApplication.desktop().availableGeometry(self)
        max_width = min(ui(560), screen.width() - ui(40))
        max_height = min(ui(420), screen.height() - ui(80))
        pixmap = make_preview_pixmap(self.mod, max_width, max_height)
        if self._popup is None:
            popup = QLabel(None, Qt.Tool | Qt.FramelessWindowHint)
            popup.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
            popup.setAlignment(Qt.AlignCenter)
            popup.setStyleSheet(
                f"background: {theme_color('panel')};"
                f" border: 2px solid {theme_color('panel_border')}; padding: 8px;"
            )
            self._popup = popup
        popup = self._popup
        popup.setPixmap(pixmap)
        popup.adjustSize()

        source_rect = self.rect()
        source_top_left = self.mapToGlobal(source_rect.topLeft())
        source_rect = source_rect.translated(source_top_left - source_rect.topLeft())
        gap = ui(14)
        right_x = source_rect.right() + gap
        left_x = source_rect.left() - popup.width() - gap
        below_y = source_rect.bottom() + gap
        above_y = source_rect.top() - popup.height() - gap
        if right_x + popup.width() <= screen.right() + 1:
            x = right_x
            y = max(screen.top(), min(source_rect.center().y() - popup.height() // 2, screen.bottom() - popup.height() + 1))
        elif left_x >= screen.left():
            x = left_x
            y = max(screen.top(), min(source_rect.center().y() - popup.height() // 2, screen.bottom() - popup.height() + 1))
        elif below_y + popup.height() <= screen.bottom() + 1:
            x = max(screen.left(), min(source_rect.center().x() - popup.width() // 2, screen.right() - popup.width() + 1))
            y = below_y
        elif above_y >= screen.top():
            x = max(screen.left(), min(source_rect.center().x() - popup.width() // 2, screen.right() - popup.width() + 1))
            y = above_y
        else:
            # Only possible when the preview is larger than the remaining
            # screen area; clamp it as a final fallback.
            x = max(screen.left(), min(source_rect.center().x() - popup.width() // 2, screen.right() - popup.width() + 1))
            y = max(screen.top(), min(source_rect.center().y() - popup.height() // 2, screen.bottom() - popup.height() + 1))
        popup.move(x, y)
        popup.show()
        popup.raise_()
class LegacyConflictDialog(QDialog):
    def __init__(self, mods: dict[str, Mod], parent=None):
        super().__init__(parent)
        self.setWindowTitle("冲突详情")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        title = QLabel("以下已启用 Mod 存在 VPK 内部资源路径重叠")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(12)
        for index, mod in enumerate(item for item in mods.values() if item.conflict_with):
            card = QFrame()
            card.setObjectName("conflictCard")
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(QLabel(f"⚠  {mod.title}"))
            peers = [mods[mod_id].title for mod_id in mod.conflict_with if mod_id in mods]
            detail = QLabel("冲突对象：" + " · ".join(peers))
            detail.setWordWrap(True)
            card_layout.addWidget(detail)
            grid.addWidget(card, index // 2, index % 2)
        scroll.setWidget(host)
        layout.addWidget(scroll)
class DragHeader(QFrame):
    """Drag surface for a frameless window."""

    def __init__(self, target: QWidget, parent=None):
        super().__init__(parent)
        self.target = target
        self._drag_offset = None
        self._native_dragging = False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.target.frameGeometry().topLeft()
            self._native_dragging = False
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            if not self._begin_native_move():
                # Fallback for Qt builds without startSystemMove(): move the
                # window manually, exactly like the original implementation.
                self.target.move(event.globalPos() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        self._native_dragging = False
        event.accept()

    def _begin_native_move(self) -> bool:
        """Start the OS move loop when Qt supports it.

        The native loop moves the window surface without repainting its
        contents on every mouse event, which is what made manual dragging
        stutter on windows with a large custom-painted background.  The
        mouse release is consumed by the loop, so stale drag state is reset
        on the next press.
        """
        if self._native_dragging:
            return True
        handle = self.target.windowHandle()
        start = getattr(handle, "startSystemMove", None) if handle is not None else None
        if start is None:
            return False
        try:
            if start():
                self._native_dragging = True
                self._drag_offset = None
                return True
        except Exception:
            pass
        return False

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            restore_default = getattr(self.target, "restore_default_window", None)
            if restore_default is not None:
                restore_default()
            elif self.target.isMaximized():
                self.target.showNormal()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
class BackgroundSurface(QWidget):
    """Low-contrast full-window image treatment that keeps controls readable."""

    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self._background = QPixmap(str(image_path)) if image_path.exists() else QPixmap()
        # Cache the scaled wallpaper per widget size: smooth-scaling the image
        # is the most expensive part of each repaint, and during window
        # dragging the size does not change, so a cache hit turns every drag
        # frame into a plain blit instead of a full high-quality scale.
        self._scaled_cache: tuple[QSize, QPixmap] | None = None

    def _scaled_background(self) -> QPixmap:
        """Return the wallpaper scaled to the current size, cached per size."""
        size = self.size()
        if size.width() <= 0 or size.height() <= 0:
            return self._background
        if self._scaled_cache is None or self._scaled_cache[0] != size:
            self._scaled_cache = (
                size,
                self._background.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation),
            )
        return self._scaled_cache[1]

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme_color("surface")))
        if not self._background.isNull():
            scaled = self._scaled_background()
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.setOpacity(theme_bg_opacity())
            painter.drawPixmap(x, y, scaled)
        painter.end()
class LegacyConflictCard(QFrame):
    disable_requested = pyqtSignal(str)

    def __init__(self, mod: Mod, peers: list[str]):
        super().__init__()
        self.setObjectName("conflictCard")
        self.setMinimumSize(ui(282), ui(282))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui(12), ui(12), ui(12), ui(12))
        layout.setSpacing(ui(8))
        preview = HoverPreview(mod, ui(188), ui(104), self)
        preview.setObjectName("conflictPreview")
        layout.addWidget(preview)
        title = QLabel(mod.title or mod.file_name)
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        title.setMaximumHeight(ui(42))
        layout.addWidget(title)
        caption = QLabel("与以下 Mod 存在资源路径重叠")
        caption.setObjectName("conflictCaption")
        layout.addWidget(caption)
        peer_text = QLabel(" · ".join(peers))
        peer_text.setObjectName("conflictPeers")
        peer_text.setWordWrap(True)
        peer_text.setVisible(False)
        peer_row = QHBoxLayout()
        peer_row.setSpacing(ui(6))
        for mod_id, peer_title in zip(mod.conflict_with, peers):
            peer = QPushButton(peer_title)
            peer.setObjectName("conflictPeerButton")
            peer.setToolTip("点击禁用此 Mod")
            peer.clicked.connect(lambda _=False, target=mod_id: self.disable_requested.emit(target))
            peer_row.addWidget(peer)
        peer_row.addStretch(1)
        layout.addLayout(peer_row)
class ConflictCard(CustomTitleMixin, QFrame):
    """Compact conflict item. Right-click opens the shared card menu; double-click disables it."""
    disable_requested = pyqtSignal(str)
    context_requested = pyqtSignal(str, object)
    custom_title_changed = pyqtSignal(str, str)
    mod_info_changed = pyqtSignal(str, str, list, list)

    def __init__(self, mod: Mod, width: int | None = None):
        super().__init__()
        self.mod = mod
        card_width = width or ModCard.BASE_WIDTH
        scale = card_width / ModCard.BASE_WIDTH
        card_height = max(ui(190), round(ModCard.BASE_HEIGHT * scale) + 2)
        self.setObjectName("conflictCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(card_width, card_height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # 单击（置顶）与双击（禁用）通过定时器区分：先延迟派发单击，
        # 双击发生时取消，避免双击禁用前先触发一次置顶。
        self._pin_timer = QTimer(self)
        self._pin_timer.setSingleShot(True)
        self._drag_origin: QPoint | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui(10), ui(10), ui(10), ui(10))
        layout.setSpacing(ui(5))
        preview = HoverPreview(mod, card_width - round(ui(20) * scale), round(ui(112) * scale), self)
        preview.setObjectName("conflictPreview")
        self.preview = preview
        layout.addWidget(preview)
        title = TwoLineElidedLabel("")
        title.setObjectName("cardTitle")
        # 13px 两行文字（含 1.32 行高）约需 34px，40px 保证第二行不被裁切。
        title.setFixedHeight(ui(40))
        self._setup_custom_title(preview, title)
        # Keep the report visually consistent with library cards: a Mod that
        # is pinned to the top of addonlist.txt gets the same red pin on its
        # preview, without competing with the conflict-count badge.
        self._pin_icon = QLabel("📌", preview)
        self._pin_icon.setObjectName("cardPinnedIcon")
        self._pin_icon.setFixedSize(ui(22), ui(22))
        self._pin_icon.setAlignment(Qt.AlignCenter)
        self._pin_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._pin_icon.move(ui(4), ui(4))
        self._pin_icon.setToolTip("当前已置顶到 addonlist.txt 最上方")
        self._pin_icon.setVisible(bool(getattr(self.mod, "addonlist_pinned", False)))
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(ui(6))
        title_row.addWidget(title, 1)
        conflict_count = len(mod.conflict_with)
        count_badge = QLabel(str(conflict_count))
        count_badge.setObjectName("conflictCountBadge")
        count_badge.setFixedSize(ui(20), ui(20))
        count_badge.setAlignment(Qt.AlignCenter)
        count_badge.setToolTip(f"Conflicts with {conflict_count} enabled Mod(s)")
        title_row.addWidget(count_badge, 0, Qt.AlignTop)
        layout.addLayout(title_row)
        code = mod.workshop_id or Path(mod.file_name).stem
        details = [f"WORKSHOP {code}"]
        if mod.subscriptions > 0:
            details.append(f"订阅 {mod.display_subscriptions}")
        meta = QLabel("  ·  ".join(details))
        meta.setObjectName("conflictMeta")
        meta.setToolTip(f"Workshop ID: {code}")
        layout.addWidget(meta)
        tags = QHBoxLayout()
        tags.setSpacing(ui(5))
        tags.addWidget(make_tag("冲突", "#b84752"))
        tags.addWidget(make_tag_button("STEAM" if mod.steam_loaded and mod.workshop_id else "本地", "#365f9f" if mod.steam_loaded and mod.workshop_id else "#526073", "打开来源", self.open_source))
        tags.addStretch(1)
        layout.addLayout(tags)
        hint = QLabel("拖动调整优先级 · 双击禁用")
        hint.setObjectName("conflictCaption")
        hint.setText("拖动调整优先级 · 双击禁用")
        hint.setToolTip("拖动：调整同组优先级并同步到 addonlist.txt；双击：禁用该 Mod")
        layout.addWidget(hint)

    def open_source(self) -> None:
        if self.mod.steam_loaded and self.mod.workshop_id:
            QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={self.mod.workshop_id}"))
        else:
            folder = Path(self.mod.file_path).parent
            if folder.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _emit_pin_request(self) -> None:
        # Kept as a harmless compatibility no-op for the old delayed-click path.
        return

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag_origin is not None
            and event.buttons() & Qt.LeftButton
            and (event.pos() - self._drag_origin).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._pin_timer.stop()
            drag = QDrag(self)
            payload = QMimeData()
            payload.setData("application/x-l4dboss-conflict-mod", self.mod.id.encode("utf-8"))
            drag.setMimeData(payload)
            drag.exec_(Qt.MoveAction)
            self._drag_origin = None
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self._pin_timer.stop()
        self._drag_origin = None
        if event.button() == Qt.LeftButton:
            self.disable_requested.emit(self.mod.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._pin_timer.stop()
        self.context_requested.emit(self.mod.id, event.globalPos())
        event.accept()


class ConflictGroupDropHost(QWidget):
    """Accept card drops and report a same-group reordering request."""

    order_requested = pyqtSignal(str, str)  # (dragged_mod_id, target_mod_id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-l4dboss-conflict-mod"):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-l4dboss-conflict-mod"):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat("application/x-l4dboss-conflict-mod"):
            event.ignore()
            return
        source_id = bytes(event.mimeData().data("application/x-l4dboss-conflict-mod")).decode("utf-8")
        target = self.childAt(event.pos())
        while target is not None and not isinstance(target, ConflictCard):
            target = target.parentWidget()
        if isinstance(target, ConflictCard) and target.mod.id != source_id:
            self.order_requested.emit(source_id, target.mod.id)
            event.acceptProposedAction()
            return
        event.ignore()


def conflict_group_sort_key(mod: Mod, priority_positions: dict[str, int] | None = None):
    """冲突组内排序键：用户拖放优先级优先，其次才是传统置顶和冲突数。"""
    priority = (priority_positions or {}).get(mod.id, 10**9)
    pinned = bool(getattr(mod, "addonlist_pinned", False))
    return (priority, 0 if pinned else 1, -len(mod.conflict_with), mod.title.casefold())


class ConflictDialog(QDialog):
    disable_requested = pyqtSignal(str)

    @staticmethod
    def _conflict_groups(mods: dict[str, Mod], priority_ids: list[str] | None = None) -> list[list[Mod]]:
        """Return connected components of the active conflict graph."""
        remaining = {mod_id for mod_id, mod in mods.items() if mod.active and mod.conflict_with}
        priority_positions = {mod_id: index for index, mod_id in enumerate(priority_ids or [])}
        groups: list[list[Mod]] = []
        while remaining:
            pending = [remaining.pop()]
            component: set[str] = set()
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                for peer in mods[current].conflict_with:
                    if peer in remaining:
                        remaining.remove(peer)
                        pending.append(peer)
            groups.append(sorted((mods[mod_id] for mod_id in component), key=lambda mod: conflict_group_sort_key(mod, priority_positions)))
        return sorted(
            groups,
            key=lambda group: (
                -sum(len(mod.conflict_with) for mod in group),
                -len(group),
                group[0].title.casefold(),
            ),
        )

    @staticmethod
    def _group_label(index: int, group: list[Mod]) -> str:
        names = {
            "rifle_ak47": "AK-47", "rifle_m16": "M16", "rifle_desert": "SCAR",
            "rifle_sg552": "SG552", "shotgun_pump": "Pump Shotgun",
            "shotgun_chrome": "Chrome Shotgun", "shotgun_auto": "Auto Shotgun",
            "shotgun_spas": "SPAS-12", "smg_uzi": "Uzi", "smg_silenced": "Silenced SMG",
            "smg_mp5": "MP5", "sniper_awp": "AWP", "sniper_scout": "Scout",
            "sniper_hunting": "Hunting Rifle", "sniper_military": "Military Sniper",
            "pistol_magnum": "Magnum", "pistol_p220": "P220", "m60": "M60", "melee": "近战武器",
        }
        counts = Counter(category for mod in group for category in mod.categories if category in names)
        summary = " / ".join(names[category] for category, _count in counts.most_common(2)) or "资源文件"
        return f"冲突组 {index:02d}  ·  {summary}  ·  {len(group)} 个 Mod"

    def __init__(self, mods: dict[str, Mod], parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        # Four fixed-width cards plus their gaps and section margins need roughly
        # 960 logical pixels.  Keep the dialog wide enough so cards never require
        # a per-group horizontal scrollbar.
        self.resize(ui(980), ui(650))
        self.setMinimumSize(ui(960), ui(460))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = DragHeader(self)
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(ui(18), ui(12), ui(14), ui(12))
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("冲突报告")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("已启用 Mod 的 VPK 内部资源发生重叠")
        subtitle.setObjectName("dialogSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setText("×")
        close.setToolTip("关闭")
        close.clicked.connect(self.reject)
        header_layout.addWidget(close)
        layout.addWidget(header)

        body = QFrame()
        body.setObjectName("conflictBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("conflictScroll")
        scroll.setWidgetResizable(True)
        scroll.viewport().setObjectName("conflictViewport")
        host = QWidget()
        host.setObjectName("conflictHost")
        rows = QVBoxLayout(host)
        rows.setContentsMargins(ui(18), ui(18), ui(18), ui(18))
        rows.setSpacing(ui(18))
        for index, group in enumerate(self._conflict_groups(mods), start=1):
            section = QFrame()
            section.setObjectName("conflictGroup")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(ui(12), ui(10), ui(12), ui(12))
            section_layout.setSpacing(ui(8))
            group_label = QLabel(f"冲突组 {index:02d}  ·  {len(group)} 个 Mod")
            group_label.setObjectName("conflictGroupLabel")
            group_label.setText(self._group_label(index, group))
            section_layout.addWidget(group_label)
            section_layout.addWidget(self._make_group_grid(group))
            rows.addWidget(section)
        rows.addStretch(1)
        self._rows = rows
        self._mods = mods
        scroll.setWidget(host)
        body_layout.addWidget(scroll)
        layout.addWidget(body, 1)

    def _make_group_grid(self, group: list[Mod]) -> QWidget:
        cards_host = QWidget()
        cards_host.setObjectName("groupCardsHost")
        cards_grid = QGridLayout(cards_host)
        cards_grid.setContentsMargins(0, 0, 0, 0)
        cards_grid.setHorizontalSpacing(ui(12))
        cards_grid.setVerticalSpacing(ui(12))
        cards_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for index, mod in enumerate(sorted(group, key=conflict_group_sort_key)):
            card = ConflictCard(mod)
            card.disable_requested.connect(self.disable_requested)
            cards_grid.addWidget(card, index // 4, index % 4)
        return cards_host

    def refresh_after_disable(self, _mod_id: str) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        groups = self._conflict_groups(self._mods)
        if not groups:
            message = QLabel("没有剩余冲突")
            message.setObjectName("emptyText")
            message.setAlignment(Qt.AlignCenter)
            self._rows.addWidget(message)
            self._rows.addStretch(1)
            return
        for index, group in enumerate(groups, start=1):
            section = QFrame()
            section.setObjectName("conflictGroup")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(ui(12), ui(10), ui(12), ui(12))
            section_layout.setSpacing(ui(8))
            group_label = QLabel(f"冲突组 {index:02d}  ·  {len(group)} 个 Mod")
            group_label.setObjectName("conflictGroupLabel")
            group_label.setText(self._group_label(index, group))
            section_layout.addWidget(group_label)
            section_layout.addWidget(self._make_group_grid(group))
            self._rows.addWidget(section)
        self._rows.addStretch(1)
class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedSize(ui(480), ui(330))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = DragHeader(self)
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(ui(18), ui(10), ui(12), ui(10))
        title = QLabel("关于 L4D2 BOSS")
        title.setObjectName("dialogTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setText("×")
        close.setToolTip("关闭")
        close.clicked.connect(self.accept)
        header_layout.addWidget(close)
        layout.addWidget(header)

        content = QWidget()
        content.setObjectName("aboutContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(ui(28), ui(24), ui(28), ui(24))
        content_layout.setSpacing(ui(10))
        brand = QLabel("L4D2 BOSS")
        brand.setObjectName("aboutBrand")
        version = QLabel("L4D2 Mod Loadout Manager  ·  v1.0.0")
        version.setObjectName("aboutVersion")
        designer = QLabel("设计人：陈端云（Mr.Chen）")
        designer.setObjectName("aboutDesigner")
        description = QLabel("一个面向《求生之路 2》的本地 Mod 管理工具。\n\n支持扫描 VPK、Steam 信息同步、分类管理、组合保存，以及已启用 Mod 的资源冲突检测。")
        description.setObjectName("aboutDescription")
        description.setWordWrap(True)
        content_layout.addWidget(brand)
        content_layout.addWidget(version)
        content_layout.addWidget(designer)
        content_layout.addSpacing(ui(4))
        content_layout.addWidget(description)
        github = QLabel("GitHub：https://github.com/chenduanyun091216/L4DBoss")
        github.setObjectName("aboutLink")
        github.setCursor(Qt.PointingHandCursor)
        github.setStyleSheet(f"color: {theme_color('link')};")
        github.mouseReleaseEvent = lambda _e: QDesktopServices.openUrl(QUrl("https://github.com/chenduanyun091216/L4DBoss"))
        content_layout.addWidget(github)
        content_layout.addStretch(1)
        layout.addWidget(content, 1)
class ModDetailsDialog(QDialog):
    """Frameless, read-only detail view for a single Mod."""

    def __init__(self, mod: Mod, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.resize(ui(620), ui(560))
        self.setMinimumSize(ui(500), ui(420))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = DragHeader(self)
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(ui(18), ui(10), ui(12), ui(10))
        title = QLabel("Mod 详细信息")
        title.setObjectName("dialogTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setToolTip("关闭")
        close.clicked.connect(self.accept)
        header_layout.addWidget(close)
        root.addWidget(header)

        content = QFrame()
        content.setObjectName("modDetailsContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(ui(20), ui(18), ui(20), ui(18))
        layout.setSpacing(ui(10))
        name = QLabel(mod.title or mod.file_name)
        name.setObjectName("modDetailsTitle")
        name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(name)

        code = mod.workshop_id or Path(mod.file_name).stem
        fields = [
            ("文件", mod.file_name),
            ("编号", code),
            ("作者", mod.author or "未知"),
            ("订阅", mod.display_subscriptions if mod.subscriptions else "暂无"),
            ("评分", f"{mod.rating:.1f}" if mod.rating else "暂无"),
            ("来源", "Steam 创意工坊" if mod.steam_loaded and mod.workshop_id else "本地文件"),
            ("状态", "已启用" if mod.active else "已禁用"),
            ("分类", "、".join(mod.categories) if mod.categories else "未分类"),
            ("文件路径", mod.file_path),
        ]
        for label, value in fields:
            row = QHBoxLayout()
            row.setSpacing(ui(10))
            key = QLabel(label)
            key.setObjectName("modDetailsKey")
            key.setFixedWidth(ui(62))
            value_label = QLabel(str(value))
            value_label.setObjectName("modDetailsValue")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(key, 0, Qt.AlignTop)
            row.addWidget(value_label, 1)
            layout.addLayout(row)

        description_title = QLabel("描述")
        description_title.setObjectName("modDetailsKey")
        layout.addWidget(description_title)
        description = QLabel(mod.description.strip() or "暂无描述")
        description.setObjectName("modDetailsDescription")
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(description)
        layout.addStretch(1)
        root.addWidget(content, 1)


QtFileDialog = QFileDialog
class AppMessageDialog(QDialog):
    """Frameless, application-themed replacement for system message boxes."""

    def __init__(self, message: str, tone: str = "info", confirm: bool = False, parent=None):
        super().__init__(parent)
        # WindowStaysOnTopHint keeps the prompt above the card area and any
        # frameless tool popups (e.g. HoverPreview) so it is never covered.
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(ui(390))
        self.setMaximumWidth(ui(520))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        surface = QFrame()
        surface.setObjectName("promptSurface")
        root.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(ui(22), ui(20), ui(22), ui(18))
        layout.setSpacing(ui(14))

        content = QHBoxLayout()
        content.setSpacing(ui(12))
        icon = QLabel("!" if tone in {"warning", "error"} else "i")
        icon.setObjectName(f"promptIcon{tone.title()}")
        icon.setFixedSize(ui(30), ui(30))
        icon.setAlignment(Qt.AlignCenter)
        content.addWidget(icon, 0, Qt.AlignTop)
        text = QLabel(message)
        text.setObjectName("promptText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.addWidget(text, 1)
        layout.addLayout(content)

        actions = QHBoxLayout()
        actions.addStretch(1)
        if confirm:
            cancel = QPushButton("\u53d6\u6d88")
            cancel.setObjectName("promptSecondaryButton")
            cancel.clicked.connect(self.reject)
            actions.addWidget(cancel)
        accept = QPushButton("\u786e\u5b9a")
        accept.setObjectName("promptPrimaryButton")
        accept.clicked.connect(self.accept)
        actions.addWidget(accept)
        layout.addLayout(actions)
class AppInputDialog(QDialog):
    """Frameless input prompt used for naming saved loadouts."""

    def __init__(self, label: str, initial_text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(ui(420))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        surface = QFrame()
        surface.setObjectName("promptSurface")
        root.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(ui(22), ui(20), ui(22), ui(18))
        layout.setSpacing(ui(12))
        prompt = QLabel(label)
        prompt.setObjectName("promptText")
        layout.addWidget(prompt)
        self.input = QLineEdit(initial_text)
        self.input.setObjectName("promptInput")
        self.input.returnPressed.connect(self.accept)
        layout.addWidget(self.input)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("\u53d6\u6d88")
        cancel.setObjectName("promptSecondaryButton")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("\u786e\u5b9a")
        confirm.setObjectName("promptPrimaryButton")
        confirm.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        layout.addLayout(actions)
        QTimer.singleShot(0, self.input.setFocus)

    @classmethod
    def get_text(cls, parent, _title: str, label: str, text: str = "") -> tuple[str, bool]:
        dialog = cls(label, text, parent)
        accepted = dialog.exec_() == QDialog.Accepted
        return dialog.input.text(), accepted
class AppMessageBox:
    """Compatibility facade for existing QMessageBox call sites."""

    Yes, No = 1, 0

    @staticmethod
    def information(parent, _title: str, message: str) -> int:
        return AppMessageDialog(message, "info", parent=parent).exec_()

    @staticmethod
    def warning(parent, _title: str, message: str) -> int:
        return AppMessageDialog(message, "warning", parent=parent).exec_()

    @staticmethod
    def critical(parent, _title: str, message: str) -> int:
        return AppMessageDialog(message, "error", parent=parent).exec_()

    @staticmethod
    def question(parent, _title: str, message: str) -> int:
        accepted = AppMessageDialog(message, "warning", confirm=True, parent=parent).exec_() == QDialog.Accepted
        return AppMessageBox.Yes if accepted else AppMessageBox.No
class AppInputBox:
    @staticmethod
    def getText(parent, title: str, label: str, text: str = "") -> tuple[str, bool]:
        return AppInputDialog.get_text(parent, title, label, text)
class AppFileDialog:
    @staticmethod
    def getOpenFileName(parent, _title: str, directory: str = "", filter: str = "") -> tuple[str, str]:
        dialog = QtFileDialog(parent)
        dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dialog.setOption(QtFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QtFileDialog.ExistingFile)
        dialog.setNameFilter(filter)
        dialog.setDirectory(directory)
        files = dialog.selectedFiles() if dialog.exec_() else []
        return (files[0], filter) if files else ("", "")

    @staticmethod
    def getExistingDirectory(parent, _title: str, directory: str = "") -> str:
        dialog = QtFileDialog(parent)
        dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dialog.setOption(QtFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QtFileDialog.Directory)
        dialog.setOption(QtFileDialog.ShowDirsOnly, True)
        dialog.setDirectory(directory)
        return dialog.selectedFiles()[0] if dialog.exec_() and dialog.selectedFiles() else ""


# Keep existing call sites concise while ensuring every application prompt is frameless.
QMessageBox = AppMessageBox
QInputDialog = AppInputBox
QFileDialog = AppFileDialog


class AppToast(QWidget):
    """Non-modal, auto-dismissing notification shown after background work.

    Unlike AppMessageDialog it never blocks the UI: the user can keep
    clicking and interacting with the main window while the toast is visible.
    """

    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("appToast")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(ui(16), ui(11), ui(16), ui(11))
        layout.setSpacing(ui(10))
        icon = QLabel("✓")
        icon.setObjectName("toastIcon")
        icon.setFixedSize(ui(22), ui(22))
        icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)
        text = QLabel(message)
        text.setObjectName("toastText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(text, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Auto-dismiss so the toast never lingers or blocks the workflow.
        QTimer.singleShot(3200, self.close)


def show_toast(message: str, parent=None) -> None:
    """Display a non-blocking toast anchored to the bottom-center of the window."""
    host = parent if isinstance(parent, QWidget) else None
    toast = AppToast(message, host)
    toast.setMinimumWidth(ui(240))
    toast.setMaximumWidth(ui(420))
    toast.adjustSize()
    if host is not None:
        geo = host.geometry()
        x = geo.x() + (geo.width() - toast.width()) // 2
        y = geo.y() + geo.height() - toast.height() - ui(28)
        toast.move(x, y)
    toast.show()
class CollectionItemDelegate(QStyledItemDelegate):
    """Paint compact edit/delete affordances on the right side of each collection."""

    edit_width = 18
    delete_width = 34

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        edit_rect = option.rect.adjusted(
            option.rect.width() - self.edit_width - self.delete_width, 0, -self.delete_width, 0
        )
        delete_rect = option.rect.adjusted(option.rect.width() - self.delete_width, 0, 0, 0)
        # This is a multi-check popup, not a single-selection list.  Remove
        # the default blue hover/selection state while keeping each item's
        # checkbox state visible.
        option.state &= ~QStyle.State_MouseOver
        option.state &= ~QStyle.State_Selected
        super().paint(painter, option, index)
        painter.save()
        # The edit/delete affordances are painted after the default delegate,
        # so restore the normal popup background in their areas as well.
        background = option.palette.base().color()
        painter.fillRect(edit_rect, background)
        painter.fillRect(delete_rect, background)
        accent = QColor(
            theme_color("tree_default") if index.data(Qt.UserRole) == "default" else theme_color("tree_favorite")
        )
        # The pen glyph carries much more visual weight than the slim "×", so
        # render it a few points smaller; it also hugs the delete button so
        # the two affordances read as one compact right-aligned control.
        glyph_rect = edit_rect.adjusted(0, 0, 0, 0)
        glyph_rect.setLeft(max(edit_rect.left(), edit_rect.right() - ui(16)))
        painter.setPen(QColor(theme_color("tree_default")))
        painter.setFont(QFont("Segoe UI Symbol", max(6, ui(7))))
        painter.drawText(glyph_rect, Qt.AlignCenter, "✎")
        painter.setPen(accent)
        painter.drawText(delete_rect, Qt.AlignCenter, "×")
        painter.restore()
class MultiSelectComboBox(QComboBox):
    """A checkable combo box that keeps its popup open for multi-selection."""

    selection_changed = pyqtSignal()
    collection_delete_requested = pyqtSignal(str)
    collection_rename_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setCursor(Qt.PointingHandCursor)
        self.setCursor(Qt.PointingHandCursor)
        self.setItemDelegate(CollectionItemDelegate(self))
        self._popup_open = False
        self._keep_popup_open = False
        self.view().setSelectionMode(QAbstractItemView.NoSelection)
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self.view().isVisible():
                self.hidePopup()
            else:
                self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor(theme_color("tree_expand")))
        painter.setFont(QFont("Segoe UI Symbol", max(9, ui(13)), QFont.Bold))
        painter.translate(0, -ui(1))
        painter.drawText(self.rect().adjusted(0, 0, -ui(9), 0), Qt.AlignRight | Qt.AlignVCenter, "⌄")
        painter.end()

    def showPopup(self) -> None:
        if self.view().isVisible():
            return
        self._popup_open = True
        super().showPopup()

    def hidePopup(self) -> None:
        # QComboBox may request closing immediately after a popup item is
        # clicked.  A checkable item is not a final selection, so keep the
        # popup open until the user clicks outside it or toggles the combo.
        if self._keep_popup_open:
            return
        if not self.view().isVisible() and not self._popup_open:
            return
        self._popup_open = False
        super().hidePopup()

    def dismiss_popup(self) -> None:
        """无条件关闭下拉层，用于切换到模态窗口前清理残留 popup。"""
        self._keep_popup_open = False
        self._popup_open = False
        super().hidePopup()

    def togglePopup(self) -> None:
        if self._popup_open or self.view().isVisible():
            self.hidePopup()
        else:
            self.showPopup()

    def eventFilter(self, source, event) -> bool:
        if source is self.lineEdit() and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                if self.view().isVisible():
                    self.hidePopup()
                else:
                    self.showPopup()
            return True
        if source is self.view().viewport() and event.type() == QEvent.MouseButtonPress:
            index = self.view().indexAt(event.pos())
            if index.isValid():
                item_rect = self.view().visualRect(index)
                right_gap = item_rect.right() - event.pos().x()
                if right_gap < CollectionItemDelegate.delete_width:
                    self.collection_delete_requested.emit(str(self.itemData(index.row())))
                    return True
                if right_gap < CollectionItemDelegate.delete_width + CollectionItemDelegate.edit_width:
                    self.collection_rename_requested.emit(str(self.itemData(index.row())))
                    return True
                state = self.model().data(index, Qt.CheckStateRole)
                next_state = Qt.Unchecked if state == Qt.Checked else Qt.Checked
                self._keep_popup_open = True
                self.model().setData(index, next_state, Qt.CheckStateRole)
                self.selection_changed.emit()
                return True
        if source is self.view().viewport() and event.type() == QEvent.MouseMove:
            index = self.view().indexAt(event.pos())
            if index.isValid():
                item_rect = self.view().visualRect(index)
                right_gap = item_rect.right() - event.pos().x()
                if right_gap < CollectionItemDelegate.delete_width:
                    self.view().setToolTip("删除组合")
                elif right_gap < CollectionItemDelegate.delete_width + CollectionItemDelegate.edit_width:
                    self.view().setToolTip("修改组合名称")
                else:
                    self.view().setToolTip("")
            else:
                self.view().setToolTip("")
        if source is self.view().viewport() and event.type() == QEvent.MouseButtonRelease:
            # Consume the release too. QComboBox normally treats it as a
            # completed single selection and closes its popup.
            if self._keep_popup_open and event.button() == Qt.LeftButton:
                QTimer.singleShot(0, self._release_popup_guard)
                return True
        return super().eventFilter(source, event)

    def _release_popup_guard(self) -> None:
        self._keep_popup_open = False
        if not self.view().isVisible() and self._popup_open:
            self.showPopup()

    def checked_values(self) -> list[str]:
        return [
            self.itemData(index)
            for index in range(self.count())
            if self.model().data(self.model().index(index, 0), Qt.CheckStateRole) == Qt.Checked
        ]
class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checked = False
        self.setFixedSize(ui(38), ui(20))
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._checked)
        event.accept()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self.rect().adjusted(0, 1, -1, -2)
        painter.setPen(QColor(theme_color("toggle_on_border") if self._checked else theme_color("toggle_off_border")))
        painter.setBrush(QColor(theme_color("toggle_on_fill") if self._checked else theme_color("toggle_off_fill")))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob_diameter = max(1, track.height() - ui(4))
        knob_x = track.right() - knob_diameter - ui(2) if self._checked else track.left() + ui(2)
        knob = track.adjusted(knob_x - track.left(), ui(2), knob_x - track.right() + knob_diameter, -ui(2))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_color("toggle_knob")))
        painter.drawEllipse(knob)
def steam_sync_candidates(mods: dict[str, Mod]) -> dict[str, Mod]:
    """Return only Workshop Mods without successfully cached Steam metadata."""
    return {
        mod_id: mod
        for mod_id, mod in mods.items()
        if mod.workshop_id and not mod.steam_loaded
    }
def fetch_steam_for_mods(mods: dict[str, Mod], progress_callback=None, cancel_event: Event | None = None) -> dict[str, Mod]:
    client = SteamClient()
    total = len(mods)
    for completed, mod in enumerate(mods.values(), start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelled()
        # Keep this guard here as well as in steam_sync_candidates(), so a
        # future caller cannot accidentally re-fetch cached entries.
        if mod.workshop_id and not mod.steam_loaded:
            info = client.fetch(mod.workshop_id)
            mod.title, mod.author = info.title or mod.title, info.author or mod.author
            mod.subscriptions = info.subscriptions if info.subscriptions is not None else mod.subscriptions
            mod.rating = info.rating if info.rating is not None else mod.rating
            mod.description = info.description or mod.description
            if info.tags is not None:
                mod.steam_tags = info.tags
            mod.steam_loaded = bool(info.title or info.author or info.subscriptions or info.rating or info.tags)
            mod.categories = infer_categories(
                mod.title,
                mod.files,
                steam_tags=mod.steam_tags,
                description=mod.description,
                file_name=mod.file_name,
            )
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelled()
        if progress_callback is not None:
            progress_callback(completed, total)
    return mods
def _readable_text_color(background: str) -> str:
    """Pick a text color that contrasts with the tag fill.

    The themes' tag QSS forces white text, which is unreadable on the lighter
    fills (conflict pink / dependency gold).  Compute WCAG relative luminance
    and return a near-black for light fills so every theme stays readable
    without hard-coding per-theme colors here.
    """
    color = QColor(background)
    if not color.isValid():
        return "#ffffff"

    def _linear(channel: int) -> float:
        value = channel / 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    luminance = (
        0.2126 * _linear(color.red()) + 0.7152 * _linear(color.green()) + 0.0722 * _linear(color.blue())
    )
    # Threshold 0.18: white text keeps >= 4.5:1 below it, black text above it.
    return "#000000" if luminance > 0.18 else "#ffffff"


def make_tag(text: str, color: str) -> QLabel:
    tag = QLabel(text)
    tag.setObjectName("tag")
    tag.setStyleSheet(f"#tag {{ background: {color}; color: {_readable_text_color(color)}; }}")
    return tag
def make_tag_button(text: str, color: str, tooltip: str, handler) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("tagButton")
    button.setToolTip(tooltip)
    button.setStyleSheet(f"#tagButton {{ background: {color}; color: {_readable_text_color(color)}; }}")
    button.clicked.connect(handler)
    return button
def make_preview_pixmap(mod: Mod, max_width: int = ui(188), max_height: int = ui(104)) -> QPixmap:
    cache_key = (mod.image_path or "__placeholder__", max_width, max_height)
    cached = PREVIEW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if mod.image_path and Path(mod.image_path).exists():
        pixmap = QPixmap(mod.image_path)
        if not pixmap.isNull():
            result = pixmap.scaled(max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            PREVIEW_CACHE[cache_key] = result
            return result
    pixmap = QPixmap(max_width, max_height)
    gradient = QLinearGradient(0, 0, pixmap.width(), pixmap.height())
    gradient.setColorAt(0, QColor("#263d61")); gradient.setColorAt(1, QColor("#151c29"))
    painter = QPainter(pixmap)
    painter.fillRect(pixmap.rect(), gradient)
    painter.setPen(QColor("#91a8c9"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "L4D2\nMOD")
    painter.end()
    PREVIEW_CACHE[cache_key] = pixmap
    return pixmap
def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
class DependencyDialog(QDialog):
    """Frameless dialog to edit one Mod's dependency records.

    Users can check local Mods as dependencies, register Workshop ids that are
    not installed yet (they resolve once that Mod is downloaded and scanned),
    or auto-detect candidates from the Mod's Steam description links.
    """

    def __init__(self, mod: Mod, mods: dict[str, Mod], parent=None):
        super().__init__(parent)
        self.mod = mod
        self.mods = mods
        self._missing_ids: list[str] = [dep for dep in mod.dependencies if dep not in mods]
        self._dep_ids: set[str] = {dep for dep in mod.dependencies if dep in mods}
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.resize(ui(600), ui(540))
        self.setMinimumSize(ui(500), ui(440))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = DragHeader(self)
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(ui(18), ui(10), ui(12), ui(10))
        header_title = QLabel("管理依赖")
        header_title.setObjectName("dialogTitle")
        header_layout.addWidget(header_title)
        header_layout.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setToolTip("关闭")
        close.clicked.connect(self.reject)
        header_layout.addWidget(close)
        root.addWidget(header)

        content = QWidget()
        content.setObjectName("aboutContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(ui(18), ui(14), ui(18), ui(14))
        content_layout.setSpacing(ui(10))

        hint = QLabel(f"为「{mod.title or mod.file_name}」设置依赖关系。启用该 Mod 时，应用会提示一并启用已安装的依赖；未安装的依赖（Workshop 编号）在下载并扫描后会自行生效。")
        hint.setObjectName("aboutDescription")
        hint.setWordWrap(True)
        content_layout.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        content_layout.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        actions.setSpacing(ui(8))
        detect = QPushButton("从简介识别依赖")
        detect.setObjectName("promptSecondaryButton")
        detect.setToolTip("扫描该 Mod 的 Steam 简介，把其中的创意工坊链接识别为依赖候选")
        detect.clicked.connect(self._detect_from_description)
        actions.addWidget(detect)
        add_id = QPushButton("按 ID 添加缺失依赖")
        add_id.setObjectName("promptSecondaryButton")
        add_id.setToolTip("输入尚未安装的依赖 Mod 的 Workshop 编号或页面链接")
        add_id.clicked.connect(self._add_missing_id)
        actions.addWidget(add_id)
        actions.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("promptSecondaryButton")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        ok = QPushButton("确定")
        ok.setObjectName("promptPrimaryButton")
        ok.clicked.connect(self.accept)
        actions.addWidget(ok)
        content_layout.addLayout(actions)
        root.addWidget(content, 1)

        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        """Rebuild the picker tree from the current dependency selection."""
        self.tree.blockSignals(True)
        self.tree.clear()
        local_mods = [mod for mod in self.mods.values() if mod.id != self.mod.id]
        selected = [mod for mod in local_mods if mod.id in self._dep_ids]
        selected.sort(key=lambda mod: (mod.title or mod.file_name).casefold())
        available = [mod for mod in local_mods if mod.id not in self._dep_ids]
        available.sort(key=lambda mod: (mod.title or mod.file_name).casefold())

        dep_group = QTreeWidgetItem([f"依赖的 Mod（已勾选 {len(selected)} 个）"])
        dep_group.setFlags(dep_group.flags() & ~Qt.ItemIsSelectable)
        self.tree.addTopLevelItem(dep_group)
        for mod in selected:
            item = self._make_check_item(mod, True)
            dep_group.addChild(item)

        local_group = QTreeWidgetItem([f"本地可用 Mod（{len(available)} 个）"])
        local_group.setFlags(local_group.flags() & ~Qt.ItemIsSelectable)
        self.tree.addTopLevelItem(local_group)
        for mod in available:
            item = self._make_check_item(mod, False)
            local_group.addChild(item)

        missing_group = QTreeWidgetItem([f"缺失依赖 · 未安装（{len(self._missing_ids)} 个）"])
        missing_group.setFlags(missing_group.flags() & ~Qt.ItemIsSelectable)
        self.tree.addTopLevelItem(missing_group)
        for dep_id in self._missing_ids:
            row = QTreeWidgetItem([f"Workshop {dep_id}"])
            row.setFlags(row.flags() & ~Qt.ItemIsUserCheckable)
            row.setToolTip(0, f"Workshop 编号 {dep_id}：下载并安装后会自动生效")
            missing_group.addChild(row)
            remove = QPushButton("×")
            remove.setObjectName("tagButton")
            remove.setToolTip("移除该缺失依赖")
            remove.setFixedSize(ui(22), ui(20))
            remove.clicked.connect(lambda _=False, mid=dep_id: self._remove_missing(mid))
            self.tree.setItemWidget(row, 0, remove)

        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _make_check_item(self, mod: Mod, checked: bool) -> QTreeWidgetItem:
        code = mod.workshop_id or Path(mod.file_name).stem
        item = QTreeWidgetItem([f"{mod.title or mod.file_name}　{code}"])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        item.setData(0, Qt.UserRole, mod.id)
        item.setToolTip(0, mod.file_name)
        return item

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        mod_id = item.data(0, Qt.UserRole)
        if not mod_id:
            return
        if item.checkState(0) == Qt.Checked:
            self._dep_ids.add(mod_id)
        else:
            self._dep_ids.discard(mod_id)
        self._refresh_group_counts()

    def _refresh_group_counts(self) -> None:
        """Keep the picker group headers in sync with the live check states."""
        if self.tree.topLevelItemCount() < 2:
            return
        dep_group = self.tree.topLevelItem(0)
        local_group = self.tree.topLevelItem(1)
        dep_checked = sum(
            1 for index in range(dep_group.childCount())
            if dep_group.child(index).checkState(0) == Qt.Checked
        )
        local_checked = sum(
            1 for index in range(local_group.childCount())
            if local_group.child(index).checkState(0) == Qt.Checked
        )
        dep_group.setText(0, f"依赖的 Mod（已勾选 {dep_checked + local_checked} 个）")

    def _detect_from_description(self) -> None:
        """Auto-detect dependencies from Workshop links in the Steam description."""
        found = extract_workshop_ids(self.mod.description)
        added_local = 0
        added_missing = 0
        for dep_id in found:
            if dep_id == self.mod.id:
                continue
            if dep_id in self.mods:
                if dep_id not in self._dep_ids:
                    self._dep_ids.add(dep_id)
                    added_local += 1
            elif dep_id not in self._missing_ids:
                self._missing_ids.append(dep_id)
                added_missing += 1
        self._rebuild_tree()
        if not found:
            QMessageBox.information(self, "未识别到依赖", "该 Mod 的 Steam 简介中没有找到创意工坊链接。")
        elif added_local or added_missing:
            QMessageBox.information(self, "识别完成", f"从简介中识别到 {len(found)} 个链接：{added_local} 个本地 Mod 已勾选，{added_missing} 个缺失依赖已加入。")

    def _add_missing_id(self) -> None:
        text, ok = QInputDialog.getText(self, "添加缺失依赖", "输入依赖 Mod 的 Workshop 编号或页面链接：")
        if not ok or not text.strip():
            return
        ids = extract_workshop_ids(text) or ([re.sub(r"\D", "", text.strip())] if re.fullmatch(r"\d{5,}", text.strip()) else [])
        added = 0
        for dep_id in ids:
            if not dep_id:
                continue
            if dep_id == self.mod.id:
                continue
            if dep_id in self.mods:
                if dep_id not in self._dep_ids:
                    self._dep_ids.add(dep_id)
                    added += 1
            elif dep_id not in self._missing_ids:
                self._missing_ids.append(dep_id)
                added += 1
        self._rebuild_tree()
        if not ids:
            QMessageBox.warning(self, "无法识别", "未能从输入中识别出 Workshop 编号，请输入形如 id=123456789 的链接或纯数字编号。")
        elif added:
            QMessageBox.information(self, "已添加", f"已添加 {added} 个依赖。")

    def _remove_missing(self, dep_id: str) -> None:
        if dep_id in self._missing_ids:
            self._missing_ids.remove(dep_id)
        self._rebuild_tree()

    def dependency_ids(self) -> list[str]:
        """Final dependency list: previous order preserved, then additions, then missing."""
        ordered: list[str] = []
        seen: set[str] = set()
        for dep in self.mod.dependencies:
            if (dep in self._dep_ids or dep in self._missing_ids) and dep not in seen:
                seen.add(dep)
                ordered.append(dep)
        candidates = sorted(self._dep_ids, key=lambda mid: (self.mods[mid].title or self.mods[mid].file_name).casefold())
        for dep in candidates:
            if dep not in seen:
                seen.add(dep)
                ordered.append(dep)
        for dep in self._missing_ids:
            if dep not in seen:
                seen.add(dep)
                ordered.append(dep)
        return ordered


class FlowLayout(QLayout):
    """横向自动换行的流式布局（精简自 PySide6 官方示例）。"""

    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing if spacing >= 0 else ui(6))
        self._items: list[QLayoutItem] = []
        # 保活子 widget 的 Python 引用，避免局部变量离开作用域后被 GC，
        # 否则下次布局计算时 item.widget() 返回失效 wrapper，访问已回收内存会崩溃。
        self._keepalive: list[QWidget] = []

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)
        w = item.widget()
        if w is not None:
            self._keepalive.append(w)

    def addWidget(self, w: QWidget) -> None:
        # QWidgetItem 本身不会把控件收为布局父对象；此前漏掉这一句会让
        # chip 保持无父窗口状态，在 Windows 上显示成左上角一闪而过的小窗。
        # 先交给 QLayout 接管父子关系，才能在 _do_layout() 中定位到标签容器内。
        self.addChildWidget(w)
        self.addItem(QWidgetItem(w))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        item = self._items.pop(index) if 0 <= index < len(self._items) else None
        if item is not None:
            w = item.widget()
            if w is not None and w in self._keepalive:
                self._keepalive.remove(w)
        return item

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x = rect.x() + self.contentsMargins().left()
        y = rect.y() + self.contentsMargins().top()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            wid = item.widget()
            if wid is None or wid.isHidden():
                continue
            sz = wid.sizeHint()
            next_x = x + sz.width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x() + self.contentsMargins().left()
                y += line_height + spacing
                next_x = x + sz.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), sz))
            x = next_x
            line_height = max(line_height, sz.height())
        return y - rect.y() + line_height + self.contentsMargins().bottom()

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            w = item.widget()
            if w is not None:
                size = size.expandedTo(w.sizeHint())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size


class EditModInfoDialog(QDialog):
    """编辑单个 Mod 的名称与标签。

    标签以气泡（chip）展示，右上角 × 可删除；下方为树状分类（mod 分类），
    勾选即在上方的气泡区新增对应气泡，取消勾选则移除气泡，二者双向联动。
    新增标签归入“其他”分类。
    """

    mod_info_changed = pyqtSignal(str, str, list, list)  # (mod_id, custom_title, manual_tags, excluded_auto_tags)

    def __init__(self, mod: Mod, available_tags: list[str], parent=None):
        super().__init__(parent)
        self.mod = mod
        self._available = list(available_tags)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.resize(ui(540), ui(560))
        self.setMinimumSize(ui(440), ui(460))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = DragHeader(self)
        header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(ui(18), ui(10), ui(12), ui(10))
        header_title = QLabel("编辑 Mod 信息")
        header_title.setObjectName("dialogTitle")
        header_layout.addWidget(header_title)
        header_layout.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setToolTip("关闭")
        close.clicked.connect(self.reject)
        header_layout.addWidget(close)
        root.addWidget(header)

        content = QWidget()
        content.setObjectName("aboutContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(ui(18), ui(14), ui(18), ui(14))
        content_layout.setSpacing(ui(10))

        name_label = QLabel("卡片名称")
        name_label.setObjectName("aboutDescription")
        content_layout.addWidget(name_label)
        self._name_edit = QLineEdit(mod.custom_title or mod.title or mod.file_name)
        self._name_edit.setObjectName("promptInput")
        self._name_edit.setPlaceholderText("留空使用原始名称")
        content_layout.addWidget(self._name_edit)

        tags_label = QLabel("当前标签（点击 × 移除）")
        tags_label.setObjectName("aboutDescription")
        content_layout.addWidget(tags_label)

        # 气泡区：流式布局，自动换行。
        self._chips_container = QWidget()
        self._chips_container.setObjectName("editChips")
        self._chips_layout = FlowLayout(self._chips_container, margin=0)
        content_layout.addWidget(self._chips_container)

        tree_label = QLabel("选择分类（勾选即加入上方标签）")
        tree_label.setObjectName("aboutDescription")
        content_layout.addWidget(tree_label)

        self._tag_tree = QTreeWidget()
        self._tag_tree.setHeaderHidden(True)
        self._tag_tree.setUniformRowHeights(True)
        self._tag_tree.itemChanged.connect(self._on_item_changed)
        content_layout.addWidget(self._tag_tree, 1)

        add_row = QHBoxLayout()
        add_row.setSpacing(ui(8))
        self._new_tag_edit = QLineEdit()
        self._new_tag_edit.setObjectName("promptInput")
        self._new_tag_edit.setPlaceholderText("输入新标签名称，回车添加（归入“其他”分类）")
        self._new_tag_edit.returnPressed.connect(self._add_new_tag)
        add_row.addWidget(self._new_tag_edit, 1)
        add_btn = QPushButton("添加标签")
        add_btn.setObjectName("promptSecondaryButton")
        add_btn.setFixedWidth(ui(96))
        add_btn.clicked.connect(self._add_new_tag)
        add_row.addWidget(add_btn)
        ok = QPushButton("确认")
        ok.setObjectName("promptPrimaryButton")
        ok.setFixedWidth(ui(96))
        ok.clicked.connect(self.accept)
        add_row.addWidget(ok)
        content_layout.addLayout(add_row)

        root.addWidget(content, 1)

        self._rebuild_tags()
        self._sync_chips()

    def _rebuild_tags(self) -> None:
        """树状呈现中英双语分类，并完整恢复当前生效标签。"""
        effective_list = effective_tags(self.mod)
        effective = set(effective_list)
        represented: set[str] = set()
        self._tag_tree.blockSignals(True)
        self._tag_tree.clear()
        for cid, label, depth in iter_category_tree(CATEGORIES):
            if depth == 0:
                group = QTreeWidgetItem(self._tag_tree, [label])
                # 一级分类本身也是有效的自动标签（例如 survivors、infected），
                # 不能只当作视觉分组，否则已有一级标签无法恢复到气泡区。
                group.setFlags(group.flags() | Qt.ItemIsUserCheckable)
                group.setData(0, Qt.UserRole, cid)
                group.setCheckState(0, Qt.Checked if cid in effective else Qt.Unchecked)
                group.setExpanded(True)
                represented.add(cid)
                continue
            item = QTreeWidgetItem(group, [label])
            item.setData(0, Qt.UserRole, cid)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if cid in effective else Qt.Unchecked)
            represented.add(cid)
        other_group = QTreeWidgetItem(self._tag_tree, ["其他 Other"])
        other_group.setFlags(other_group.flags() & ~Qt.ItemIsSelectable)
        other_group.setExpanded(True)
        self._other_group = other_group
        # 任何已有但不属于静态分类树的标签都必须先进入“其他”，否则
        # 它们没有对应的 tree item，也就无法生成上方的当前标签气泡。
        for tag in effective_list:
            if tag in represented:
                continue
            item = QTreeWidgetItem(other_group, [tag])
            item.setData(0, Qt.UserRole, tag)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
        self._tag_tree.blockSignals(False)

    def _iter_checkable(self):
        for i in range(self._tag_tree.topLevelItemCount()):
            group = self._tag_tree.topLevelItem(i)
            if group.flags() & Qt.ItemIsUserCheckable:
                yield group
            for j in range(group.childCount()):
                yield group.child(j)

    def _on_item_changed(self, _item, _column) -> None:
        # 勾选状态变化 → 重建气泡区，实现联动。
        self._sync_chips()

    def _sync_chips(self) -> None:
        """根据当前勾选状态更新气泡区可见性（chip 仅在首次创建，不删除重建）。"""
        if not hasattr(self, "_chip_map"):
            self._chip_map: dict = {}
            self._empty_label = None
            for it in self._iter_checkable():
                chip = self._make_chip(it.text(0), it)
                self._chips_layout.addWidget(chip)
                self._chip_map[id(it)] = (it, chip)
        any_visible = False
        for it, chip in self._chip_map.values():
            visible = it.checkState(0) == Qt.Checked
            chip.setVisible(visible)
            any_visible = any_visible or visible
        if not any_visible:
            if self._empty_label is None:
                self._empty_label = QLabel("（暂无标签）")
                self._empty_label.setObjectName("editChipEmpty")
                self._chips_layout.addWidget(self._empty_label)
            self._empty_label.setVisible(True)
        elif self._empty_label is not None:
            self._empty_label.setVisible(False)

    def _make_chip(self, text: str, tree_item) -> QWidget:
        cid = tree_item.data(0, Qt.UserRole) or text
        chip = QWidget()
        chip.setObjectName("editChip")
        chip.setProperty("cid", cid)
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(ui(6), ui(2), ui(3), ui(2))
        layout.setSpacing(ui(2))
        label = QLabel(text)
        label.setObjectName("editChipText")
        layout.addWidget(label)
        x = QPushButton("×")
        x.setObjectName("editChipClose")
        x.setFixedSize(ui(16), ui(16))
        x.clicked.connect(lambda _checked=False, item=tree_item: self._remove_chip(item))
        layout.addWidget(x)
        self._style_chip(chip, cid)
        return chip

    def _style_chip(self, chip: QWidget, cid: str) -> None:
        bg = chip_color(cid, ACTIVE_THEME)
        fg = chip_text_color(ACTIVE_THEME)
        chip.setStyleSheet(
            f"#editChip {{ background: {bg}; border: none; border-radius: 11px; min-height: 22px; max-height: 22px; }}"
            f" #editChipText {{ color: {fg}; font-size: 11px; font-weight: 700; }}"
        )

    def _remove_chip(self, tree_item) -> None:
        self._tag_tree.blockSignals(True)
        tree_item.setCheckState(0, Qt.Unchecked)
        self._tag_tree.blockSignals(False)
        self._sync_chips()

    def _add_new_tag(self) -> None:
        text = self._new_tag_edit.text().strip()
        if not text:
            return
        for it in self._iter_checkable():
            if it.text(0) == text or it.data(0, Qt.UserRole) == text:
                self._tag_tree.blockSignals(True)
                it.setCheckState(0, Qt.Checked)
                self._tag_tree.blockSignals(False)
                self._new_tag_edit.clear()
                self._sync_chips()
                return
        item = QTreeWidgetItem(self._other_group, [text])
        item.setData(0, Qt.UserRole, text)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        if not hasattr(self, "_chip_map"):
            self._chip_map = {}
            self._empty_label = None
        chip = self._make_chip(text, item)
        self._chips_layout.addWidget(chip)
        self._chip_map[id(item)] = (item, chip)
        self._new_tag_edit.clear()
        self._sync_chips()

    def accept(self) -> None:
        auto = set(self.mod.categories)
        checked: set[str] = set()
        for it in self._iter_checkable():
            if it.checkState(0) == Qt.Checked:
                checked.add(it.data(0, Qt.UserRole))
        name = self._name_edit.text().strip()
        original = self.mod.title or self.mod.file_name
        if not name or name == original:
            name = ""
        manual_tags = sorted(checked - auto)
        excluded_auto_tags = sorted(auto - checked)
        self.mod_info_changed.emit(self.mod.id, name, manual_tags, excluded_auto_tags)
        super().accept()

