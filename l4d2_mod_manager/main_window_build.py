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
from . import theme
from .components import *


def _build_ui(self) -> None:
    central = BackgroundSurface(BACKGROUND_IMAGE)
    central.setObjectName("appSurface")
    root = QVBoxLayout(central)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    root.addWidget(self._build_header())

    body = QSplitter(Qt.Horizontal)
    body.setHandleWidth(1)
    sidebar = QWidget()
    sidebar.setObjectName("sidebar")
    sidebar.setMinimumWidth(ui(230))
    side_layout = QVBoxLayout(sidebar)
    side_layout.setContentsMargins(ui(16), ui(18), ui(12), ui(16))
    side_layout.setSpacing(ui(10))
    caption_row = QHBoxLayout()
    caption_row.setContentsMargins(0, 0, 0, 0)
    caption = QLabel("MOD 分类")
    caption.setObjectName("sectionLabel")
    caption_row.addWidget(caption)
    caption_row.addStretch(1)
    simple_label = QLabel("Steam 分类")
    simple_label.setObjectName("categorySwitchLabel")
    caption_row.addWidget(simple_label)
    self.category_mode_switch = ToggleSwitch()
    self.category_mode_switch.setObjectName("categoryModeSwitch")
    self.category_mode_switch.setChecked(False)
    self.category_mode_switch.toggled.connect(self.on_category_mode_switch_changed)
    caption_row.addWidget(self.category_mode_switch)
    side_layout.addLayout(caption_row)
    self.category_tree = QTreeWidget()
    self.category_tree.setObjectName("categoryTree")
    self.category_tree.setHeaderHidden(True)
    self.category_tree.setIndentation(ui(22))
    self.category_tree.setUniformRowHeights(True)
    self.category_tree.itemSelectionChanged.connect(self.on_category_selected)
    self.category_tree.itemSelectionChanged.connect(self._refresh_tree_foregrounds)
    side_layout.addWidget(self.category_tree, 1)
    body.addWidget(sidebar)

    content = QWidget()
    content_layout = QVBoxLayout(content)
    # Keep only a slim outer gutter; the toolbar uses the scrollbar width
    # as an inset so its controls align with the card viewport, not the bar.
    content_layout.setContentsMargins(ui(16), ui(6), ui(8), ui(18))
    content_layout.setSpacing(ui(0))
    self.content_bar = self._build_content_bar()
    self.content_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    content_layout.addWidget(self.content_bar)
    content_layout.addSpacing(ui(8))
    self.scroll = QScrollArea()
    self.scroll.setObjectName("cardsScroll")
    self.scroll.setWidgetResizable(True)
    self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    self.scroll.viewport().setObjectName("cardsViewport")
    # The overlay must live inside the scroll viewport (sibling of the
    # cards host), not directly under the QScrollArea: QScrollArea keeps
    # its viewport above every other child, so a scroll-area child would
    # always be painted beneath the card grid.
    self._cards_loading_overlay = QFrame(self.scroll.viewport())
    self._cards_loading_overlay.setObjectName("cardsLoadingOverlay")
    loading_layout = QVBoxLayout(self._cards_loading_overlay)
    loading_layout.setContentsMargins(0, 0, 0, 0)
    loading_layout.setSpacing(ui(6))
    self._cards_loading_spinner = QLabel("◐")
    self._cards_loading_spinner.setObjectName("cardsLoadingSpinner")
    self._cards_loading_spinner.setAlignment(Qt.AlignCenter)
    self._cards_loading_label = QLabel("正在加载 Mod…")
    self._cards_loading_label.setObjectName("cardsLoadingLabel")
    self._cards_loading_label.setAlignment(Qt.AlignCenter)
    loading_panel = QFrame()
    loading_panel.setObjectName("cardsLoadingPanel")
    panel_layout = QVBoxLayout(loading_panel)
    panel_layout.setContentsMargins(ui(24), ui(18), ui(24), ui(18))
    panel_layout.setSpacing(ui(6))
    panel_layout.addWidget(self._cards_loading_spinner)
    panel_layout.addWidget(self._cards_loading_label)
    loading_layout.addStretch(1)
    loading_layout.addWidget(loading_panel, 0, Qt.AlignHCenter)
    loading_layout.addStretch(1)
    self._cards_loading_frames = ("◐", "◓", "◑", "◒")
    self._cards_loading_frame = 0
    self._cards_loading_timer = QTimer(self)
    self._cards_loading_timer.timeout.connect(self._advance_cards_loading_spinner)
    self._cards_loading_overlay.hide()
    self.cards_host = QWidget()
    self.cards_host.setObjectName("cardsHost")
    self.cards_layout = QGridLayout(self.cards_host)
    self.cards_layout.setContentsMargins(0, 0, 0, 0)
    self.cards_layout.setHorizontalSpacing(ui(11))
    self.cards_layout.setVerticalSpacing(ui(12))
    self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    self.scroll.setWidget(self.cards_host)
    content_layout.addWidget(self.scroll, 1)
    self.pagination_bar = QWidget()
    self.pagination_bar.setObjectName("paginationBar")
    pagination_layout = QHBoxLayout(self.pagination_bar)
    pagination_layout.setContentsMargins(0, 0, ui(8), 0)
    pagination_layout.setSpacing(ui(8))
    self.previous_page_button = QPushButton("上一页")
    self.previous_page_button.setObjectName("paginationButton")
    self.previous_page_button.setFixedHeight(ui(20))
    self.previous_page_button.clicked.connect(lambda: self.change_page(-1))
    self.page_label = QLabel()
    self.page_label.setObjectName("pageLabel")
    self.page_label.setAlignment(Qt.AlignCenter)
    self.next_page_button = QPushButton("下一页")
    self.next_page_button.setObjectName("paginationButton")
    self.next_page_button.setFixedHeight(ui(20))
    self.next_page_button.clicked.connect(lambda: self.change_page(1))
    pagination_layout.addWidget(self.previous_page_button)
    pagination_layout.addWidget(self.page_label)
    pagination_layout.addWidget(self.next_page_button)
    pagination_layout.addStretch(1)
    size_label = QLabel("卡片大小")
    size_label.setObjectName("pageLabel")
    size_label.setToolTip("调整卡片大小；卡片会保持宽高比例")
    # 默认卡片宽度：默认窗口（1250x730）下每排显示 5 张卡片。
    self._card_size = ui(168)
    self.card_size_decrease = QPushButton("-")
    self.card_size_decrease.setObjectName("paginationButton")
    self.card_size_decrease.setFixedSize(ui(26), ui(22))
    self.card_size_decrease.setToolTip("缩小卡片")
    self.card_size_decrease.clicked.connect(lambda: self._change_card_size(-ui(10)))
    self.card_size_increase = QPushButton("+")
    self.card_size_increase.setObjectName("paginationButton")
    self.card_size_increase.setFixedSize(ui(26), ui(22))
    self.card_size_increase.setToolTip("放大卡片")
    self.card_size_increase.clicked.connect(lambda: self._change_card_size(ui(10)))
    pagination_layout.addWidget(size_label)
    pagination_layout.addWidget(self.card_size_decrease)
    pagination_layout.addWidget(self.card_size_increase)
    self.pagination_spacer = QWidget()
    self.pagination_spacer.setFixedHeight(ui(10))
    content_layout.addWidget(self.pagination_spacer)
    content_layout.addWidget(self.pagination_bar)
    body.addWidget(content)
    body.setSizes([ui(275), ui(1045)])
    body.setStretchFactor(0, 0)
    body.setStretchFactor(1, 1)
    body.setCollapsible(0, False)
    body.setCollapsible(1, False)
    root.addWidget(body, 1)
    root.addWidget(self._build_footer())
    self.setCentralWidget(central)
    self._size_grip = QSizeGrip(central)
    self._size_grip.setFixedSize(ui(18), ui(18))
    self._size_grip.move(central.width() - self._size_grip.width(), central.height() - self._size_grip.height())
    self._size_grip.raise_()


