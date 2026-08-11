from __future__ import annotations

import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from threading import Event

from PyQt5.QtCore import QEvent, QObject, QRunnable, QSize, QTimer, QUrl, Qt, QThreadPool, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QIcon, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAction, QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStyle, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .categories import CATEGORIES, infer_categories
from .models import Mod, ModCollection
from .steam_client import SteamClient
from .storage import AppStorage
from .vpk_scanner import is_conflict_relevant_path, scan_mod_directory

APP_ROOT = Path(__file__).resolve().parent.parent
UI_SCALE = 1.0
PREVIEW_CACHE: dict[str, QPixmap] = {}


def ui(value: int) -> int:
    return max(1, round(value * UI_SCALE))


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


class ModCard(QFrame):
    clicked = pyqtSignal(str)
    context_requested = pyqtSignal(str, object)

    def __init__(self, mod: Mod, collection_names: list[str] | None = None):
        super().__init__()
        self.mod = mod
        self.setObjectName(
            "modCardConflict" if mod.active and mod.conflict_with else ("modCardActive" if mod.active else "modCard")
        )
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(ui(214), ui(294))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui(12), ui(12), ui(12), ui(12))
        layout.setSpacing(ui(8))
        preview = QLabel()
        preview.setObjectName("preview")
        preview.setAlignment(Qt.AlignCenter)
        preview.setPixmap(make_preview_pixmap(mod))
        layout.addWidget(preview)

        title = TwoLineElidedLabel(mod.title or mod.file_name)
        title.setObjectName("cardTitle")
        title.setFixedHeight(ui(40))
        layout.addWidget(title)

        code = mod.workshop_id or Path(mod.file_name).stem
        meta_lines = [f"WORKSHOP  {code}"]
        stats = []
        if mod.subscriptions > 0:
            stats.append(f"订阅 {mod.display_subscriptions}")
        if mod.rating > 0:
            stats.append(f"评分 {mod.rating:.1f}")
        if stats:
            meta_lines.append("  ·  ".join(stats))
        meta = QLabel("\n".join(meta_lines))
        meta.setObjectName("cardMeta")
        meta.setWordWrap(True)
        meta.setFixedHeight(ui(32))
        layout.addWidget(meta)

        tags = QHBoxLayout()
        self.tags_layout = tags
        tags.setSpacing(ui(5))
        if mod.active:
            tags.addWidget(make_tag("已启用", "#35d49b"))
        if mod.conflict_with:
            tags.addWidget(make_tag("存在冲突", "#ff7070"))
        self._add_source_tag(tags)
        tags.addStretch(1)
        layout.addLayout(tags)

        action_row = QHBoxLayout()
        action_row.setSpacing(ui(6))
        button = QPushButton("禁用 Mod" if mod.active else "启用 Mod")
        button.setObjectName("cardActionActive" if mod.active else "cardAction")
        self.toggle_button = button
        button.clicked.connect(lambda: self.clicked.emit(self.mod.id))
        action_row.addWidget(button, 1)
        layout.addLayout(action_row)

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
        self.setObjectName(
            "modCardConflict" if self.mod.active and self.mod.conflict_with else ("modCardActive" if self.mod.active else "modCard")
        )
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if self.mod.active:
            self.tags_layout.addWidget(make_tag("已启用", "#35d49b"))
        if self.mod.conflict_with:
            self.tags_layout.addWidget(make_tag("存在冲突", "#ff7070"))
        self._add_source_tag(self.tags_layout)
        self.tags_layout.addStretch(1)
        self.toggle_button.setText("禁用 Mod" if self.mod.active else "启用 Mod")
        self.toggle_button.setObjectName("cardActionActive" if self.mod.active else "cardAction")
        self.style().unpolish(self)
        self.style().polish(self)
        self.toggle_button.style().unpolish(self.toggle_button)
        self.toggle_button.style().polish(self.toggle_button)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.mod.id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.mod.id, event.globalPos())
        event.accept()


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

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.target.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.target.move(event.globalPos() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        event.accept()


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
        preview = QLabel()
        preview.setObjectName("conflictPreview")
        preview.setAlignment(Qt.AlignCenter)
        preview.setPixmap(make_preview_pixmap(mod))
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


class ConflictCard(QFrame):
    """Compact conflict item. Double-clicking it disables the represented Mod."""
    disable_requested = pyqtSignal(str)

    def __init__(self, mod: Mod):
        super().__init__()
        self.mod = mod
        self.setObjectName("conflictCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(ui(208), ui(210))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui(10), ui(10), ui(10), ui(10))
        layout.setSpacing(ui(6))
        preview = QLabel()
        preview.setObjectName("conflictPreview")
        preview.setAlignment(Qt.AlignCenter)
        preview.setPixmap(make_preview_pixmap(mod))
        layout.addWidget(preview)
        title = TwoLineElidedLabel(mod.title or mod.file_name)
        title.setObjectName("cardTitle")
        title.setFixedHeight(ui(36))
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(ui(6))
        title_row.addWidget(title, 1)
        conflict_count = len(mod.conflict_with)
        count_badge = QLabel(str(conflict_count))
        count_badge.setObjectName("conflictCountBadge")
        count_badge.setFixedSize(ui(24), ui(24))
        count_badge.setAlignment(Qt.AlignCenter)
        count_badge.setToolTip(f"Conflicts with {conflict_count} enabled Mod(s)")
        title_row.addWidget(count_badge, 0, Qt.AlignTop)
        layout.addLayout(title_row)
        hint = QLabel("双击卡片以禁用")
        hint.setObjectName("conflictCaption")
        layout.addWidget(hint)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.disable_requested.emit(self.mod.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ConflictDialog(QDialog):
    disable_requested = pyqtSignal(str)

    @staticmethod
    def _conflict_groups(mods: dict[str, Mod]) -> list[list[Mod]]:
        """Return connected components of the active conflict graph."""
        remaining = {mod_id for mod_id, mod in mods.items() if mod.active and mod.conflict_with}
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
            groups.append(sorted((mods[mod_id] for mod_id in component), key=lambda mod: mod.title.lower()))
        return sorted(groups, key=lambda group: group[0].title.lower())

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
        for index, mod in enumerate(sorted(group, key=lambda item: (-len(item.conflict_with), item.title.lower()))):
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
        content_layout.addStretch(1)
        layout.addWidget(content, 1)


QtFileDialog = QFileDialog


class AppMessageDialog(QDialog):
    """Frameless, application-themed replacement for system message boxes."""

    def __init__(self, message: str, tone: str = "info", confirm: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
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
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
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


class MultiSelectComboBox(QComboBox):
    """A checkable combo box that keeps its popup open for multi-selection."""

    selection_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.view().viewport().installEventFilter(self)

    def eventFilter(self, source, event) -> bool:
        if source is self.view().viewport() and event.type() == QEvent.MouseButtonRelease:
            index = self.view().indexAt(event.pos())
            if index.isValid():
                state = self.model().data(index, Qt.CheckStateRole)
                next_state = Qt.Unchecked if state == Qt.Checked else Qt.Checked
                self.model().setData(index, next_state, Qt.CheckStateRole)
                self.selection_changed.emit()
                return True
        return super().eventFilter(source, event)

    def checked_values(self) -> list[str]:
        return [
            self.itemData(index)
            for index in range(self.count())
            if self.model().data(self.model().index(index, 0), Qt.CheckStateRole) == Qt.Checked
        ]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("L4D2 Boss · 求生之路 2 Mod 管理器")
        self.resize(ui(1200), ui(820))
        self.setMinimumSize(ui(1020), ui(680))
        self.storage = AppStorage(APP_ROOT)
        self.settings = self.storage.load_settings()
        self.mods = self.storage.load_mods()
        self.steam_cache = self.storage.load_steam_cache()
        if self._reclassify_loaded_mods():
            self.storage.save_mods(self.mods)
        self.collections = self.storage.load_collections()
        self._ensure_default_collection()
        self._selected_collection_names: set[str] = {"default"}
        self._updating_collection_combo = False
        self.current_category = "all"
        self.thread_pool = QThreadPool.globalInstance()
        self.steam_sync_in_progress = False
        self._steam_cancel_event = Event()
        self._card_widgets: dict[str, ModCard] = {}
        self._conflict_paths: dict[str, set[str]] = {}
        self._active_path_owners: dict[str, set[str]] = {}
        self._build_ui()
        self._apply_style()
        self._rebuild_conflict_index()
        self.refresh_collection_combo()
        self.apply_selected_collections(write_addonlist=False)
        self.refresh_tree()
        self.refresh_cards()
        self.refresh_stats()
        mod_dir = self.settings.get("mod_dir")
        if mod_dir and Path(mod_dir).exists() and not self.mods:
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

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appSurface")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QSplitter(Qt.Horizontal)
        body.setHandleWidth(1)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(ui(16), ui(18), ui(12), ui(16))
        side_layout.setSpacing(ui(10))
        caption = QLabel("MOD 分类")
        caption.setObjectName("sectionLabel")
        side_layout.addWidget(caption)
        self.category_tree = QTreeWidget()
        self.category_tree.setHeaderHidden(True)
        self.category_tree.itemSelectionChanged.connect(self.on_category_selected)
        side_layout.addWidget(self.category_tree, 1)
        help_text = QLabel("提示：点击卡片或「启用 Mod」即可切换状态。\n右键卡片可加入已保存的组合。")
        help_text.setObjectName("sideHint")
        help_text.setWordWrap(True)
        side_layout.addWidget(help_text)
        body.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(ui(22), ui(18), 0, ui(18))
        content_layout.setSpacing(ui(14))
        self.content_bar = self._build_content_bar()
        self.content_bar.setFixedWidth(ui(904))
        content_layout.addWidget(self.content_bar, 0, Qt.AlignLeft)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.cards_host = QWidget()
        self.cards_host.setObjectName("cardsHost")
        self.cards_layout = QGridLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(ui(16))
        self.cards_layout.setVerticalSpacing(ui(16))
        self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.cards_host)
        content_layout.addWidget(self.scroll, 1)
        body.addWidget(content)
        body.setSizes([ui(275), ui(1045)])
        root.addWidget(body, 1)
        root.addWidget(self._build_footer())
        self.setCentralWidget(central)

    def _build_header(self) -> QWidget:
        header = DragHeader(self)
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(ui(24), ui(14), ui(14), ui(14))
        layout.setSpacing(ui(10))
        brand = QVBoxLayout()
        brand.setSpacing(0)
        name_row = QHBoxLayout()
        name_row.setSpacing(ui(8))
        name = QPushButton("L4D2  BOSS")
        name.setObjectName("brandButton")
        name.setToolTip("查看软件信息")
        name.clicked.connect(self.show_about)
        credit = QLabel("@ by Mr.Chen")
        credit.setObjectName("brandCredit")
        credit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        credit.setContentsMargins(0, ui(3), 0, 0)
        name_row.addWidget(name)
        name_row.addWidget(credit)
        name_row.addStretch(1)
        sub = QLabel("MOD LOADOUT MANAGER")
        sub.setObjectName("brandSub")
        brand.addLayout(name_row)
        brand.addWidget(sub)
        layout.addLayout(brand)
        layout.addStretch(1)
        self.choose_button = self._header_button(QStyle.SP_DirOpenIcon, "选择目录", self.choose_directory, secondary=True)
        self.refresh_button = self._header_button(QStyle.SP_BrowserReload, "扫描 Mod", lambda: self.scan_mods(False))
        self.fetch_button = self._header_button(QStyle.SP_ArrowDown, "同步 Steam", self.fetch_steam_info)
        layout.addWidget(self.choose_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.fetch_button)
        self.enable_all_button = self._header_button(QStyle.SP_DialogApplyButton, "全部激活", lambda: self.set_all_mods_active(True))
        self.disable_all_button = self._header_button(QStyle.SP_DialogCancelButton, "全部禁用", lambda: self.set_all_mods_active(False), secondary=True)
        layout.addWidget(self.enable_all_button)
        layout.addWidget(self.disable_all_button)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setText("×")
        close.setToolTip("关闭程序")
        close.clicked.connect(self.close)
        layout.addWidget(close)
        return header

    def show_about(self) -> None:
        AboutDialog(self).exec_()

    def _header_button(self, icon, text, handler, secondary: bool = False) -> QPushButton:
        button = QPushButton(self.style().standardIcon(icon), text)
        button.setObjectName("headerButtonSecondary" if secondary else "headerButton")
        button.clicked.connect(handler)
        return button

    def _build_content_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.content_title = QLabel("全部 Mod")
        self.content_title.setObjectName("contentTitle")
        self.content_subtitle = QLabel()
        self.content_subtitle.setObjectName("contentSubtitle")
        title_box.addWidget(self.content_title)
        title_box.addWidget(self.content_subtitle)
        layout.addLayout(title_box)
        layout.addStretch(1)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("搜索名称、作者或 Workshop ID…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.refresh_cards)
        layout.addWidget(self.search_input)
        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("sortCombo")
        self.sort_combo.setMinimumWidth(ui(132))
        self.sort_combo.view().setObjectName("sortComboMenu")
        self.sort_combo.addItem("名称 · 正序", ("name", False))
        self.sort_combo.addItem("名称 · 倒序", ("name", True))
        self.sort_combo.addItem("时间 · 正序", ("modified", False))
        self.sort_combo.addItem("时间 · 倒序", ("modified", True))
        saved_sort = (self.settings.get("sort_field", "name"), bool(self.settings.get("sort_descending", False)))
        for index in range(self.sort_combo.count()):
            if self.sort_combo.itemData(index) == saved_sort:
                self.sort_combo.setCurrentIndex(index)
                break
        self.sort_combo.currentIndexChanged.connect(self.on_sort_changed)
        layout.addWidget(self.sort_combo)
        self.collection_combo = MultiSelectComboBox()
        self.collection_combo.setObjectName("collectionCombo")
        self.collection_combo.setMinimumWidth(ui(210))
        self.collection_combo.setMaxVisibleItems(7)
        self.collection_combo.view().setObjectName("collectionComboMenu")
        self.collection_combo.view().setTextElideMode(Qt.ElideRight)
        self.collection_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.collection_combo.selection_changed.connect(self.on_collection_selection_changed)
        layout.addWidget(self.collection_combo)
        self.delete_collection_button = QPushButton("删除")
        self.delete_collection_button.setObjectName("collectionDeleteButton")
        self.delete_collection_button.setToolTip("删除当前勾选的组合（default 组合不可删除）")
        self.delete_collection_button.clicked.connect(self.delete_selected_collections)
        layout.addWidget(self.delete_collection_button)
        return bar

    def _build_footer_legacy(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(ui(24), ui(10), ui(24), ui(10))
        self.total_label, self.active_label = QLabel(), QLabel()
        self.conflict_button = QPushButton()
        self.conflict_button.setObjectName("conflictButton")
        self.conflict_button.clicked.connect(self.show_conflicts)
        layout.addWidget(self.total_label)
        layout.addWidget(self.active_label)
        layout.addWidget(self.conflict_button)
        layout.addStretch(1)
        self.save_button = QPushButton("保存当前组合")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save_collection)
        layout.addWidget(self.save_button)
        layout.insertWidget(layout.indexOf(self.save_button), self.enable_all_button)
        layout.insertWidget(layout.indexOf(self.save_button), self.disable_all_button)
        return footer

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, ui(10), 0, ui(10))
        layout.setSpacing(0)

        status_host = QWidget()
        status_host.setFixedWidth(ui(277))
        status_layout = QHBoxLayout(status_host)
        status_layout.setContentsMargins(ui(24), 0, 0, 0)
        status_layout.setSpacing(ui(8))
        self.total_label, self.active_label = QLabel(), QLabel()
        self.conflict_button = QPushButton()
        self.conflict_button.setObjectName("conflictButton")
        self.conflict_button.clicked.connect(self.show_conflicts)
        status_layout.addWidget(self.total_label)
        status_layout.addWidget(self.active_label)
        status_layout.addWidget(self.conflict_button)
        status_layout.addStretch(1)
        layout.addWidget(status_host)

        action_host = QWidget()
        action_host.setFixedWidth(ui(904))
        action_layout = QHBoxLayout(action_host)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(ui(10))
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
        action_layout.addWidget(self.enable_all_button)
        action_layout.addWidget(self.disable_all_button)
        self.save_button = QPushButton("保存当前组合")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save_collection)
        action_layout.addWidget(self.save_button)
        layout.addWidget(action_host)
        layout.addStretch(1)
        return footer

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"; }
            QMainWindow, QDialog { background: transparent; color: #e8edf5; }
            #appSurface { background: #10141c; border-radius: 14px; }
            #header { background: #171d29; border-bottom: 1px solid #2a3444; border-top-left-radius: 14px; border-top-right-radius: 14px; }
            #brand { color: #f4f8ff; font-size: 20px; font-weight: 800; letter-spacing: 2px; }
            #brandButton { color: #f4f8ff; background: transparent; border: 0; padding: 0; font-size: 20px; font-weight: 800; letter-spacing: 2px; text-align: left; }
            #brandButton:hover { color: #79a5ff; }
            #brandCredit { color: #8291a8; font-size: 11px; font-weight: 700; }
            #brandSub, #contentSubtitle { color: #8090a8; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
            #headerButton, #headerButtonSecondary { background: #273347; color: #d9e4f4; border: 1px solid #38465c; border-radius: 7px; padding: 8px 13px; font-weight: 700; }
            #headerButton:hover, #headerButtonSecondary:hover { background: #33435c; color: white; }
            #primaryButton { background: #2d65d6; color: white; border: 0; border-radius: 7px; padding: 8px 13px; font-weight: 700; }
            #primaryButton:hover { background: #3c78ee; }
            #sidebar { background: #151b26; border-right: 1px solid #283242; }
            #sectionLabel { color: #94a4bc; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
            #sideHint { color: #718097; font-size: 11px; line-height: 1.45; padding: 10px; background: #1c2533; border-radius: 7px; }
            QTreeWidget { background: transparent; border: 0; color: #b8c4d5; outline: none; font-size: 13px; }
            QTreeWidget::item { min-height: 29px; border-radius: 6px; padding: 3px 5px; }
            QTreeWidget::item:hover { background: #212b3a; color: #f2f6fc; }
            QTreeWidget::item:selected { background: #2b5fca; color: white; font-weight: 700; }
            QScrollArea { border: 0; background: #10141c; }
            QScrollBar:vertical { background: #10151e; width: 8px; margin: 5px 0 5px 0; border-radius: 4px; }
            QScrollBar::handle:vertical { background: #273242; min-height: 42px; border-radius: 4px; }
            QScrollBar::handle:vertical:hover { background: #3a4a60; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QScrollBar:horizontal { background: #10151e; height: 8px; margin: 0 4px 3px 4px; border-radius: 4px; }
            QScrollBar::handle:horizontal { background: #273242; min-width: 42px; border-radius: 4px; }
            QScrollBar::handle:horizontal:hover { background: #3a4a60; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
            #cardsHost { background: #10141c; }
            #contentTitle { color: #f1f5fb; font-size: 22px; font-weight: 800; }
            QLineEdit, QComboBox { min-height: 32px; background: #19212e; color: #e6edf7; border: 1px solid #2c384a; border-radius: 7px; padding: 0 11px; }
            QLineEdit:focus, QComboBox:focus { border-color: #5486ec; background: #1b2534; }
            #searchInput { min-width: 235px; }
            #collectionCombo, #sortCombo { padding-left: 12px; padding-right: 30px; font-weight: 600; }
            #collectionCombo QLineEdit { background: transparent; border: 0; padding: 0; color: #e6edf7; font-weight: 600; }
            #collectionCombo:hover, #sortCombo:hover { background: #202b3c; border-color: #3b506e; }
            #collectionCombo::drop-down, #sortCombo::drop-down { subcontrol-origin: padding; subcontrol-position: top right; border: 0; width: 30px; }
            #collectionComboMenu, #sortComboMenu { background: #18212e; color: #dfe9f8; border: 1px solid #3a4a61; border-radius: 8px; outline: 0; padding: 5px; selection-background-color: transparent; }
            #collectionComboMenu::item, #sortComboMenu::item { min-height: 40px; border: 1px solid transparent; border-radius: 6px; padding: 0 12px; margin: 3px 0; }
            #collectionComboMenu::item:hover, #sortComboMenu::item:hover { background: #25344a; border-color: #3a5272; color: #ffffff; }
            #collectionComboMenu::item:selected, #sortComboMenu::item:selected { background: #2d65d6; border-color: #4d83eb; color: #ffffff; font-weight: 700; }
            #collectionComboMenu QScrollBar:vertical, #sortComboMenu QScrollBar:vertical { background: transparent; width: 7px; margin: 7px 3px 7px 0; }
            #collectionComboMenu QScrollBar::handle:vertical, #sortComboMenu QScrollBar::handle:vertical { background: #3a4b63; min-height: 30px; border-radius: 3px; }
            #collectionComboMenu QScrollBar::handle:vertical:hover, #sortComboMenu QScrollBar::handle:vertical:hover { background: #50709a; }
            #collectionDeleteButton { min-height: 32px; color: #f1c2c7; background: #30212a; border: 1px solid #5c3743; border-radius: 7px; padding: 0 11px; font-weight: 700; }
            #collectionDeleteButton:hover { color: #ffffff; background: #923946; border-color: #dc6170; }
            #collectionDeleteButton:disabled { color: #687384; background: #1b222d; border-color: #2d3747; }
            #modCard, #modCardActive, #modCardConflict { background: #18202c; border: 1px solid #293649; border-radius: 10px; }
            #modCard:hover { background: #1c2634; border-color: #4c6890; }
            #modCardActive { border: 2px solid #23c987; background: #12362e; }
            #modCardActive:hover { background: #174538; border-color: #55efad; }
            #modCardConflict { border: 2px solid #f04455; background: #481923; }
            #modCardConflict:hover { background: #5a1d29; border-color: #ff7885; }
            #preview { background: #111821; border-radius: 7px; min-height: 112px; max-height: 112px; }
            #cardTitle { color: #f2f6fc; font-size: 14px; font-weight: 700; }
            #cardMeta { color: #91a0b4; font-size: 11px; }
            #tag { color: #ffffff; border-radius: 4px; padding: 3px 6px; font-size: 10px; font-weight: 700; }
            #cardAction, #cardActionActive { min-height: 28px; border-radius: 6px; font-weight: 700; }
            #cardAction { color: #cbd7e8; background: #253247; border: 1px solid #34445c; }
            #cardAction:hover { color: white; background: #2d65d6; border-color: #2d65d6; }
            #cardActionActive { color: #d2ffeb; background: #167453; border: 1px solid #2be39a; }
            #cardActionActive:hover { color: white; background: #b84752; border-color: #b84752; }
            #tagButton { min-height: 20px; color: #ffffff; border: 0; border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: 700; }
            #tagButton:hover { border: 1px solid #d8e7ff; padding: 0 5px; }
            #emptyText { color: #9db2d0; background: transparent; border: 0; padding: 0; font-size: 15px; font-weight: 500; line-height: 1.7; letter-spacing: 0.5px; }
            #steamSyncStatus { background: #1b2a3d; border: 1px solid #355577; border-radius: 7px; }
            #steamSyncLabel { color: #bcd7ff; font-size: 11px; font-weight: 700; }
            #steamSyncProgress { min-height: 6px; max-height: 6px; border: 0; border-radius: 3px; background: #263a54; }
            #steamSyncProgress::chunk { border-radius: 3px; background: #4c86eb; }
            #footer { background: #151b26; border-top: 1px solid #283242; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
            #footer QLabel { color: #9eacc0; padding-right: 12px; }
            #conflictButton { color: #ffabab; background: transparent; border: 0; font-weight: 700; }
            #conflictButton:disabled { color: #718097; }
            #closeButton { min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px; padding: 0; border: 0; color: #92a1b6; background: transparent; font-size: 13px; font-weight: 800; }
            #closeButton:hover { color: #ff7a85; background: transparent; }
            #dialogHeader { background: #1b2432; border-bottom: 1px solid #2d3a4d; border-top-left-radius: 14px; border-top-right-radius: 14px; }
            #dialogTitle { color: #f1f5fb; font-size: 17px; font-weight: 800; }
            #dialogSubtitle { color: #8596af; font-size: 11px; }
            #aboutContent { background: #121924; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
            #aboutBrand { color: #f1f5fb; font-size: 25px; font-weight: 800; letter-spacing: 2px; }
            #aboutVersion { color: #7698d9; font-size: 12px; font-weight: 700; }
            #aboutDesigner { color: #aebbd0; font-size: 12px; font-weight: 500; padding: 2px 0; background: transparent; border: 0; }
            #aboutDescription { color: #9eacc0; font-size: 12px; line-height: 1.55; }
            #conflictBody { background: #10141c; border: 0; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
            #conflictScroll { background: transparent; border: 0; }
            #conflictViewport { background: #10141c; border: 0; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
            #conflictHost { background: transparent; border: 0; }
            #groupCardScroll, #groupCardsHost { background: #151d29; border: 0; border-radius: 7px; }
            #conflictGroup { background: #151d29; border: 1px solid #303b4d; border-radius: 10px; }
            #conflictGroupLabel { color: #e99aa2; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
            #conflictCard { background: #20202b; border: 1px solid #6d404b; border-radius: 10px; }
            #conflictCard:hover { background: #282333; border-color: #b85866; }
            #conflictCountBadge { color: #fff4f5; background: #b84752; border: 1px solid #ef7d87; border-radius: 12px; font-size: 11px; font-weight: 800; }
            #conflictPreview { background: #111821; border-radius: 7px; min-height: 104px; max-height: 104px; }
            #conflictCaption { color: #f08b96; font-size: 11px; font-weight: 700; }
            #conflictPeers { color: #abb8c9; font-size: 11px; }
            #conflictPeerButton { max-height: 28px; color: #ffd3d7; background: #3c2730; border: 1px solid #754551; border-radius: 6px; padding: 0 9px; font-size: 11px; font-weight: 700; }
            #conflictPeerButton:hover { color: white; background: #c94a54; border-color: #e26770; }
            #promptSurface { background: #18212e; border: 1px solid #3a4a61; border-radius: 12px; }
            #promptText { color: #e5edf9; font-size: 13px; line-height: 1.5; }
            #promptIconInfo, #promptIconWarning, #promptIconError { color: white; border-radius: 15px; font-size: 16px; font-weight: 800; }
            #promptIconInfo { background: #2d65d6; border: 1px solid #5b8ced; }
            #promptIconWarning { background: #a66d24; border: 1px solid #e5a34a; }
            #promptIconError { background: #a93f4c; border: 1px solid #ed7681; }
            #promptInput { min-height: 34px; background: #111a26; color: #eef5ff; border: 1px solid #3a4a61; border-radius: 7px; padding: 0 10px; }
            #promptInput:focus { background: #162131; border-color: #5486ec; }
            #promptPrimaryButton, #promptSecondaryButton { min-height: 32px; border-radius: 6px; padding: 0 15px; font-weight: 700; }
            #promptPrimaryButton { background: #2d65d6; color: white; border: 1px solid #3d78e7; }
            #promptPrimaryButton:hover { background: #3c78ee; border-color: #6297f3; }
            #promptSecondaryButton { background: #253247; color: #cfdbeb; border: 1px solid #3b4b62; }
            #promptSecondaryButton:hover { background: #30415a; border-color: #526882; }
            QToolTip { background: #202c3d; color: #e9f1ff; border: 1px solid #496282; border-radius: 5px; padding: 6px 8px; font-size: 11px; }
            QMenu { background: #18212e; color: #dfe9f8; border: 1px solid #3a4a61; border-radius: 8px; padding: 5px; }
            QMenu::item { min-height: 29px; border-radius: 5px; padding: 0 26px 0 11px; margin: 1px 0; }
            QMenu::item:selected { background: #2d65d6; color: white; }
            QMenu::item:disabled { color: #718097; background: transparent; }
            QMenu::separator { height: 1px; background: #304057; margin: 5px 8px; }
            QMenu::right-arrow { width: 8px; height: 8px; }
            QMessageBox, QInputDialog, QFileDialog { background: #18212e; color: #e8edf5; }
            QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel { color: #dce6f5; }
            QMessageBox QLabel#qt_msgbox_label { min-width: 280px; line-height: 1.45; }
            QMessageBox QPushButton, QInputDialog QPushButton, QFileDialog QPushButton { min-height: 30px; background: #2d65d6; color: white; border: 1px solid #3d78e7; border-radius: 6px; padding: 0 14px; font-weight: 700; }
            QMessageBox QPushButton:hover, QInputDialog QPushButton:hover, QFileDialog QPushButton:hover { background: #3c78ee; border-color: #6297f3; }
            QMessageBox QPushButton:pressed, QInputDialog QPushButton:pressed, QFileDialog QPushButton:pressed { background: #2455b9; }
            QMessageBox QPushButton[text="取消"], QMessageBox QPushButton[text="否"], QInputDialog QPushButton[text="取消"], QFileDialog QPushButton[text="取消"] { background: #253247; border-color: #3b4b62; color: #cfdbeb; }
            QMessageBox QPushButton[text="取消"]:hover, QMessageBox QPushButton[text="否"]:hover, QInputDialog QPushButton[text="取消"]:hover, QFileDialog QPushButton[text="取消"]:hover { background: #30415a; border-color: #526882; }
            QInputDialog QLineEdit, QFileDialog QLineEdit { min-height: 30px; background: #121a25; color: #edf4ff; border: 1px solid #3a4a61; border-radius: 6px; padding: 0 9px; }
            QInputDialog QLineEdit:focus, QFileDialog QLineEdit:focus { border-color: #5486ec; background: #162131; }
            QFileDialog QTreeView, QFileDialog QListView, QFileDialog QSidebar { background: #121a25; color: #dce6f5; border: 1px solid #303e53; outline: 0; }
            QFileDialog QTreeView::item, QFileDialog QListView::item, QFileDialog QSidebar::item { min-height: 27px; padding: 2px 7px; }
            QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected, QFileDialog QSidebar::item:selected { background: #2d65d6; color: white; }
            QFileDialog QComboBox { min-height: 28px; }
            QStatusBar { background: #151b26; color: #91a0b4; border-top: 1px solid #283242; }
        """)

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
        self.category_tree.clear()
        for category in CATEGORIES:
            self.category_tree.addTopLevelItem(self._make_tree_item(category))
        self.category_tree.expandAll()
        self.category_tree.setCurrentItem(self.category_tree.topLevelItem(0))

    def _make_tree_item(self, entry) -> QTreeWidgetItem:
        if isinstance(entry, tuple):
            item = QTreeWidgetItem([entry[1]])
            item.setData(0, Qt.UserRole, entry[0])
            return item
        item = QTreeWidgetItem([entry["label"]])
        item.setData(0, Qt.UserRole, entry["id"])
        for child in entry.get("children", []):
            item.addChild(self._make_tree_item(child))
        return item

    def refresh_cards(self) -> None:
        clear_layout(self.cards_layout)
        self._card_widgets = {}
        filtered = self.filtered_mods()
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
            empty = QLabel("没有找到匹配的 Mod\n调整搜索条件，或点击「选择目录」导入 VPK 文件。")
            empty.setObjectName("emptyText")
            empty.setAlignment(Qt.AlignCenter)
            empty_layout.addWidget(empty, 0, Qt.AlignHCenter)
            empty_layout.addStretch(1)
            self.cards_layout.addWidget(empty_host, 0, 0, 1, columns)
            return
        self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.cards_layout.setRowStretch(0, 0)
        columns = self.card_columns()
        for column in range(max(columns, self.cards_layout.columnCount())):
            self.cards_layout.setColumnStretch(column, 0)
        for index, mod in enumerate(filtered):
            card = ModCard(mod, self.collection_names_for(mod.id))
            card.clicked.connect(self.toggle_mod)
            card.context_requested.connect(self.show_card_context_menu)
            self._card_widgets[mod.id] = card
            self.cards_layout.addWidget(card, index // columns, index % columns, Qt.AlignTop)
        for column in range(columns):
            self.cards_layout.setColumnMinimumWidth(column, ui(214))

    def card_columns(self) -> int:
        width = self.scroll.viewport().width() if hasattr(self, "scroll") else 900
        return max(1, width // ui(225))

    def collection_names_for(self, mod_id: str) -> list[str]:
        return [item.name for item in self.collections if mod_id in item.mod_ids]

    def show_card_context_menu(self, mod_id: str, global_pos) -> None:
        menu = QMenu(self)
        add_menu = menu.addMenu("加入已保存的组合")
        existing = set(self.collection_names_for(mod_id))
        if not self.collections:
            action = add_menu.addAction("暂无组合，请先保存当前组合")
            action.setEnabled(False)
        for collection in self.collections:
            action = add_menu.addAction(collection.name)
            action.setEnabled(collection.name not in existing)
            action.triggered.connect(lambda _=False, name=collection.name: self.add_mod_to_collection(mod_id, name))
        menu.exec_(global_pos)

    def add_mod_to_collection(self, mod_id: str, collection_name: str) -> None:
        for collection in self.collections:
            if collection.name == collection_name:
                if mod_id not in collection.mod_ids:
                    collection.mod_ids.append(mod_id)
                self.storage.save_collections(self.collections)
                self.refresh_cards()
                return

    def filtered_mods(self) -> list[Mod]:
        mods = list(self.mods.values())
        if self.current_category != "all":
            mods = [mod for mod in mods if self.current_category in mod.categories]
        query = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""
        if query:
            mods = [mod for mod in mods if query in " ".join([mod.title, mod.author, mod.file_name, mod.workshop_id or ""]).lower()]
        sort_value = self.sort_combo.currentData() if hasattr(self, "sort_combo") else ("name", False)
        sort_field, descending = sort_value if isinstance(sort_value, tuple) else ("name", False)
        if sort_field == "modified":
            return sorted(mods, key=self._time_sort_key, reverse=descending)
        return sorted(mods, key=lambda mod: mod.title.casefold(), reverse=descending)

    @staticmethod
    def _time_sort_key(mod: Mod) -> tuple[int, str]:
        """Sort by the VPK's local modification time, with a stable name tie-breaker."""
        try:
            modified_at = Path(mod.file_path).stat().st_mtime_ns
        except OSError:
            modified_at = 0
        return modified_at, mod.title.casefold()

    def on_sort_changed(self) -> None:
        sort_value = self.sort_combo.currentData()
        sort_field, descending = sort_value if isinstance(sort_value, tuple) else ("name", False)
        self.settings["sort_field"] = sort_field
        self.settings["sort_descending"] = descending
        self.storage.save_settings(self.settings)
        self.refresh_cards()

    def refresh_stats(self) -> None:
        conflicts = sum(1 for mod in self.mods.values() if mod.conflict_with)
        active = sum(1 for mod in self.mods.values() if mod.active)
        self.total_label.setText(f"共 {len(self.mods)} 个 Mod")
        self.active_label.setText(f"已启用 {active} 个")
        self.conflict_button.setText(f"{conflicts} 个冲突" if conflicts else "无冲突")
        self.conflict_button.setEnabled(conflicts > 0)

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

    def _ensure_default_collection(self) -> None:
        if any(collection.name == "default" for collection in self.collections):
            return
        self.collections.insert(
            0,
            ModCollection(
                name="default",
                mod_ids=[mod.id for mod in self.mods.values() if mod.active],
            ),
        )
        self.storage.save_collections(self.collections)

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
        self.delete_collection_button.setEnabled(any(name != "default" for name in selected))

    def on_collection_selection_changed(self) -> None:
        if self._updating_collection_combo:
            return
        self._selected_collection_names = set(self.collection_combo.checked_values())
        self._update_collection_combo_label()
        if self._selected_collection_names:
            self.apply_selected_collections()

    def apply_selected_collections(self, write_addonlist: bool = True) -> None:
        selected = [
            collection
            for collection in self.collections
            if collection.name in self._selected_collection_names
        ]
        if not selected:
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

    def delete_selected_collections(self) -> None:
        removable = self._selected_collection_names - {"default"}
        if not removable:
            QMessageBox.information(self, "无法删除", "default 是启动时的基础组合，不能删除。")
            return
        names = "、".join(sorted(removable))
        if QMessageBox.question(self, "删除组合", f"确定删除组合「{names}」吗？") != QMessageBox.Yes:
            return
        self.collections = [collection for collection in self.collections if collection.name not in removable]
        self._selected_collection_names -= removable
        if not self._selected_collection_names:
            self._selected_collection_names = {"default"}
        self.storage.save_collections(self.collections)
        self.refresh_collection_combo()
        self.apply_selected_collections()

    def on_category_selected(self) -> None:
        items = self.category_tree.selectedItems()
        if items:
            self.current_category = items[0].data(0, Qt.UserRole)
            self.content_title.setText(items[0].text(0))
            self.refresh_cards()

    def choose_directory(self) -> None:
        # Use the platform's native folder picker so users get the familiar
        # Windows File Explorer experience when choosing the Mod directory.
        directory = QtFileDialog.getExistingDirectory(
            self,
            "选择 Mod 文件夹",
            self.settings.get("mod_dir") or str(Path.home()),
            QtFileDialog.ShowDirsOnly,
        )
        if directory:
            self.settings["mod_dir"] = directory
            self.storage.save_settings(self.settings)
            self.scan_mods(False)

    def scan_mods(self, refresh_all: bool) -> None:
        mod_dir = self.settings.get("mod_dir")
        if not mod_dir:
            QMessageBox.information(self, "需要选择目录", "请先选择保存 VPK Mod 的文件夹。")
            return
        if not Path(mod_dir).exists():
            QMessageBox.warning(self, "目录不存在", f"目录不存在：{mod_dir}")
            return
        self.set_busy(True, "正在扫描 VPK 文件…")
        worker = Worker(scan_mod_directory, Path(mod_dir), self.mods, refresh_all)
        worker.signals.finished.connect(self.on_scan_finished)
        worker.signals.failed.connect(self.on_worker_failed)
        self.thread_pool.start(worker)

    def reset_mods(self) -> None:
        if QMessageBox.question(self, "重新扫描", "将清除本地 Mod 元数据并重新扫描目录，是否继续？") == QMessageBox.Yes:
            self.storage.reset_mods()
            self.mods = {}
            self.scan_mods(True)

    def on_scan_finished(self, mods: dict[str, Mod]) -> None:
        old_active = {key for key, mod in self.mods.items() if mod.active}
        self.mods = mods
        for key in old_active:
            if key in self.mods:
                self.mods[key].active = True
        self._apply_steam_cache(self.mods)
        self._rebuild_conflict_index()
        self.storage.save_mods(self.mods)
        self.refresh_cards(); self.refresh_stats(); self.set_busy(False)

    def fetch_steam_info(self) -> None:
        if self.steam_sync_in_progress:
            self.cancel_steam_sync()
            return
        if not self.mods:
            QMessageBox.information(self, "暂无 Mod", "请先扫描 Mod 文件夹。")
            return
        # A full rescan creates fresh Mod objects.  Hydrate those from the
        # persisted cache before deciding which items still need a request.
        self._apply_steam_cache(self.mods)
        pending_mods = steam_sync_candidates(self.mods)
        if not pending_mods:
            QMessageBox.information(self, "Steam 信息已是最新", "所有可识别的 Workshop Mod 都已有本地 Steam 数据，无需重新请求。")
            return
        if QMessageBox.question(
            self,
            "开始同步 Steam 信息",
            f"将从 Steam 获取 {len(pending_mods)} 个尚未同步的 Mod 信息。已有本地数据的 Mod 不会重新请求。点击“确定”后将在后台继续执行，你可以继续进行其他操作。",
        ) != QMessageBox.Yes:
            return
        self.steam_sync_in_progress = True
        self._steam_cancel_event.clear()
        self.fetch_button.setEnabled(False)
        self.fetch_button.setText("取消同步")
        self.fetch_button.setEnabled(True)
        total = len(pending_mods)
        self.steam_sync_progress.setRange(0, total)
        self.steam_sync_progress.setValue(0)
        self._set_steam_sync_status(0, total)
        self.steam_sync_widget.show()
        worker = Worker(fetch_steam_for_mods, deepcopy(pending_mods), progress_callback=None, cancel_event=self._steam_cancel_event)
        worker.kwargs["progress_callback"] = worker.signals.progress.emit
        worker.signals.progress.connect(self._set_steam_sync_status)
        worker.signals.finished.connect(self.on_steam_finished)
        worker.signals.failed.connect(self.on_steam_failed)
        worker.signals.cancelled.connect(self.on_steam_cancelled)
        self.thread_pool.start(worker)

    def on_steam_finished(self, mods: dict[str, Mod]) -> None:
        for mod_id, updated in mods.items():
            local = self.mods.get(mod_id)
            if local is None:
                continue
            for field in ("title", "author", "subscriptions", "rating", "description", "steam_tags", "steam_loaded", "categories"):
                setattr(local, field, getattr(updated, field))
        self.steam_sync_in_progress = False
        self._reset_steam_sync_controls()
        self.steam_sync_widget.hide()
        self.storage.save_mods(self.mods)
        self._save_steam_cache()
        QMessageBox.information(self, "Steam 同步完成", "Steam 信息已获取完成。点击“确定”后刷新页面。")
        self.refresh_cards()
        self.refresh_stats()

    def on_steam_failed(self, message: str) -> None:
        self.steam_sync_in_progress = False
        self._reset_steam_sync_controls()
        self.steam_sync_widget.hide()
        QMessageBox.critical(self, "Steam 同步失败", message)

    def cancel_steam_sync(self) -> None:
        if not self.steam_sync_in_progress:
            return
        self._steam_cancel_event.set()
        self.fetch_button.setText("取消中…")
        self.fetch_button.setEnabled(False)
        label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
        if label is not None:
            label.setText("正在取消 Steam 同步…")

    def on_steam_cancelled(self) -> None:
        self.steam_sync_in_progress = False
        self._reset_steam_sync_controls()
        self.steam_sync_widget.hide()
        QMessageBox.information(self, "Steam 同步已取消", "已停止后续 Mod 的 Steam 数据同步。")

    def _reset_steam_sync_controls(self) -> None:
        self.fetch_button.setText("同步 Steam")
        self.fetch_button.setEnabled(True)

    def _set_steam_sync_status(self, completed: int, total: int) -> None:
        self.steam_sync_progress.setRange(0, max(total, 1))
        self.steam_sync_progress.setValue(completed)
        percent = round(completed * 100 / total) if total else 100
        label = self.steam_sync_widget.findChild(QLabel, "steamSyncLabel")
        if label is not None:
            label.setText(f"正在同步 Steam 数据… {completed}/{total}（{percent}%）")

    def set_all_mods_active(self, active: bool) -> None:
        if not self.mods or all(mod.active == active for mod in self.mods.values()):
            return
        for mod in self.mods.values():
            mod.active = active
        self._rebuild_conflict_index()
        self.storage.save_mods(self.mods)
        self._refresh_card_states()
        self.refresh_stats()

    def toggle_mod(self, mod_id: str) -> None:
        if mod_id in self.mods:
            self.mods[mod_id].active = not self.mods[mod_id].active
            affected = self._update_conflicts_for_toggle(mod_id)
            self.storage.save_mods(self.mods)
            self._refresh_card_states(affected)
            self.refresh_stats()

    def show_conflicts(self) -> None:
        if any(mod.conflict_with for mod in self.mods.values()):
            dialog = ConflictDialog(self.mods, self)
            dialog.disable_requested.connect(self.disable_conflict_mod)
            dialog.disable_requested.connect(dialog.refresh_after_disable)
            dialog.exec_()

    def disable_conflict_mod(self, mod_id: str) -> None:
        mod = self.mods.get(mod_id)
        if mod is None or not mod.active:
            return
        mod.active = False
        affected = self._update_conflicts_for_toggle(mod_id)
        self.storage.save_mods(self.mods)
        self._refresh_card_states(affected)
        self.refresh_stats()

    def save_collection(self) -> None:
        active_ids = [mod.id for mod in self.mods.values() if mod.active]
        if not active_ids:
            QMessageBox.information(self, "没有已启用 Mod", "请先至少启用一个 Mod。")
            return
        name, ok = QInputDialog.getText(self, "保存 Mod 组合", "组合名称：")
        if ok and name.strip():
            name = name.strip()
            self.collections = [item for item in self.collections if item.name != name]
            self.collections.append(ModCollection(name=name, mod_ids=active_ids))
            self.storage.save_collections(self.collections)
            self._selected_collection_names = {name}
            self.refresh_collection_combo()
            self.apply_selected_collections()
            QMessageBox.information(self, "保存完成", f"已保存「{name}」，并写入 addonlist.txt。")

    def write_addonlist(self) -> None:
        mod_dir = self.settings.get("mod_dir")
        if mod_dir:
            lines = ['"AddonList"', "{\n"]
            for mod in sorted(self.mods.values(), key=lambda item: item.file_name.lower()):
                lines.append(f'\t"{mod.file_name.replace(chr(92), "/")}"\t\t"{"1" if mod.active else "0"}"\n')
            lines.append("}\n")
            Path(mod_dir, "addonlist.txt").write_text("".join(lines), encoding="utf-8")

    def on_worker_failed(self, message: str) -> None:
        self.set_busy(False)
        QMessageBox.critical(self, "操作失败", message)

    def set_busy(self, busy: bool, message: str = "") -> None:
        for button in (self.choose_button, self.refresh_button, self.fetch_button, self.enable_all_button, self.disable_all_button, self.save_button):
            button.setEnabled(not busy)
        self.fetch_button.setEnabled(not busy and not self.steam_sync_in_progress)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "cards_layout"):
            QTimer.singleShot(0, self.refresh_cards)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(100, self.refresh_cards)


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


def make_tag(text: str, color: str) -> QLabel:
    tag = QLabel(text)
    tag.setObjectName("tag")
    tag.setStyleSheet(f"#tag {{ background: {color}; }}")
    return tag


def make_tag_button(text: str, color: str, tooltip: str, handler) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("tagButton")
    button.setToolTip(tooltip)
    button.setStyleSheet(f"#tagButton {{ background: {color}; }}")
    button.clicked.connect(handler)
    return button


def make_preview_pixmap(mod: Mod) -> QPixmap:
    cache_key = mod.image_path or "__placeholder__"
    cached = PREVIEW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if mod.image_path and Path(mod.image_path).exists():
        pixmap = QPixmap(mod.image_path)
        if not pixmap.isNull():
            result = pixmap.scaled(ui(188), ui(104), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            PREVIEW_CACHE[cache_key] = result
            return result
    pixmap = QPixmap(ui(188), ui(104))
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


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon())
    window = MainWindow()
    window.show()
    return app.exec_()
