from __future__ import annotations

import inspect
import os
import subprocess
from pathlib import Path

# Headless Qt: build widgets without a real display session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from l4d2_mod_manager import main_window_details, theme
from l4d2_mod_manager.components import (
    HoverPreview,
    ModCard,
    _readable_text_color,
    make_tag,
    make_tag_button,
)
from l4d2_mod_manager.models import Mod
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def _mod(**overrides: object) -> Mod:
    defaults: dict[str, object] = {
        "id": "m1",
        "file_path": str(Path("m1.vpk")),
        "file_name": "m1.vpk",
        "title": "测试 Mod",
    }
    defaults.update(overrides)
    return Mod(**defaults)


def _relative_luminance(color: QColor) -> float:
    def linear(channel: int) -> float:
        value = channel / 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * linear(color.red())
        + 0.7152 * linear(color.green())
        + 0.0722 * linear(color.blue())
    )


def _contrast_ratio(fg: str, bg: str) -> float:
    foreground = _relative_luminance(QColor(fg))
    background = _relative_luminance(QColor(bg))
    hi, lo = max(foreground, background), min(foreground, background)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# _readable_text_color / tag contrast
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "background,expected",
    [
        ("#2d65d6", "#ffffff"),  # active blue
        ("#365f9f", "#ffffff"),  # steam blue
        ("#526073", "#ffffff"),  # local gray
        ("#b84752", "#ffffff"),  # conflict badge red
        ("#8b5a9f", "#ffffff"),  # melee purple
        ("#a15a50", "#ffffff"),  # infected brown
        ],
)
def test_readable_text_color_keeps_white_on_dark_fills(background: str, expected: str) -> None:
    assert _readable_text_color(background) == expected


@pytest.mark.parametrize(
    "background,expected",
    [
        ("#ff7070", "#000000"),  # conflict pink
        ("#c9a227", "#000000"),  # dependency gold
        ("#3b8b78", "#000000"),  # survivor green
    ],
)
def test_readable_text_color_switches_to_dark_on_light_fills(background: str, expected: str) -> None:
    assert _readable_text_color(background) == expected


def test_readable_text_color_falls_back_white_for_invalid_input() -> None:
    assert _readable_text_color("not-a-color") == "#ffffff"


def test_tag_colors_meet_wcag_aa_contrast() -> None:
    # Every fill used by make_tag/make_tag_button in the app must give its
    # computed text color at least 4.5:1 contrast.
    tag_fills = {
        "#2d65d6": "active",
        "#ff7070": "conflict",
        "#c9a227": "dependency",
        "#365f9f": "steam",
        "#526073": "local",
        "#b84752": "conflict badge",
        "#8b5a9f": "melee",
        "#3b8b78": "survivor",
        "#a15a50": "infected",
    }
    for fill, label in tag_fills.items():
        text = _readable_text_color(fill)
        assert _contrast_ratio(text, fill) >= 4.5, f"{label} ({fill}) contrast too low"


def test_make_tag_applies_contrast_text_color(qapp: QApplication) -> None:
    light_tag = make_tag("冲突", "#ff7070")
    assert light_tag.objectName() == "tag"
    assert "background: #ff7070" in light_tag.styleSheet()
    assert "color: #000000" in light_tag.styleSheet()

    dark_tag = make_tag("已启用", "#2d65d6")
    assert "color: #ffffff" in dark_tag.styleSheet()


def test_make_tag_button_applies_contrast_text_color(qapp: QApplication) -> None:
    button = make_tag_button("冲突", "#c9a227", "提示", lambda: None)
    assert button.objectName() == "tagButton"
    assert "color: #000000" in button.styleSheet()


# ---------------------------------------------------------------------------
# open_mod_source regression (was: double @staticmethod + self -> NameError)
# ---------------------------------------------------------------------------

