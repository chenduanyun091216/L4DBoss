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
    # 底部左侧控件（上一页/页码/下一页/卡片大小/-+）固定宽度、紧密排列：
    # 位置不受右侧悬停提示影响，提示只占用它与右侧按钮之间的弹性空间。
    self.pagination_bar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    pagination_layout = QHBoxLayout(self.pagination_bar)
    pagination_layout.setContentsMargins(0, 0, ui(8), 0)
    pagination_layout.setSpacing(ui(2))
    self.previous_page_button = QPushButton("上一页")
    self.previous_page_button.setObjectName("paginationButton")
    self.previous_page_button.setFixedHeight(ui(20))
    self.previous_page_button.clicked.connect(lambda: self.change_page(-1))
    self.page_label = QLabel()
    self.page_label.setObjectName("pageLabel")
    self.page_label.setAlignment(Qt.AlignCenter)
    # 去掉 #footer QLabel 规则带来的右侧 12px 内边距，让“下一页”
    # 按钮紧贴“第 1 / 5 页”文字，而不是隔着一段空白。
    self.page_label.setStyleSheet("padding-right: 0px;")
    self.next_page_button = QPushButton("下一页")
    self.next_page_button.setObjectName("paginationButton")
    self.next_page_button.setFixedHeight(ui(20))
    self.next_page_button.clicked.connect(lambda: self.change_page(1))
    pagination_layout.addWidget(self.previous_page_button)
    pagination_layout.addWidget(self.page_label)
    pagination_layout.addWidget(self.next_page_button)
    size_label = QLabel("卡片大小")
    size_label.setObjectName("pageLabel")
    size_label.setToolTip("调整卡片大小；卡片会保持宽高比例")
    # 去掉右侧 12px 内边距使其紧贴“-”按钮，并加少量左侧内边距
    # 让“卡片大小”文字相对“下一页”向右移一点。
    size_label.setStyleSheet("padding-right: 0px; padding-left: 4px;")
    # 默认卡片宽度：默认窗口（1250x730）下每排显示 5 张卡片。
    self._card_size = ui(168)
    self.card_size_decrease = QPushButton("-")
    self.card_size_decrease.setObjectName("paginationButton")
    self.card_size_decrease.setFixedSize(ui(22), ui(20))
    self.card_size_decrease.setToolTip("缩小卡片")
    self.card_size_decrease.clicked.connect(lambda: self._change_card_size(-ui(10)))
    self.card_size_increase = QPushButton("+")
    self.card_size_increase.setObjectName("paginationButton")
    self.card_size_increase.setFixedSize(ui(22), ui(20))
    self.card_size_increase.setToolTip("放大卡片")
    self.card_size_increase.clicked.connect(lambda: self._change_card_size(ui(10)))
    pagination_layout.addWidget(size_label)
    pagination_layout.addWidget(self.card_size_decrease)
    pagination_layout.addWidget(self.card_size_increase)
    # 分页与卡片大小控件不再占用内容区：整个上方区域留给卡片展示，
    # 这些控件移入底部操作栏，与右侧四个操作按钮处于同一水平行。
    body.addWidget(content)
    body.setSizes([ui(275), ui(1045)])
    body.setStretchFactor(0, 0)
    body.setStretchFactor(1, 1)
    body.setCollapsible(0, False)
    body.setCollapsible(1, False)
    root.addWidget(body, 1)
    root.addWidget(self._build_footer())
    self.setCentralWidget(central)
    # 悬停提示：独立置顶小窗（Qt.Tool + WindowStaysOnTopHint），永远浮在
    # 主窗口之上，主窗口内任何元素都无法遮盖它；不占布局、不抢焦点、
    # 鼠标事件穿透，其余控件位置完全不受影响。
    self.hover_overlay = HintOverlay(self)
    self._hover_anchor: QWidget | None = None
    self._hover_text = ""
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
    # 主布局间距收窄为 1px：头部 4 个按钮放进独立子布局（内部仍为 11px），
    # 使“主题”按钮与右上角窗口控制按钮的间隙由 11px 收窄为 1px，
    # 整个按钮组因此整体右移约 10px。
    layout.setSpacing(ui(1))
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
    # 标题右键菜单：查看 Mod 目录 / 使用手册 / 关于应用。
    name.setContextMenuPolicy(Qt.CustomContextMenu)
    name.customContextMenuRequested.connect(lambda pos: self._show_title_menu(name, pos))
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
    # 底部进度条（选择游戏/扫描 Mod/同步 Steam/恢复组合共用）位于头部
    # “选择游戏”按钮左侧的空白区域：紧邻触发它的按钮，空闲时隐藏不占空间，
    # 显示期间与右侧按钮同一水平行、互不遮盖。
    self.steam_sync_widget = QWidget()
    self.steam_sync_widget.setObjectName("steamSyncStatus")
    self.steam_sync_widget.setFixedHeight(ui(30))
    sync_layout = QHBoxLayout(self.steam_sync_widget)
    sync_layout.setContentsMargins(ui(10), 0, ui(10), 0)
    sync_layout.setSpacing(ui(8))
    # 单行提示：不换行，文字较多时展示框随文字拉长（上限 ui(360)），
    # 框内文字上下居中；仅当窗口过窄时才以 … 省略，避免挤出右侧按钮。
    sync_label = SingleLineElidedLabel("正在同步 Steam 数据…", max_width=ui(360))
    sync_label.setObjectName("steamSyncLabel")
    sync_layout.addWidget(sync_label)
    self.steam_sync_progress = QProgressBar()
    self.steam_sync_progress.setObjectName("steamSyncProgress")
    self.steam_sync_progress.setRange(0, 1)
    self.steam_sync_progress.setTextVisible(False)
    self.steam_sync_progress.setFixedWidth(ui(92))
    sync_layout.addWidget(self.steam_sync_progress)
    self.steam_sync_widget.hide()
    layout.addWidget(self.steam_sync_widget)
    # 同步进度条与按钮组之间保持 11px 间距（1px 主间距 + 10px 固定间距）。
    layout.addSpacing(ui(10))
    # 头部四个按钮（选择游戏/扫描 Mod/同步 Steam/主题）不再使用悬浮弹框
    # 提示，悬停说明统一以置顶覆盖层显示在按钮左侧的空白区域。
    self.choose_button = self._header_button(
        QStyle.SP_FileDialogNewFolder, "选择游戏", self.choose_directory,
        secondary=True,
    )
    self.refresh_button = self._header_button(
        QStyle.SP_BrowserReload, "扫描 Mod", lambda: self.scan_mods(False),
    )
    self.fetch_button = self._header_button(
        QStyle.SP_ArrowDown, "同步 Steam", self.fetch_steam_info,
    )
    # 头部四个按钮放入独立子布局：按钮之间仍保持 11px 间距。
    self.header_action_layout = QHBoxLayout()
    self.header_action_layout.setSpacing(ui(11))
    self.header_action_layout.addWidget(self.choose_button)
    self.header_action_layout.addWidget(self.refresh_button)
    self.header_action_layout.addWidget(self.fetch_button)
    for button in (self.choose_button, self.refresh_button, self.fetch_button):
        button.installEventFilter(self)
    self.toggle_all_button = self._header_button(QStyle.SP_DialogApplyButton, "全部启动", self.toggle_all_mods)
    # 虽然创建于头部，但最终被加入底部操作栏；用独立 objectName
    # 以便钛色灰主题将底部四个按钮统一样式为“幽灵绿”风格。
    self.toggle_all_button.setObjectName("toggleAllButton")
    self.header_action_layout.addWidget(self.toggle_all_button)
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
    self.header_action_layout.addWidget(self.theme_button)
    layout.addLayout(self.header_action_layout)
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
    """Show the hover hint overlay, anchored left of the header buttons."""
    if getattr(self, "_progress_visible", False):
        # 进度条显示期间不叠加提示，避免盖住进度条。
        return
    self._show_hover_hint(text, self.choose_button)


