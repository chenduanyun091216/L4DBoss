from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtWidgets import QDialog, QFrame, QGraphicsOpacityEffect, QLabel, QVBoxLayout

from .custom_mod import (
    CUSTOM_MOD_FILENAME,
    CustomModPublishDialog,
    CustomModPage,
    build_custom_vpk,
    custom_mod_filename,
    custom_mod_preview_filename,
    validate_custom_vpk,
    write_custom_preview,
)
from .components import AppInputBox, AppMessageBox
from .theme import BACKGROUND_IMAGE, theme_color, ui

# Do not fall back to native Windows prompts inside the themed custom-Mod
# flow. These aliases retain the familiar QMessageBox/QInputDialog call sites
# while routing every prompt to the application's frameless components.
QMessageBox = AppMessageBox
QInputDialog = AppInputBox


class CustomModEditorDialog(QDialog):
    """Focused, theme-aware container for the custom-Mod editor."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("customModEditorDialog")
        self.setWindowTitle("自定义 Mod")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumSize(ui(760), ui(490))
        self.resize(ui(840), ui(550))
        self.generated = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.content_layout = layout

    def set_editor(self, editor: CustomModPage) -> None:
        self.content_layout.addWidget(editor, 1)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        image = QPixmap(str(BACKGROUND_IMAGE))
        if not image.isNull():
            painter.drawPixmap(self.rect(), image)
        veil = QColor(theme_color("surface")); veil.setAlpha(58)
        painter.fillRect(self.rect(), veil)
        painter.end()
        super().paintEvent(event)


def _return_from_custom_mod(self, refresh: bool = False) -> None:
    _set_custom_focus(self, False)
    self._content_mode = "detail"
    self.show_mod_list()
    if refresh:
        self.scan_mods(True)


def _set_custom_focus(self, enabled: bool) -> None:
    """Dim non-editor controls while the inline editor is open."""
    for widget in (getattr(self, "sidebar", None), getattr(self, "content_bar", None), getattr(self, "pagination_bar", None)):
        if widget is None:
            continue
        if enabled:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.38)
            widget.setGraphicsEffect(effect)
            widget.setEnabled(False)
        else:
            widget.setGraphicsEffect(None)
            widget.setEnabled(True)


def open_custom_mod_page(self) -> None:
    addon_dirs = self.configured_addon_directories()
    if not addon_dirs:
        QMessageBox.information(self, "需要选择游戏", "请先选择 left4dead2.exe。")
        return

    dialog = CustomModEditorDialog(self)
    page = CustomModPage(
        dialog,
        self.settings.get("custom_mod_values", {}),
        self.settings.get("custom_mod_presets", {}),
    )

    def refresh_presets(selected: str = "") -> None:
        presets = self.settings.get("custom_mod_presets", {})
        page.presets = presets
        page.preset_combo.blockSignals(True)
        page.preset_combo.clear()
        page.preset_combo.addItem("当前配置", "")
        page.preset_combo.addItems(sorted(presets))
        page.preset_combo.setCurrentText(selected if selected in presets else "当前配置")
        page.preset_combo.blockSignals(False)

    def save_preset() -> None:
        name = page.preset_name.text().strip()
        if not name:
            QMessageBox.information(self, "保存预设", "请输入预设名称。")
            return
        presets = self.settings.setdefault("custom_mod_presets", {})
        presets[name] = page.current_values()
        self.storage.save_settings(self.settings)
        refresh_presets(name)
        QMessageBox.information(self, "保存预设", f"预设“{name}”已保存，尚未生成 Mod。")

    def rename_preset() -> None:
        old_name = page.preset_combo.currentText()
        if old_name == "当前配置":
            old_name = ""
        presets = self.settings.get("custom_mod_presets", {})
        if not old_name or old_name not in presets:
            QMessageBox.information(self, "重命名预设", "请先在下拉框中选择一个已保存的预设。")
            return
        new_name, accepted = QInputDialog.getText(dialog, "重命名预设", "新名称：", text=old_name)
        new_name = new_name.strip()
        if not accepted or new_name == old_name:
            return
        if not new_name:
            QMessageBox.information(self, "重命名预设", "预设名称不能为空。")
            return
        if new_name in presets:
            QMessageBox.warning(self, "重命名预设", f"预设“{new_name}”已存在。")
            return
        presets[new_name] = presets.pop(old_name)
        self.storage.save_settings(self.settings)
        refresh_presets(new_name)

    def delete_preset() -> None:
        name = page.preset_combo.currentText()
        if name == "当前配置":
            name = ""
        presets = self.settings.get("custom_mod_presets", {})
        if not name or name not in presets:
            QMessageBox.information(self, "删除预设", "请先在下拉框中选择一个已保存的预设。")
            return
        answer = QMessageBox.question(dialog, "删除预设", f"确定删除预设“{name}”吗？")
        if answer != QMessageBox.Yes:
            return
        presets.pop(name, None)
        self.storage.save_settings(self.settings)
        refresh_presets()

    def generate_mod() -> None:
        values = page.collected_values()
        addons_root = addon_dirs[0]
        previous_filename = self.settings.get("custom_mod_filename", CUSTOM_MOD_FILENAME)
        output = addons_root / previous_filename
        publish = None
        if values:
            publish = CustomModPublishDialog(self, self.settings.get("custom_mod_display_name", "自定义 Mod"))
            if publish.exec_() != publish.Accepted:
                return
        try:
            if values:
                filename = custom_mod_filename(publish.mod_name)
                preview_filename = custom_mod_preview_filename(filename)
                destination = addons_root / filename
                if filename != previous_filename and destination.exists():
                    answer = QMessageBox.question(
                        self, "文件已存在", f"{filename} 已存在于 addons 文件夹。是否覆盖它？"
                    )
                    if answer != QMessageBox.Yes:
                        return
                generated = build_custom_vpk(addons_root, values, filename)
                written = validate_custom_vpk(generated, values)
                write_custom_preview(addons_root, publish.mod_name, publish.image_path, preview_filename)
                if previous_filename != filename:
                    old_vpk = addons_root / previous_filename
                    old_preview = addons_root / custom_mod_preview_filename(previous_filename)
                    if old_vpk.exists():
                        old_vpk.unlink()
                    if old_preview.exists():
                        old_preview.unlink()
                self.settings["custom_mod_values"] = values
                self.settings["custom_mod_display_name"] = publish.mod_name
                self.settings["custom_mod_filename"] = filename
            else:
                if output.exists():
                    output.unlink()
                old_preview = addons_root / custom_mod_preview_filename(previous_filename)
                if old_preview.exists():
                    old_preview.unlink()
                self.settings.pop("custom_mod_values", None)
                self.settings.pop("custom_mod_display_name", None)
                self.settings.pop("custom_mod_filename", None)
            self.storage.save_settings(self.settings)
            self.write_addonlist()
            dialog.generated = True
            for mod in self.mods.values():
                if Path(mod.file_path).name.casefold() == self.settings.get("custom_mod_filename", CUSTOM_MOD_FILENAME).casefold():
                    mod.custom_title = publish.mod_name if publish else ""
                    self.storage.save_mods(self.mods)
                    break
            detail = "\n".join(written) if values else ""
            QMessageBox.information(self, "自定义 Mod", ("已生成并安装自定义 Mod。\n\n写入文件：\n" + detail) if values else "已恢复默认，不再启用自定义 Mod。")
            dialog.accept()
        except Exception as exc:
            QMessageBox.critical(self, "生成失败", f"无法生成自定义 Mod：{exc}")

    page.save_preset_button.clicked.connect(save_preset)
    page.rename_preset_button.clicked.connect(rename_preset)
    page.delete_preset_button.clicked.connect(delete_preset)
    page.generate_button.clicked.connect(generate_mod)
    dialog.set_editor(page)
    page.back_button.clicked.connect(dialog.reject)
    dialog.exec_()
    if dialog.generated:
        self.scan_mods(True)