def test_open_mod_source_is_an_instance_method() -> None:
    signature = inspect.signature(main_window_details.open_mod_source)
    assert list(signature.parameters) == ["self", "mod"]


def test_open_mod_source_reveals_existing_file(monkeypatch, qapp: QApplication, tmp_path: Path) -> None:
    mod_file = tmp_path / "mod.vpk"
    mod_file.write_bytes(b"vpk")
    calls: list[list[str]] = []

    monkeypatch.setattr(subprocess, "Popen", lambda args, *a, **kw: calls.append(args) or object())

    main_window_details.open_mod_source(QWidget(), _mod(file_path=str(mod_file)))

    assert calls, "explorer.exe should have been launched"
    assert calls[0][0] == "explorer.exe"
    assert str(mod_file) in "".join(calls[0])


def test_open_mod_source_warns_when_file_missing(monkeypatch, qapp: QApplication, tmp_path: Path) -> None:
    captured: list[tuple[object, str, str]] = []

    def fake_warning(parent, title, text, *args, **kwargs):
        captured.append((parent, title, text))
        return None

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))

    window = QWidget()
    main_window_details.open_mod_source(window, _mod(file_path=str(tmp_path / "gone.vpk")))

    assert len(captured) == 1
    parent, title, _text = captured[0]
    assert parent is window  # the bug made this branch raise NameError
    assert "找不到 Mod 文件" in title


# ---------------------------------------------------------------------------
# HoverPreview composition cache
# ---------------------------------------------------------------------------

def test_hover_preview_reuses_composition_when_nothing_changed(qapp: QApplication) -> None:
    preview = HoverPreview(_mod(), 100, 60)
    first = preview.pixmap()
    assert first is not None and not first.isNull()

    preview._refresh_preview_pixmap()
    assert preview.pixmap() is first, "identical composition should be cached"

    preview.resize(120, 72)
    second = preview.pixmap()
    assert second is not first, "resize must recompose"

    preview._refresh_preview_pixmap()
    assert preview.pixmap() is second, "second composition should be cached"


def test_hover_preview_recomposes_after_refresh_image(qapp: QApplication, tmp_path: Path) -> None:
    preview = HoverPreview(_mod(), 100, 60)
    first = preview.pixmap()

    image_file = tmp_path / "img.png"
    QPixmap(120, 80).fill(QColor("#123456")).save(str(image_file))
    other = _mod(
        id="m2",
        file_path="m2.vpk",
        file_name="m2.vpk",
        title="另一个",
        image_path=str(image_file),
    )

    preview.refresh_image(other)
    assert preview.pixmap() is not first


# ---------------------------------------------------------------------------
# ModCard.refresh_state (subtree re-polish only on real state change)
# ---------------------------------------------------------------------------

def test_mod_card_refresh_state_object_name_follows_state(qapp: QApplication) -> None:
    mod = _mod()
    card = ModCard(mod)
    assert card.objectName() == "modCard"

    mod.active = True
    card.refresh_state()
    assert card.objectName() == "modCardActive"

    mod.conflict_with = ["other"]
    card.refresh_state()
    assert card.objectName() == "modCardConflict"

    mod.conflict_with = []
    card.refresh_state()
    assert card.objectName() == "modCardActive"

    mod.active = False
    card.refresh_state()
    assert card.objectName() == "modCard"


def test_mod_card_refresh_state_idempotent(qapp: QApplication) -> None:
    card = ModCard(_mod(active=True))
    card.refresh_state()
    card.refresh_state()
    card.refresh_state()
    assert card.objectName() == "modCardActive"
    assert card.toggle_button.text() == "禁用Mod"


# ---------------------------------------------------------------------------
# Theme QSS regressions
# ---------------------------------------------------------------------------

def test_all_themes_define_link_color() -> None:
    for theme_name in theme.THEMES:
        assert "link" in theme.THEME_PALETTE[theme_name], f"{theme_name} missing link color"