def _build_header(self) -> QWidget:
    header = DragHeader(self)
    header.setObjectName("header")
    header.setFixedHeight(ui(56))
    layout = QHBoxLayout(header)
    layout.setContentsMargins(ui(24), ui(4), ui(8), ui(4))
    layout.setSpacing(ui(11))
    brand = QVBoxLayout()
    brand.setSpacing(0)
    name_row = QHBoxLayout()
    name_row.setSpacing(ui(8))
    brand_icon = QLabel()
    brand_icon.setObjectName("brandIcon")
    brand_icon_size = ui(38)
    brand_icon.setFixedSize(brand_icon_size, brand_icon_size)
    brand_icon.setAlignment(Qt.AlignCenter)
    title_pixmap = QPixmap(str(TITLE_IMAGE))
    if title_pixmap.isNull():
        brand_icon.hide()
    else:
        # Render at device resolution first so the detailed logo stays sharp
        # on high-DPI screens while retaining a 40px logical display size.
        device_ratio = max(1.0, brand_icon.devicePixelRatioF())
        rendered_size = round(ui(36) * device_ratio)
        title_pixmap = title_pixmap.scaled(
            rendered_size, rendered_size, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        title_pixmap.setDevicePixelRatio(device_ratio)
        brand_icon.setPixmap(title_pixmap)
    name = QPushButton("L4D2  BOSS")
    name.setObjectName("brandButton")
    name.setToolTip("查看软件信息")
    name.clicked.connect(self.show_about)
    credit = QLabel("@ by Mr.Chen")
    credit.setObjectName("brandCredit")
    credit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    credit.setContentsMargins(0, ui(3), 0, 0)
    name_row.addWidget(brand_icon, 0, Qt.AlignVCenter)
    name_row.addWidget(name, 0, Qt.AlignVCenter)
    name_row.addWidget(credit)
    name_row.addStretch(1)
    sub = QLabel("MOD LOADOUT MANAGER")
    sub.setObjectName("brandSub")
    brand.addLayout(name_row)
    brand.addWidget(sub)
    layout.addLayout(brand)
    layout.addStretch(1)
    self.header_hint = QLabel()
    self.header_hint.setObjectName("headerHint")
    self.header_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    self.header_hint.setMinimumWidth(ui(250))
    self.header_hint.hide()
    layout.addWidget(self.header_hint)
    self.choose_button = self._header_button(
        QStyle.SP_FileDialogNewFolder, "选择游戏", self.choose_directory,
        secondary=True, tooltip="选择游戏：定位 left4dead2.exe 并扫描 addons 文件夹",
    )
    self.refresh_button = self._header_button(
        QStyle.SP_BrowserReload, "扫描 Mod", lambda: self.scan_mods(False),
        tooltip="扫描 Mod：重新扫描本地 addons 文件夹",
    )
    self.fetch_button = self._header_button(
        QStyle.SP_ArrowDown, "同步 Steam", self.fetch_steam_info,
        tooltip="同步 Steam：获取创意工坊 Mod 的名称、订阅数和标签",
    )
    layout.addWidget(self.choose_button)
    layout.addWidget(self.refresh_button)
    layout.addWidget(self.fetch_button)
    for button in (self.choose_button, self.refresh_button, self.fetch_button):
        button.installEventFilter(self)
    self.toggle_all_button = self._header_button(QStyle.SP_DialogApplyButton, "全部启动", self.toggle_all_mods)
    # 虽然创建于头部，但最终被加入底部操作栏；用独立 objectName
    # 以便钛色灰主题将底部四个按钮统一样式为“幽灵绿”风格。
    self.toggle_all_button.setObjectName("toggleAllButton")
    layout.addWidget(self.toggle_all_button)
    self.theme_button = self._header_button(
        QStyle.SP_DesktopIcon, "", self._open_theme_menu,
    )
    self._update_theme_button()
    self._header_action_buttons = (
        self.choose_button, self.refresh_button, self.fetch_button, self.theme_button,
    )
    header_action_width = max(button.sizeHint().width() for button in self._header_action_buttons) + ui(4)
    for button in self._header_action_buttons:
        button.setFixedWidth(header_action_width)
    layout.addWidget(self.theme_button)
    close = QPushButton("×")
    close.setObjectName("closeButton")
    close.setText("×")
    self.minimize_button = self._window_control_button("−", None, self.showMinimized)
    self.maximize_button = self._window_control_button("□", None, self.toggle_maximized)
    close.setFixedSize(ui(30), ui(30))
    close.clicked.connect(self.close)
    window_controls = QHBoxLayout()
    window_controls.setContentsMargins(0, 0, 0, 0)
    window_controls.setSpacing(ui(4))
    window_controls.addWidget(self.minimize_button)
    window_controls.addWidget(self.maximize_button)
    window_controls.addWidget(close)
    layout.addLayout(window_controls)
    self.close_button = close
    for button in (self.theme_button, self.minimize_button, self.maximize_button, self.close_button):
        button.installEventFilter(self)
    return header


def _header_button(
    self, icon, text, handler, secondary: bool = False, icon_only: bool = False, tooltip: str = "",
) -> QPushButton:
    button = QPushButton(self.style().standardIcon(icon), text)
    if icon_only:
        button.setText("")
        button.setObjectName("headerIconButton")
        button.setFixedSize(ui(40), ui(36))
        button.setIconSize(QSize(ui(20), ui(20)))
    else:
        button.setObjectName("headerButtonSecondary" if secondary else "headerButton")
    if tooltip:
        button.setToolTip(tooltip)
    button.clicked.connect(handler)
    return button

@staticmethod

@staticmethod
def _window_control_button(symbol: str, tooltip: str, handler) -> QPushButton:
    button = QPushButton(symbol)
    button.setObjectName("windowControlButton")
    button.setFixedSize(ui(30), ui(30))
    if tooltip:
        button.setToolTip(tooltip)
    button.clicked.connect(handler)
    return button


def toggle_maximized(self) -> None:
    if self.isMaximized():
        self.showNormal()
        self.maximize_button.setText("❐")
    else:
        self.showMaximized()
        self.maximize_button.setText("❐")

    self._suppress_content_alignment = False
    # Re-align controls only for the explicit window-state transition.
    self._schedule_window_state_alignment()


def restore_default_window(self) -> None:
    """Restore the main window to its normal startup dimensions."""
    self.showNormal()
    self.resize(ui(1250), ui(730))
    self._schedule_window_state_alignment()


def _show_header_hint(self, text: str) -> None:
    self.header_hint.setText(text)
    self.header_hint.setVisible(True)


def _clear_header_hint(self) -> None:
    self.header_hint.setVisible(False)
    self.header_hint.clear()


def _update_theme_button(self) -> None:
    label = THEME_LABELS.get(self._theme, self._theme)
    self.theme_button.setIcon(self._theme_icon())
    self.theme_button.setText(f" {label} ▾")


def _theme_icon(self) -> QIcon:
    """Create a compact light/dark disc for the theme selector."""
    size = ui(22)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#39c6dc"))
    painter.drawEllipse(ui(2), ui(2), size - ui(4), size - ui(4))
    painter.setBrush(QColor("#f5f8fd"))
    painter.drawEllipse(ui(2), ui(2), (size - ui(4)) // 2, size - ui(4))
    painter.setBrush(QColor("#18344e"))
    painter.drawEllipse(ui(7), ui(7), ui(4), ui(4))
    painter.end()
    return QIcon(pixmap)


def _open_theme_menu(self) -> None:
    from PyQt5.QtWidgets import QMenu

    menu = QMenu(self)
    menu.setObjectName("themeMenu")
    # 下拉菜单宽度与主题按钮一致。
    menu.setMinimumWidth(max(ui(1), self.theme_button.width()))
    for theme_key in THEME_ORDER:
        action = menu.addAction(THEME_LABELS.get(theme_key, theme_key))
        action.setCheckable(True)
        action.setChecked(theme_key == self._theme)
        hint = THEME_HINTS.get(theme_key, "")
        action.hovered.connect(lambda h=hint: self._show_header_hint(h))
        action.triggered.connect(lambda _checked=False, key=theme_key: self._set_theme(key))
    menu.aboutToHide.connect(self._clear_header_hint)
    menu.exec_(self.theme_button.mapToGlobal(self.theme_button.rect().bottomLeft()))


def _set_theme(self, theme_key: str) -> None:
    if theme_key == self._theme:
        return
    self._theme = theme_key
    self.settings["theme"] = theme_key
    self.storage.save_settings(self.settings)
    self._apply_style()
    self._update_theme_button()
    self.refresh_tree()
    for card in self._card_widgets.values():
        card.refresh_state()

@staticmethod

@staticmethod
def _launch_icon() -> QIcon:
    """An original survivor-inspired mark for the launch action."""
    size = ui(48)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor("#ff8b8b"))
    painter.setBrush(QColor("#b3242f"))
    painter.drawEllipse(ui(2), ui(2), size - ui(4), size - ui(4))
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Arial", max(7, ui(12)), QFont.Black))
    painter.drawText(pixmap.rect().adjusted(0, -ui(5), 0, 0), Qt.AlignCenter, "L4D")
    painter.setFont(QFont("Arial", max(7, ui(15)), QFont.Black))
    painter.drawText(pixmap.rect().adjusted(0, ui(10), 0, 0), Qt.AlignCenter, "2")
    painter.end()
    return QIcon(pixmap)


def _build_content_bar(self) -> QWidget:
    bar = QWidget()
    layout = QHBoxLayout(bar)
    # The vertical scrollbar is 8px wide.  Match that inset so the
    # collection picker ends on the same line as the last card.
    layout.setContentsMargins(0, 0, ui(8), 0)
    self.content_back_button = QPushButton()
    self.content_back_button.setObjectName("contentBackButton")
    self.content_back_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
    self.content_back_button.setIconSize(QSize(ui(18), ui(18)))
    self.content_back_button.setFixedSize(ui(30), ui(28))
    self.content_back_button.setToolTip("返回 Mod 列表")
    self.content_back_button.clicked.connect(self.show_mod_list)
    self.content_back_button.hide()
    layout.addWidget(self.content_back_button)
    self.content_title_host = QWidget()
    self.content_title_host.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    title_box = QVBoxLayout(self.content_title_host)
    title_box.setContentsMargins(0, 0, 0, 0)
    title_box.setSpacing(1)
    self.content_title = QLabel("全部 Mod")
    self.content_title.setObjectName("contentTitle")
    self.content_subtitle = QLabel()
    self.content_subtitle.setObjectName("contentSubtitle")
    title_box.addWidget(self.content_title)
    title_box.addWidget(self.content_subtitle)
    layout.addWidget(self.content_title_host)
    layout.addStretch(1)
    self.search_input = QLineEdit()
    self.search_input.setObjectName("searchInput")
    self.search_input.setPlaceholderText("搜索名称、作者或 Workshop ID…")
    self.search_input.setClearButtonEnabled(True)
    self.search_input.textChanged.connect(self.on_search_changed)
    self.collection_combo = MultiSelectComboBox()
    self.collection_combo.setObjectName("collectionCombo")
    self.collection_combo.setFixedWidth(ui(214))
    self.collection_combo.setMaxVisibleItems(7)
    self.collection_combo.view().setObjectName("collectionComboMenu")
    self.collection_combo.view().setTextElideMode(Qt.ElideRight)
    self.collection_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    self.collection_combo.selection_changed.connect(self.on_collection_selection_changed)
    self.collection_combo.collection_delete_requested.connect(self.delete_collection)
    self.collection_combo.collection_rename_requested.connect(self.rename_collection)
    self._filter_controls = QHBoxLayout()
    self._filter_controls.setContentsMargins(0, 0, 0, 0)
    self._filter_controls.setSpacing(ui(11))
    self._filter_controls.addWidget(self.search_input)
    self._filter_controls.addWidget(self.collection_combo)
    layout.addLayout(self._filter_controls)
    return bar


def _build_footer_legacy(self) -> QWidget:
    footer = QFrame()
    footer.setObjectName("footer")
    layout = QHBoxLayout(footer)
    layout.setContentsMargins(ui(24), ui(10), ui(24), ui(10))
    self.total_label = self._make_mod_count_button("totalModCount", self.show_all_mods)
    self.active_label = self._make_mod_count_button("activeModCount", self.show_active_mods)
    self.conflict_button = QPushButton()
    self.conflict_button.setObjectName("conflictButton")
    self.conflict_button.clicked.connect(self.show_conflicts)
    layout.addWidget(self.total_label)
    layout.addWidget(self.active_label)
    layout.addWidget(self.conflict_button)
    layout.addStretch(1)
    self.save_button = QPushButton(self.style().standardIcon(QStyle.SP_DialogSaveButton), "保 存")
    self.save_button.setObjectName("primaryButton")
    self.save_button.clicked.connect(self.save_collection)
    layout.addWidget(self.save_button)
    layout.insertWidget(layout.indexOf(self.save_button), self.toggle_all_button)
    return footer

@staticmethod

@staticmethod
def _make_mod_count_button(object_name: str, handler) -> QPushButton:
    button = QPushButton()
    button.setObjectName(object_name)
    button.setFlat(True)
    button.setFixedWidth(ui(78))
    button.setCursor(Qt.PointingHandCursor)
    button.clicked.connect(handler)
    return button


def _build_footer(self) -> QWidget:
    footer = QFrame()
    footer.setObjectName("footer")
    footer.setFixedHeight(ui(56))
    layout = QHBoxLayout(footer)
    layout.setContentsMargins(0, ui(4), 0, ui(4))
    layout.setSpacing(0)

    status_host = QWidget()
    status_host.setFixedWidth(ui(277))
    status_layout = QHBoxLayout(status_host)
    status_layout.setContentsMargins(ui(24), 0, 0, 0)
    status_layout.setSpacing(ui(8))
    self.total_label = self._make_mod_count_button("totalModCount", self.show_all_mods)
    self.active_label = self._make_mod_count_button("activeModCount", self.show_active_mods)
    self.conflict_button = QPushButton()
    self.conflict_button.setObjectName("conflictButton")
    self.conflict_button.setFixedWidth(ui(78))
    self.conflict_button.clicked.connect(self.show_conflicts)
    status_layout.addWidget(self.total_label)
    status_layout.addWidget(self.active_label)
    status_layout.addWidget(self.conflict_button)
    status_layout.addStretch(1)
    layout.addWidget(status_host)

    self.action_host = QWidget()
    self.action_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    action_host = self.action_host
    action_layout = QHBoxLayout(action_host)
    action_layout.setContentsMargins(0, 0, 0, 0)
    action_layout.setSpacing(self.cards_layout.horizontalSpacing())
    self.steam_sync_widget = QWidget()
    self.steam_sync_widget.setObjectName("steamSyncStatus")
    sync_layout = QHBoxLayout(self.steam_sync_widget)
    sync_layout.setContentsMargins(ui(10), 0, ui(10), 0)
    sync_layout.setSpacing(ui(8))
    sync_label = QLabel("正在同步 Steam 数据…")
    sync_label.setObjectName("steamSyncLabel")
    sync_layout.addWidget(sync_label)
    self.steam_sync_progress = QProgressBar()
    self.steam_sync_progress.setObjectName("steamSyncProgress")
    self.steam_sync_progress.setRange(0, 1)
    self.steam_sync_progress.setTextVisible(False)
    self.steam_sync_progress.setFixedWidth(ui(92))
    sync_layout.addWidget(self.steam_sync_progress)
    self.steam_sync_widget.hide()
    action_layout.addWidget(self.steam_sync_widget)
    action_layout.addStretch(1)
    action_layout.addWidget(self.toggle_all_button)
    self.save_button = QPushButton(self.style().standardIcon(QStyle.SP_DialogSaveButton), "保 存")
    self.save_button.setObjectName("primaryButton")
    self.save_button.clicked.connect(self.save_collection)
    action_layout.addWidget(self.save_button)
    self.save_as_button = QPushButton(self.style().standardIcon(QStyle.SP_FileDialogDetailedView), "另存为")
    self.save_as_button.setObjectName("secondaryButton")
    self.save_as_button.clicked.connect(self.save_collection_as_new)
    action_layout.addWidget(self.save_as_button)
    self.launch_button = QPushButton("  启动游戏")
    self.launch_button.setObjectName("launchButton")
    self.launch_button.setIcon(self._launch_icon())
    self.launch_button.setIconSize(QSize(ui(20), ui(20)))
    self.launch_button.clicked.connect(self.launch_game)
    action_layout.addWidget(self.launch_button)
    self._footer_action_buttons = (
        self.toggle_all_button, self.save_button, self.save_as_button, self.launch_button,
    )
    action_buttons = self._header_action_buttons + self._footer_action_buttons
    action_width = max(button.minimumSizeHint().width() for button in action_buttons)
    action_height = max(ui(1), max(button.minimumSizeHint().height() for button in action_buttons) - ui(2))
    for button in self._header_action_buttons:
        button.setFixedSize(action_width, action_height)
    for button in self._footer_action_buttons:
        button.setFixedSize(action_width, action_height)
    layout.addWidget(action_host, 1)
    return footer


def _apply_style(self) -> None:
    # Mutate the canonical ACTIVE_THEME in the theme module so that
    # theme_color() (which reads theme.ACTIVE_THEME) reflects the switch.
    # A plain ``global`` here would only rebind this module's star-imported
    # copy and leave painted widgets stuck on the previous theme.
    theme.ACTIVE_THEME = self._theme
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(THEMES.get(self._theme, THEMES["dark"]))
    self.setStyleSheet("")
    # Re-evaluate button minimum sizes after the stylesheet has applied;
    # padding and font metrics are part of the real required width.
    QTimer.singleShot(0, self._sync_content_right_edges)