def _clear_header_hint(self) -> None:
    self._clear_hover_hint()


def _show_footer_hint(self, text: str) -> None:
    """Show the hover hint overlay, anchored left of “全部启动”."""
    self._show_hover_hint(text, self.toggle_all_button)


def _clear_footer_hint(self) -> None:
    self._clear_hover_hint()


def _show_hover_hint(self, text: str, anchor: QWidget) -> None:
    """Show the topmost hint chip to the left of ``anchor``.

    The chip is a separate always-on-top tool window, so it is never
    covered by anything inside the app, and no other widget moves.
    """
    overlay = getattr(self, "hover_overlay", None)
    if overlay is None:
        return
    overlay.set_hint_text(text)
    overlay.show_near(anchor.mapToGlobal(QPoint(0, 0)), anchor.height())
    self._hover_anchor = anchor
    self._hover_text = text


def _clear_hover_hint(self) -> None:
    overlay = getattr(self, "hover_overlay", None)
    if overlay is None:
        return
    overlay.hide()
    self._hover_anchor = None
    self._hover_text = ""


def _show_title_menu(self, anchor: QWidget, pos) -> None:
    """Title (L4D2 BOSS) right-click menu: mod dir / manual / about."""
    menu = QMenu(self)
    menu.setObjectName("themeMenu")
    view_dir = menu.addAction("查看 Mod 目录")
    view_dir.setToolTip("打开游戏安装 Mod 文件的 addons 文件夹")
    view_dir.triggered.connect(self.open_mods_directory)
    manual = menu.addAction("使用手册")
    manual.setToolTip("在浏览器中打开 GitHub 使用说明")
    manual.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(MANUAL_URL)))
    about = menu.addAction("关于应用")
    about.setToolTip("查看关于 L4D Boss")
    about.triggered.connect(self.show_about)
    menu.exec_(anchor.mapToGlobal(pos))


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
    # 下拉菜单宽度固定为与主题按钮一致（仅设最小宽度时，菜单内容
    # 超出按钮宽度会把菜单撑宽，与按钮不一致）。
    menu.setFixedWidth(max(ui(1), self.theme_button.width()))
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
    # 树节点文字颜色随主题变化，需要重建；卡片不需要逐张 refresh_state：
    # 窗口级样式表更换时 Qt 已自动重抛光整个子树，逐张 unpolish/polish
    # 只是重复开销。
    self.refresh_tree()

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
    # 分页控件（上一页/下一页/页码）与卡片大小控件与四个操作按钮同行；
    # 底部进度条不在此处，而是位于头部“选择游戏”按钮左侧。
    action_layout.addWidget(self.pagination_bar)
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
    # 底部四个操作按钮的悬停提示以置顶覆盖层显示在“全部启动”按钮左侧。
    for button in self._footer_action_buttons:
        button.installEventFilter(self)
    # 头部按钮取各自自然宽度、高度统一；底部四个按钮统一使用“启动游戏”
    # 按钮的宽度，保持右下角按钮组等宽（高度同样统一）。
    action_height = max(ui(1), max(button.minimumSizeHint().height() for button in self._footer_action_buttons) - ui(2))
    for button in self._header_action_buttons:
        button.setFixedSize(button.minimumSizeHint().width(), action_height)
    launch_width = self.launch_button.minimumSizeHint().width()
    for button in self._footer_action_buttons:
        button.setFixedSize(launch_width, action_height)
    layout.addWidget(action_host, 1)
    return footer