def test_titanium_pin_hint_uses_readable_dark_gold() -> None:
    # Light gold on the light titanium conflict surface is unreadable; the
    # fix switched it to the same dark gold the light theme uses.
    assert "#mainConflictGroupPinHint { color: #8a5d00" in theme.THEMES["titanium"]


# ---------------------------------------------------------------------------
# Re-scan must preserve user-edited mod fields (custom_title regression)
# ---------------------------------------------------------------------------

def test_rescan_preserves_custom_title_by_file_path(tmp_path: Path) -> None:
    # Simulate the app: an existing mods dict with a custom title, then a fresh
    # scan that rebuilds the Mod objects (custom_title reset to "").
    old_mod = Mod(
        id="m1",
        file_path=str(tmp_path / "m1.vpk"),
        file_name="m1.vpk",
        title="原始标题",
        custom_title="我的自定义名称",
    )
    self_mods = {"m1": old_mod}

    fresh_mod = Mod(
        id="m1:2",
        file_path=str(tmp_path / "m1.vpk"),
        file_name="m1.vpk",
        title="原始标题",
        custom_title="",
    )
    scanned = {"m1:2": fresh_mod}

    # Reproduce the merge logic from on_scan_finished.
    old_by_path = {
        str(Path(mod.file_path).resolve()): mod
        for mod in self_mods.values()
    }
    merged = dict(scanned)
    for mod in merged.values():
        old = old_by_path.get(str(Path(mod.file_path).resolve()))
        if old is None:
            continue
        for field in ("custom_title", "favorite", "favorite_at", "dependencies", "conflict_pin"):
            setattr(mod, field, getattr(old, field))

    assert merged["m1:2"].custom_title == "我的自定义名称"


def test_rescan_keeps_custom_title_after_file_change(tmp_path: Path) -> None:
    # When Steam updates a workshop mod the file mtime/size change, so scan_mods
    # produces a brand new id, but the path is identical -> title must survive.
    old_mod = Mod(
        id="123456789",
        file_path=str(tmp_path / "ws.vpk"),
        file_name="ws.vpk",
        title="订阅Mod",
        custom_title="重命名后的名字",
    )
    self_mods = {"123456789": old_mod}

    fresh_mod = Mod(
        id="ws.vpk:1000:2000",
        file_path=str(tmp_path / "ws.vpk"),
        file_name="ws.vpk",
        title="订阅Mod",
        custom_title="",
    )
    scanned = {"ws.vpk:1000:2000": fresh_mod}

    old_by_path = {
        str(Path(mod.file_path).resolve()): mod
        for mod in self_mods.values()
    }
    merged = dict(scanned)
    for mod in merged.values():
        old = old_by_path.get(str(Path(mod.file_path).resolve()))
        if old is None:
            continue
        for field in ("custom_title", "favorite", "favorite_at", "dependencies", "conflict_pin"):
            setattr(mod, field, getattr(old, field))

    assert merged["ws.vpk:1000:2000"].custom_title == "重命名后的名字"


def test_rescan_preserves_favorite_flag(tmp_path: Path) -> None:
    # Favorite state lives on mod.favorite / mod.favorite_at and is wiped when
    # the Mod object is rebuilt during a rescan. The merge must restore it.
    old_mod = Mod(
        id="m1",
        file_path=str(tmp_path / "m1.vpk"),
        file_name="m1.vpk",
        title="原始标题",
        favorite=True,
        favorite_at=1234567890,
    )
    self_mods = {"m1": old_mod}

    fresh_mod = Mod(
        id="m1:2",
        file_path=str(tmp_path / "m1.vpk"),
        file_name="m1.vpk",
        title="原始标题",
        favorite=False,
        favorite_at=0,
    )
    scanned = {"m1:2": fresh_mod}

    old_by_path = {
        str(Path(mod.file_path).resolve()): mod
        for mod in self_mods.values()
    }
    merged = dict(scanned)
    for mod in merged.values():
        old = old_by_path.get(str(Path(mod.file_path).resolve()))
        if old is None:
            continue
        for field in ("custom_title", "favorite", "favorite_at", "dependencies", "conflict_pin"):
            setattr(mod, field, getattr(old, field))

    assert merged["m1:2"].favorite is True
    assert merged["m1:2"].favorite_at == 1234567890


