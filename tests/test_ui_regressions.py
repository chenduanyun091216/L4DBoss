from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEventLoop, QTimer
from PyQt5.QtWidgets import QApplication

from l4d2_mod_manager import main_window as main_window_module
from l4d2_mod_manager import main_window_mods as main_window_mods_module
from l4d2_mod_manager.components import EditModInfoDialog, ModCard, make_preview_pixmap
from l4d2_mod_manager.custom_mod import CustomModPublishDialog, write_custom_preview
from l4d2_mod_manager.custom_mod import CustomModPage
from l4d2_mod_manager.custom_mod_page import CustomModEditorDialog
from l4d2_mod_manager.models import Mod
from l4d2_mod_manager.storage import AppStorage
from l4d2_mod_manager.theme import PREVIEW_CACHE, PREVIEW_CACHE_LIMIT, ui


class UiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, root: Path):
        with patch.object(main_window_module, "USER_DATA_ROOT", root / "appdata"):
            window = main_window_module.MainWindow()
        game_root = root / "game"
        addons = game_root / "left4dead2" / "addons"
        addons.mkdir(parents=True)
        exe = game_root / "left4dead2.exe"
        exe.touch()
        window.settings["game_exe"] = str(exe)
        window.show()
        self.app.processEvents()
        return window

    def test_fixed_header_filter_geometry_survives_resize(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            window = self._window(Path(temp))
            try:
                window._sync_content_right_edges(force=True)
                self.app.processEvents()
                expected_combo = ui(168)
                expected_search = expected_combo * 2 + window.cards_layout.horizontalSpacing()
                self.assertEqual(window.collection_combo.width(), expected_combo)
                self.assertEqual(window.search_box.width(), expected_search)
                widths = [button.width() for button in window._header_action_buttons]
                self.assertLessEqual(max(widths) - min(widths), 1)
                self.assertEqual(window.custom_mod_button.width(), max(widths))
                first = window._header_action_buttons[0]
                last = window._header_action_buttons[-1]
                self.assertEqual(first.mapToGlobal(first.rect().topLeft()).x(), window.search_box.mapToGlobal(window.search_box.rect().topLeft()).x())
                self.assertEqual(last.mapToGlobal(last.rect().topRight()).x(), window.search_box.mapToGlobal(window.search_box.rect().topRight()).x())
                self.assertEqual(window.custom_mod_button.mapToGlobal(window.custom_mod_button.rect().topLeft()).x(), window.collection_combo.mapToGlobal(window.collection_combo.rect().topLeft()).x())

                window.resize(ui(1450), ui(850))
                self.app.processEvents()
                window._sync_content_right_edges(force=True)
                self.assertEqual(window.collection_combo.width(), expected_combo)
                self.assertEqual(window.search_box.width(), expected_search)
            finally:
                window.close()

    def test_scan_dispatch_keeps_ui_event_loop_responsive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            window = self._window(Path(temp))
            beats = []

            def slow_scan(*_args):
                time.sleep(0.25)
                return {}, {}

            heartbeat = QTimer()
            heartbeat.setInterval(20)
            heartbeat.timeout.connect(lambda: beats.append(time.monotonic()))
            loop = QEventLoop()
            watcher = QTimer()
            watcher.setInterval(20)
            watcher.timeout.connect(lambda: loop.quit() if window.refresh_button.isEnabled() else None)
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            try:
                with patch.object(main_window_mods_module, "_scan_mods_with_conflict_paths", slow_scan):
                    started = time.monotonic()
                    window.scan_mods(False)
                    dispatch_time = time.monotonic() - started
                    heartbeat.start()
                    watcher.start()
                    timeout.start(2000)
                    loop.exec_()
                self.assertLess(dispatch_time, 0.1)
                self.assertGreaterEqual(len(beats), 5)
                self.assertTrue(window.refresh_button.isEnabled())
            finally:
                heartbeat.stop()
                watcher.stop()
                window.close()

    def test_steam_stop_text_and_per_theme_icons(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            window = self._window(Path(temp))
            try:
                window._set_steam_stop_mode(True)
                self.assertEqual(window.fetch_button.text(), "停止")
                self.assertTrue(window.fetch_button.property("stopMode"))
                self.assertFalse(window.fetch_button.icon().isNull())
                window._set_steam_stop_mode(False)
                self.assertEqual(window.fetch_button.text(), "同步Steam")

                icon_keys = []
                for theme_name in ("dark", "light", "titanium"):
                    if window._theme != theme_name:
                        window._set_theme(theme_name)
                    self.app.processEvents()
                    icon_keys.append(window.theme_button.icon().cacheKey())
                self.assertEqual(len(set(icon_keys)), 3)
            finally:
                window.close()

    def test_create_mod_dialog_opens_and_closes_without_native_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            window = self._window(Path(temp))
            opened = []

            def close_editor() -> None:
                dialogs = [
                    w for w in self.app.topLevelWidgets()
                    if isinstance(w, CustomModEditorDialog) and w.isVisible()
                ]
                if dialogs:
                    opened.append(True)
                    dialogs[0].reject()

            try:
                QTimer.singleShot(80, close_editor)
                window.open_custom_mod_dialog()
                self.assertTrue(opened)
            finally:
                window.close()

    def test_create_mod_publish_nested_dialog_opens_without_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            window = self._window(Path(temp))
            opened = []

            def close_publish() -> None:
                dialogs = [
                    w for w in self.app.topLevelWidgets()
                    if isinstance(w, CustomModPublishDialog) and w.isVisible()
                ]
                if dialogs:
                    opened.append("publish")
                    dialogs[0].reject()

            def close_editor() -> None:
                dialogs = [
                    w for w in self.app.topLevelWidgets()
                    if isinstance(w, CustomModEditorDialog) and w.isVisible()
                ]
                if dialogs:
                    dialogs[0].reject()

            def drive_editor() -> None:
                dialogs = [
                    w for w in self.app.topLevelWidgets()
                    if isinstance(w, CustomModEditorDialog) and w.isVisible()
                ]
                if not dialogs:
                    return
                page = dialogs[0].findChild(CustomModPage)
                control = next(
                    widget for controls in page._controls.values() for widget in controls.values()
                    if hasattr(widget, "value") and hasattr(widget, "setValue")
                )
                control.setValue(min(control.maximum(), control.value() + 1))
                QTimer.singleShot(80, close_publish)
                QTimer.singleShot(180, close_editor)
                page.generate_button.click()

            try:
                QTimer.singleShot(80, drive_editor)
                window.open_custom_mod_dialog()
                self.assertEqual(opened, ["publish"])
            finally:
                window.close()

    def test_custom_tags_are_leftmost_reusable_and_outlined(self) -> None:
        mod = Mod(
            id="1", file_path="one.vpk", file_name="one.vpk", title="One",
            categories=["scripts"], manual_tags=["我的标签"],
        )
        dialog = EditModInfoDialog(mod, ["scripts", "我的标签", "复用标签"])
        try:
            items = [dialog._chips_layout.itemAt(i).widget() for i in range(dialog._chips_layout.count())]
            first_visible = next(widget for widget in items if not widget.isHidden())
            self.assertEqual(first_visible.property("cid"), "我的标签")
            self.assertIn("2px solid", first_visible.styleSheet())
            self.assertIn("复用标签", dialog._new_tag_edit._tag_model.stringList())

            card = ModCard(mod)
            try:
                html = card.type_summary_label.text()
                self.assertLess(html.index("我的标签"), html.index("tags:"))
            finally:
                card.deleteLater()
        finally:
            dialog.reject()

    def test_publish_placeholder_and_generated_cover(self) -> None:
        dialog = CustomModPublishDialog(initial_name="测试封面")
        try:
            self.assertEqual(dialog.image_edit.placeholderText(), "未选择时自动生成封面")
            self.assertNotEqual(dialog.image_edit.palette().placeholderText().color().value(), 0)
        finally:
            dialog.reject()
        with tempfile.TemporaryDirectory() as temp:
            target = write_custom_preview(Path(temp), "测试封面")
            self.assertTrue(target.exists())
            self.assertGreater(target.stat().st_size, 1000)

    def test_preview_cache_is_bounded(self) -> None:
        PREVIEW_CACHE.clear()
        for index in range(PREVIEW_CACHE_LIMIT + 20):
            mod = Mod(
                id=str(index), file_path=f"{index}.vpk", file_name=f"{index}.vpk",
                title=str(index), image_path=f"missing-{index}.png",
            )
            make_preview_pixmap(mod, 32, 18)
        self.assertLessEqual(len(PREVIEW_CACHE), PREVIEW_CACHE_LIMIT)

    def test_storage_writes_valid_json_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            storage = AppStorage(Path(temp))
            payload = {"theme": "dark", "values": list(range(1000))}
            storage.save_settings(payload)
            self.assertEqual(storage.load_settings(), payload)
            self.assertFalse(storage.settings_file.with_name(".settings.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