def _apply_style(self) -> None:
    # Mutate the canonical ACTIVE_THEME in the theme module so that
    # theme_color() (which reads theme.ACTIVE_THEME) reflects the switch.
    # A plain ``global`` here would only rebind this module's star-imported
    # copy and leave painted widgets stuck on the previous theme.
    theme.ACTIVE_THEME = self._theme
    # 主题样式表只设置在主窗口上：对话框/菜单/提示框等以主窗口为父级，
    # 会自动继承窗口级样式表。不能再用 QApplication.setStyleSheet——
    # 它会同步重抛光应用中所有控件的全部规则，切换主题时明显卡顿；
    # 窗口级只重抛光本窗口子树。无父级的 QToolTip 由启动时设置的
    # TOOLTIP_QSS 覆盖。
    self.setStyleSheet(THEMES.get(self._theme, THEMES["dark"]))
    # Re-evaluate button minimum sizes after the stylesheet has applied;
    # padding and font metrics are part of the real required width.
    QTimer.singleShot(0, self._sync_content_right_edges)


def _set_progress_visible(self, visible: bool) -> None:
    """Show/hide the shared progress bar (scan/sync/restore).

    The bar lives in the header, immediately left of the “选择游戏” button,
    on the same row as the header action buttons, so it never covers cards,
    pagination controls or the footer actions.  Hidden widgets take no
    layout space; while visible, hover hints are suppressed so the header
    row never overflows.
    """
    self._progress_visible = bool(visible)
    if hasattr(self, "steam_sync_widget"):
        self.steam_sync_widget.setVisible(visible)
    if visible and hasattr(self, "hover_overlay"):
        # 进度条出现时立即收起悬停提示：提示是置顶小窗，会盖住进度条。
        self._clear_header_hint()