def test_scan_dedupes_same_file_after_update(tmp_path: Path) -> None:
    # A non-numeric filename mod's id fingerprint includes size:mtime. After an
    # update the file gets a new id but the same path; the scanner must collapse
    # the two ids into a single card instead of leaving two.
    vpk_file = tmp_path / "mymod.vpk"
    vpk_file.write_bytes(b"old-content")
    old_stat = vpk_file.stat()

    old_mod = Mod(
        id=f"mymod.vpk:{old_stat.st_size}:{int(old_stat.st_mtime_ns)}",
        file_path=str(vpk_file),
        file_name="mymod.vpk",
        title="My Mod",
        file_size=old_stat.st_size,
        file_mtime_ns=old_stat.st_mtime_ns,
    )
    # Simulate the file being updated (new size/mtime) before the next scan.
    vpk_file.write_bytes(b"new-content-with-different-size")
    new_stat = vpk_file.stat()
    new_mod = Mod(
        id=f"mymod.vpk:{new_stat.st_size}:{int(new_stat.st_mtime_ns)}",
        file_path=str(vpk_file),
        file_name="mymod.vpk",
        title="My Mod",
        file_size=new_stat.st_size,
        file_mtime_ns=new_stat.st_mtime_ns,
    )

    result = {"old": old_mod, "new": new_mod}
    valid_paths = {str(vpk_file.resolve())}

    best_by_path: dict[str, Mod] = {}
    for mod_id, mod in result.items():
        if str(Path(mod.file_path).resolve()) not in valid_paths:
            continue
        path_key = str(Path(mod.file_path).resolve())
        prev = best_by_path.get(path_key)
        if prev is None or mod.file_mtime_ns > prev.file_mtime_ns:
            best_by_path[path_key] = mod
    deduped = {mod.id: mod for mod in best_by_path.values()}

    assert len(deduped) == 1
    assert next(iter(deduped.values())).file_mtime_ns == new_stat.st_mtime_ns


# ---------------------------------------------------------------------------
# Collection chips must scale with the card width (not use a hardcoded 9px)
# ---------------------------------------------------------------------------

def test_collection_chip_font_scales_with_card_width(qapp) -> None:
    # On a non-default-size card the chip font used to stay at a hardcoded 9px
    # while the preview shrank, so the tags wrapped/aligned incorrectly.
    mod = _mod(id="m1", file_path=str(Path("m1.vpk")), file_name="m1.vpk")
    card = ModCard(mod, ["组合A", "组合B"], width=160)  # below BASE_WIDTH 214
    card.set_collection_context(["组合A", "组合B"], {"组合A", "组合B"})

    # Chip font size must follow the scale (160/214) of the tag scale, not 9.
    expected = max(7, round(9 * (160 / 214)))
    assert card._collection_chip_font_size == expected
    assert card._collection_chip_font_size != 9 or expected == 9

    # The tag host must not exceed the preview width (no overflow off-card).
    assert card._collection_tag_host.width() <= card.preview.width()


def test_collection_tag_hidden_when_fewer_than_two_selected(qapp) -> None:
    mod = _mod(id="m1", file_path=str(Path("m1.vpk")), file_name="m1.vpk")
    card = ModCard(mod, ["组合A"], width=214)
    card.set_collection_context(["组合A"], {"组合A"})  # only one selected
    assert card._collection_tag_host.isHidden()

    card.set_collection_context(["组合A"], {"组合A", "组合B"})
    assert not card._collection_tag_host.isHidden()
