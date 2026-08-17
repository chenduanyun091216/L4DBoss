from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPolygon
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QStackedWidget, QStyle, QStyleOptionComboBox, QStyleOptionSpinBox, QTabWidget, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
    QVBoxLayout, QWidget,
)

import vpk

from .theme import theme_color, ui


CUSTOM_MOD_FILENAME = "L4DBoss_CustomMod.vpk"
CUSTOM_MOD_PREVIEW_FILENAME = "L4DBoss_CustomMod.jpg"


class StyledComboBox(QComboBox):
    """Combo box with a crisp, theme-aware chevron instead of platform arrows."""
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        option = QStyleOptionComboBox(); self.initStyleOption(option)
        rect = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, self)
        center = rect.center()
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        pen = painter.pen(); pen.setColor(QColor(theme_color("menu_text"))); pen.setWidth(ui(2)); painter.setPen(pen)
        painter.drawPolyline(QPolygon([
            center + QPoint(-ui(4), -ui(2)), center + QPoint(0, ui(2)), center + QPoint(ui(4), -ui(2)),
        ]))
        painter.end()


class _StyledSpinArrows:
    def _paint_arrows(self) -> None:
        option = QStyleOptionSpinBox(); self.initStyleOption(option)
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing); painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_color("menu_text")))
        for control, up in ((QStyle.SC_SpinBoxUp, True), (QStyle.SC_SpinBoxDown, False)):
            rect = self.style().subControlRect(QStyle.CC_SpinBox, option, control, self)
            center = rect.center(); half = ui(4)
            points = ([center + QPoint(-half, ui(2)), center + QPoint(half, ui(2)), center + QPoint(0, -ui(3))]
                      if up else [center + QPoint(-half, -ui(2)), center + QPoint(half, -ui(2)), center + QPoint(0, ui(3))])
            painter.drawPolygon(QPolygon(points))
        painter.end()


class StyledSpinBox(_StyledSpinArrows, QSpinBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event); self._paint_arrows()


class StyledDoubleSpinBox(_StyledSpinArrows, QDoubleSpinBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event); self._paint_arrows()


def custom_mod_filename(mod_name: str) -> str:
    """Return a safe, user-facing VPK filename for a published custom Mod."""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (mod_name or "").strip())
    stem = stem.rstrip(". ")
    if stem.casefold().endswith(".vpk"):
        stem = stem[:-4].rstrip(". ")
    return f"{stem or '自定义 Mod'}.vpk"


def custom_mod_preview_filename(vpk_filename: str) -> str:
    return f"{Path(vpk_filename).stem}.jpg"


@dataclass(frozen=True)
class WeaponSpec:
    key: str
    label: str
    script_name: str
    damage: int
    clip_size: int
    ammo_max: int  # -1 means the game default is unlimited.
    cycle_time: float
    recoil: bool
    range: int
    range_modifier: float
    reload_duration: float
    deploy_duration: float


@dataclass(frozen=True)
class MeleeSpec:
    key: str
    label: str
    damage: int = 36
    attack_speed: float = .175
    attack_range: int = 70
    push_force: int = 0
    attack_interval: float = .175
    continuous: bool = False


WEAPONS = (
    WeaponSpec("pistol", "普通手枪", "weapon_pistol.txt", 36, 15, -1, .175, True, 3000, .75, 1.9, .5),
    WeaponSpec("pistol_magnum", "马格南", "weapon_pistol_magnum.txt", 80, 8, 150, .3, True, 3500, .75, 2.0, .5),
    WeaponSpec("smg", "冲锋枪", "weapon_smg.txt", 20, 50, 650, .06, True, 3000, .84, 2.35, .5),
    WeaponSpec("smg_silenced", "消音冲锋枪", "weapon_smg_silenced.txt", 24, 50, 650, .055, True, 3000, .84, 2.35, .5),
    WeaponSpec("smg_mp5", "MP5", "weapon_smg_mp5.txt", 24, 50, 650, .055, True, 3000, .84, 2.35, .5),
    WeaponSpec("rifle", "M16", "weapon_rifle.txt", 33, 50, 360, .0875, True, 3000, .97, 2.35, .5),
    WeaponSpec("rifle_ak47", "AK47", "weapon_rifle_ak47.txt", 58, 40, 360, .13, True, 3000, .97, 2.35, .5),
    WeaponSpec("rifle_desert", "三连发步枪", "weapon_rifle_desert.txt", 44, 60, 360, .175, True, 3000, .97, 2.35, .5),
    WeaponSpec("rifle_sg552", "SG552", "weapon_rifle_sg552.txt", 33, 50, 360, .0875, True, 3000, .97, 2.35, .5),
    WeaponSpec("rifle_m60", "M60", "weapon_rifle_m60.txt", 50, 150, 0, .1, True, 3000, .97, 2.5, .5),
    WeaponSpec("hunting_rifle", "猎枪", "weapon_hunting_rifle.txt", 90, 15, 150, .25, True, 8000, .97, 2.4, .5),
    WeaponSpec("sniper_military", "军用狙击枪", "weapon_sniper_military.txt", 90, 30, 180, .3, True, 8000, .97, 2.8, .5),
    WeaponSpec("sniper_scout", "Scout", "weapon_sniper_scout.txt", 115, 15, 150, 1.25, True, 8000, .97, 2.8, .5),
    WeaponSpec("sniper_awp", "AWP", "weapon_sniper_awp.txt", 115, 20, 150, 1.5, True, 8000, .97, 2.8, .5),
    WeaponSpec("autoshotgun", "自动霰弹枪", "weapon_autoshotgun.txt", 20, 10, 90, .25, True, 3000, .7, 4.5, .5),
    WeaponSpec("shotgun_spas", "SPAS", "weapon_shotgun_spas.txt", 28, 10, 90, .25, True, 3000, .7, 4.5, .5),
    WeaponSpec("pumpshotgun", "泵动霰弹枪", "weapon_pumpshotgun.txt", 25, 8, 56, .5, True, 3000, .7, 5.5, .5),
    WeaponSpec("shotgun_chrome", "Chrome 霰弹枪", "weapon_shotgun_chrome.txt", 31, 8, 56, .5, True, 3000, .7, 5.5, .5),
    WeaponSpec("grenade_launcher", "榴弹发射器", "weapon_grenade_launcher.txt", 400, 1, 30, 2.4, False, 3000, .97, 2.4, .5),
)

MELEE = (
    MeleeSpec("baseball_bat", "棒球棍"), MeleeSpec("cricket_bat", "板球棍"),
    MeleeSpec("crowbar", "撬棍"), MeleeSpec("electric_guitar", "电吉他"),
    MeleeSpec("fireaxe", "消防斧"), MeleeSpec("frying_pan", "平底锅"),
    MeleeSpec("golfclub", "高尔夫球杆"), MeleeSpec("katana", "武士刀"),
    MeleeSpec("machete", "砍刀"), MeleeSpec("pitchfork", "干草叉"),
    MeleeSpec("shovel", "铁铲"), MeleeSpec("tonfa", "警棍"), MeleeSpec("knife", "小刀"),
)

WEAPON_GROUPS = (
    ("手枪 Pistols", ("pistol", "pistol_magnum")),
    ("冲锋枪 SMGs", ("smg", "smg_silenced", "smg_mp5")),
    ("步枪 Rifles", ("rifle", "rifle_ak47", "rifle_desert", "rifle_sg552")),
    ("霰弹枪 Shotguns", ("pumpshotgun", "shotgun_chrome", "autoshotgun", "shotgun_spas")),
    ("狙击枪 Snipers", ("hunting_rifle", "sniper_military", "sniper_awp", "sniper_scout")),
    ("特殊武器 Special", ("grenade_launcher", "rifle_m60")),
)


def default_values() -> dict[str, dict[str, object]]:
    values = {
        spec.key: {
            "damage": spec.damage, "clip_size": spec.clip_size, "ammo_max": spec.ammo_max,
            "cycle_time": spec.cycle_time, "recoil": spec.recoil, "range": spec.range,
            "range_modifier": spec.range_modifier, "reload_duration": spec.reload_duration,
            "deploy_duration": spec.deploy_duration,
        }
        for spec in WEAPONS
    }
    values.update({
        spec.key: {
            "damage": spec.damage, "attack_speed": spec.attack_speed,
            "attack_range": spec.attack_range, "push_force": spec.push_force,
            "attack_interval": spec.attack_interval, "continuous": spec.continuous,
        }
        for spec in MELEE
    })
    return values


def _replace_key(text: str, key: str, value: object) -> str:
    pattern = rf'("{re.escape(key)}"\s+")([^"]*)(")'
    if re.search(pattern, text, flags=re.IGNORECASE):
        return re.sub(pattern, lambda m: f"{m.group(1)}{value}{m.group(3)}", text, count=1, flags=re.IGNORECASE)
    return text


def _game_script(addons_root: Path, relative_path: str) -> str | None:
    game_root = addons_root.parent.parent
    package_paths = (
        game_root / "update" / "pak01_dir.vpk",
        game_root / "left4dead2_dlc3" / "pak01_dir.vpk",
        game_root / "left4dead2_dlc2" / "pak01_dir.vpk",
        game_root / "left4dead2_dlc1" / "pak01_dir.vpk",
        addons_root.parent / "pak01_dir.vpk",
    )
    for pak in package_paths:
        if not pak.exists():
            continue
        try:
            package = vpk.open(str(pak))
            if relative_path in package:
                return package[relative_path].read().decode("utf-8", errors="replace")
        except Exception:
            continue
    return None


def _custom_weapon_text(addons_root: Path, spec: WeaponSpec, values: dict[str, object]) -> str:
    text = _game_script(addons_root, f"scripts/{spec.script_name}")
    if text is None:
        raise RuntimeError(f"无法读取游戏原始武器脚本：{spec.script_name}")
    for key, value in {
        "Damage": values["damage"], "clip_size": values["clip_size"],
        "CycleTime": values["cycle_time"], "Range": values["range"],
        "RangeModifier": values["range_modifier"], "ReloadDuration": values["reload_duration"],
        "DeployDuration": values["deploy_duration"],
    }.items():
        text = _replace_key(text, key, value)
    if not bool(values["recoil"]):
        for key in ("VerticalPunch", "SpreadPerShot", "MaxSpread", "SpreadDecay", "MinDuckingSpread", "MinStandingSpread", "MinInAirSpread", "MaxMovementSpread"):
            text = _replace_key(text, key, 0)
    return text


def _custom_melee_text(addons_root: Path, spec: MeleeSpec, values: dict[str, object]) -> str:
    relative = f"scripts/melee/{spec.key}.txt"
    text = _game_script(addons_root, relative)
    if text is None:
        raise RuntimeError(f"无法读取游戏原始近战脚本：{spec.key}.txt")
    for key, value in (
        ("Damage", values["damage"]), ("CycleTime", values["attack_speed"]),
        ("Range", values["attack_range"]), ("PushForce", values["push_force"]),
        ("AttackInterval", values["attack_interval"]), ("ContinuousAttack", 1 if values["continuous"] else 0),
    ):
        text = _replace_key(text, key, value)
    return text


def _changed_weapon_values(values: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    """Merge partial input and retain only weapons whose data differs from stock.

    The editor normally supplies complete weapon dictionaries, but older saved
    presets and external callers may supply every weapon indiscriminately.
    Filtering here is the final safety net that keeps unrelated game scripts
    out of the generated VPK.
    """
    defaults = default_values()
    changed: dict[str, dict[str, object]] = {}
    for key, data in values.items():
        if key not in defaults or not isinstance(data, dict):
            continue
        merged = dict(defaults[key])
        merged.update(data)
        if merged != defaults[key]:
            changed[key] = merged
    return changed


def _ammo_vscript(values: dict[str, dict[str, object]]) -> str:
    cvars = {
        "pistol": "ammo_pistol_max", "pistol_magnum": "ammo_pistol_max",
        "smg": "ammo_smg_max", "smg_silenced": "ammo_smg_max", "smg_mp5": "ammo_smg_max",
        "rifle": "ammo_assaultrifle_max", "rifle_ak47": "ammo_assaultrifle_max",
        "rifle_desert": "ammo_assaultrifle_max", "rifle_sg552": "ammo_assaultrifle_max",
        # The M60 has its own pool. Mapping it to assault-rifle ammo made its
        # stock value of 0 overwrite every rifle reserve pool, leaving them
        # with a single magazine and no reload reserve.
        "rifle_m60": "ammo_m60_max", "hunting_rifle": "ammo_huntingrifle_max",
        "sniper_military": "ammo_huntingrifle_max", "sniper_scout": "ammo_huntingrifle_max",
        "sniper_awp": "ammo_huntingrifle_max", "autoshotgun": "ammo_shotgun_max",
        "shotgun_spas": "ammo_shotgun_max", "pumpshotgun": "ammo_shotgun_max",
        "shotgun_chrome": "ammo_shotgun_max", "grenade_launcher": "ammo_grenadelauncher_max",
    }
    defaults = default_values()
    requested: dict[str, int] = {}
    for key, data in values.items():
        if key not in cvars or key not in defaults:
            continue
        value = int(data.get("ammo_max", defaults[key].get("ammo_max", -1)))
        # A changed damage/recoil setting must not also write that weapon's
        # unchanged reserve-ammo default into a shared engine cvar.
        if value < 0 or value == defaults[key].get("ammo_max"):
            continue
        requested[cvars[key]] = value
    commands = [f'Convars.SetValue("{cvar}", {value});' for cvar, value in requested.items()]
    return "\n".join(commands) + ("\n" if commands else "")


def build_custom_vpk(
    addons_root: Path, values: dict[str, dict[str, object]], filename: str = CUSTOM_MOD_FILENAME
) -> Path:
    """Build the smallest compatible archive: changed scripts plus optional ammo vscript."""
    values = _changed_weapon_values(values)
    source = addons_root / ".l4dboss_custom_mod_build"
    shutil.rmtree(source, ignore_errors=True)
    script_root = source / "scripts"
    script_root.mkdir(parents=True, exist_ok=True)
    try:
        for spec in WEAPONS:
            if spec.key in values:
                (script_root / spec.script_name).write_text(
                    _custom_weapon_text(addons_root, spec, values[spec.key]), encoding="utf-8"
                )
        for spec in MELEE:
            if spec.key in values:
                target = script_root / "melee" / f"{spec.key}.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(_custom_melee_text(addons_root, spec, values[spec.key]), encoding="utf-8")
        ammo = _ammo_vscript(values)
        if ammo:
            vscript_root = script_root / "vscripts"
            target = vscript_root / "l4dboss_custom_ammo.nut"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(ammo, encoding="utf-8")
            # mapspawn_addon runs before the general Squirrel VM is ready.
            # Defer Convars.SetValue into worldspawn's script scope so the
            # reserve-ammo cvars actually apply on every new map.
            (vscript_root / "mapspawn_addon.nut").write_text(
                'EntFire("worldspawn", "RunScriptFile", "l4dboss_custom_ammo");\n',
                encoding="utf-8",
            )
        output = addons_root / filename
        package = vpk.new(str(source))
        package.version = 1
        package.save(str(output))
        return output
    finally:
        shutil.rmtree(source, ignore_errors=True)


def validate_custom_vpk(output: Path, values: dict[str, dict[str, object]]) -> list[str]:
    values = _changed_weapon_values(values)
    package = vpk.open(str(output))
    files = {str(path).replace("\\", "/") for path in package}
    written: list[str] = []
    for spec in WEAPONS:
        if spec.key not in values:
            continue
        path = f"scripts/{spec.script_name}"
        if path not in files:
            raise RuntimeError(f"生成结果缺少 {path}")
        text = package[path].read().decode("utf-8", errors="replace")
        match = re.search(r'"clip_size"\s+"([^"]+)"', text, flags=re.IGNORECASE)
        if match and int(float(match.group(1))) != int(values[spec.key]["clip_size"]):
            raise RuntimeError(f"{path} 的弹匣容量未正确写入")
        written.append(path)
    return written


def write_custom_preview(
    addons_root: Path, title: str, image_path: str = "", filename: str = CUSTOM_MOD_PREVIEW_FILENAME
) -> Path:
    target = addons_root / filename
    image = QImage(image_path) if image_path else QImage()
    if image.isNull():
        image = QImage(960, 540, QImage.Format_RGB32)
        painter = QPainter(image)
        painter.fillRect(image.rect(), QColor("#152942"))
        painter.fillRect(0, 0, image.width(), 10, QColor("#4d83eb"))
        painter.setPen(QColor("#eaf2ff"))
        painter.setFont(QFont("Microsoft YaHei UI", 36, QFont.Bold))
        painter.drawText(image.rect().adjusted(56, 0, -56, -10), Qt.AlignCenter, title or "自定义 Mod")
        painter.setPen(QColor("#9fb8d8"))
        painter.setFont(QFont("Microsoft YaHei UI", 16))
        painter.drawText(image.rect().adjusted(56, 180, -56, -10), Qt.AlignHCenter | Qt.AlignTop, "L4DBoss · Custom Loadout")
        painter.end()
    image.save(str(target), "JPG", quality=92)
    return target


class ToggleSwitch(QCheckBox):
    """Compact, theme-aware boolean control used for option-style settings."""
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setChecked(checked)
        self.setText("")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(ui(50), ui(25))
        self.stateChanged.connect(self._update_hint)
        self._update_hint()

    def _update_hint(self) -> None:
        self.setToolTip("已开启" if self.isChecked() else "已关闭")

    def mouseReleaseEvent(self, event) -> None:
        # The control has a fully custom paint routine, so handle the click
        # explicitly instead of relying on the native checkbox indicator.
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.setChecked(not self.isChecked())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self.rect().adjusted(ui(1), ui(2), -ui(1), -ui(2))
        active = self.isChecked()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_color("toggle_on_fill" if active else "toggle_off_fill")))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        painter.setPen(QColor(theme_color("toggle_on_border" if active else "toggle_off_border")))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        diameter = track.height() - ui(5)
        x = track.right() - diameter - ui(3) if active else track.left() + ui(3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_color("toggle_knob")))
        painter.drawEllipse(int(x), track.top() + ui(3), diameter, diameter)
        painter.end()


class CustomModPage(QWidget):
    def __init__(self, parent=None, initial_values: dict[str, dict[str, object]] | None = None,
                 presets: dict[str, dict[str, dict[str, object]]] | None = None):
        super().__init__(parent)
        self.defaults = default_values()
        self.values = default_values()
        self.presets = presets or {}
        for key, data in (initial_values or {}).items():
            if key in self.values:
                migrated = dict(data)
                # Older releases displayed 360 as the pistol default even
                # though the game's ordinary pistol reserve is unlimited.
                if key == "pistol" and migrated.get("ammo_max") == 360:
                    migrated["ammo_max"] = -1
                self.values[key].update(migrated)
        self._controls: dict[str, dict[str, QWidget]] = {}

        outer = QHBoxLayout(self)
        outer.setContentsMargins(ui(3), ui(3), ui(3), ui(3))
        surface = QFrame()
        self.surface = surface
        surface.setObjectName("customModSurface")
        outer.addWidget(surface, 1)
        root = QVBoxLayout(surface)
        root.setContentsMargins(ui(10), ui(7), ui(10), ui(7))
        root.setSpacing(ui(4))

        preset = QFrame(); preset.setObjectName("customModPresetBar")
        preset_row = QHBoxLayout(preset); preset_row.setContentsMargins(ui(9), ui(5), ui(9), ui(5)); preset_row.setSpacing(ui(5))
        preset_row.addWidget(QLabel("预设"))
        self.preset_combo = StyledComboBox(); self.preset_combo.setObjectName("customModCombo"); self.preset_combo.addItem("当前配置", ""); self.preset_combo.addItems(sorted(self.presets))
        self.preset_combo.currentTextChanged.connect(self._load_preset); preset_row.addWidget(self.preset_combo, 1)
        self.preset_name = QLineEdit(); self.preset_name.setObjectName("customModPresetName"); self.preset_name.setPlaceholderText("输入名称后保存为预设")
        preset_row.addWidget(self.preset_name, 1)
        self.save_preset_button = QPushButton("保存预设"); self.save_preset_button.setObjectName("secondaryButton"); preset_row.addWidget(self.save_preset_button)
        self.rename_preset_button = QPushButton("重命名"); self.rename_preset_button.setObjectName("secondaryButton"); preset_row.addWidget(self.rename_preset_button)
        self.delete_preset_button = QPushButton("删除"); self.delete_preset_button.setObjectName("secondaryButton"); preset_row.addWidget(self.delete_preset_button)
        root.addWidget(preset)

        tabs = QTabWidget(); tabs.setObjectName("customModTabs"); tabs.tabBar().setObjectName("customModTabBar")
        tabs.addTab(self._build_weapon_tab(), "枪械")
        tabs.addTab(self._build_melee_tab(), "近战武器")
        root.addWidget(tabs, 1)

        actions = QHBoxLayout(); actions.setSpacing(ui(6))
        reset = QPushButton("恢复全部默认"); reset.setObjectName("secondaryButton"); reset.clicked.connect(self._reset_defaults)
        self.back_button = QPushButton("取消"); self.back_button.setObjectName("secondaryButton")
        self.generate_button = QPushButton("生成Mod"); self.generate_button.setObjectName("primaryButton")
        actions.addWidget(reset); actions.addStretch(1); actions.addWidget(self.back_button); actions.addWidget(self.generate_button)
        root.addLayout(actions)

    def _build_weapon_tab(self) -> QWidget:
        page = QWidget(); layout = QHBoxLayout(page); layout.setContentsMargins(ui(3), ui(5), ui(3), ui(1)); layout.setSpacing(ui(8))
        picker, first_key = self._grouped_picker(WEAPON_GROUPS, WEAPONS)
        layout.addWidget(picker)
        right = QVBoxLayout(); right.setSpacing(ui(5))
        self.apply_recoil_all_button = QPushButton("将“无后坐力”应用到所有枪械"); self.apply_recoil_all_button.setObjectName("customModApplyButton")
        self.apply_recoil_all_button.clicked.connect(self._apply_recoil_all); right.addWidget(self.apply_recoil_all_button, 0, Qt.AlignLeft)
        stack = QStackedWidget(); stack.setObjectName("customModFormHost"); right.addWidget(stack, 1); layout.addLayout(right, 1)
        for spec in WEAPONS:
            panel, controls = self._build_weapon_form(spec); self._controls[spec.key] = controls; stack.addWidget(panel)
        index_by_key = {spec.key: index for index, spec in enumerate(WEAPONS)}
        picker.currentItemChanged.connect(lambda item, _old: stack.setCurrentIndex(index_by_key[item.data(0, Qt.UserRole)]) if item and item.data(0, Qt.UserRole) in index_by_key else None)
        self._select_picker_key(picker, first_key)
        return page

    def _build_melee_tab(self) -> QWidget:
        page = QWidget(); layout = QHBoxLayout(page); layout.setContentsMargins(ui(3), ui(5), ui(3), ui(1)); layout.setSpacing(ui(8))
        picker, first_key = self._grouped_picker((("近战武器 Melee", tuple(spec.key for spec in MELEE)),), MELEE)
        layout.addWidget(picker)
        right = QVBoxLayout(); right.setSpacing(ui(5))
        self.apply_continuous_all_button = QPushButton("将“连续攻击效果”应用到所有近战武器"); self.apply_continuous_all_button.setObjectName("customModApplyButton")
        self.apply_continuous_all_button.clicked.connect(self._apply_continuous_all); right.addWidget(self.apply_continuous_all_button, 0, Qt.AlignLeft)
        stack = QStackedWidget(); stack.setObjectName("customModFormHost"); right.addWidget(stack, 1); layout.addLayout(right, 1)
        for spec in MELEE:
            panel, controls = self._build_melee_form(spec); self._controls[spec.key] = controls; stack.addWidget(panel)
        index_by_key = {spec.key: index for index, spec in enumerate(MELEE)}
        picker.currentItemChanged.connect(lambda item, _old: stack.setCurrentIndex(index_by_key[item.data(0, Qt.UserRole)]) if item and item.data(0, Qt.UserRole) in index_by_key else None)
        self._select_picker_key(picker, first_key)
        return page

    @staticmethod
    def _select_picker_key(picker: QTreeWidget, key: str) -> None:
        iterator = QTreeWidgetItemIterator(picker)
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole) == key:
                picker.setCurrentItem(item)
                return
            iterator += 1

    @staticmethod
    def _grouped_picker(groups, specs):
        tree = QTreeWidget(); tree.setObjectName("customModPicker"); tree.setFixedWidth(ui(155))
        tree.setHeaderHidden(True); tree.setIndentation(ui(14)); tree.setRootIsDecorated(True)
        by_key = {spec.key: spec for spec in specs}; first_key = ""
        for group_label, keys in groups:
            parent = QTreeWidgetItem([group_label]); parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
            font = parent.font(0); font.setBold(True); parent.setFont(0, font)
            tree.addTopLevelItem(parent)
            for key in keys:
                spec = by_key.get(key)
                if spec is None: continue
                child = QTreeWidgetItem([spec.label]); child.setData(0, Qt.UserRole, key); parent.addChild(child)
                first_key = first_key or key
            parent.setExpanded(True)
        return tree, first_key

    @staticmethod
    def _spin(value: object, minimum: float, maximum: float, decimals: int) -> QWidget:
        widget: QWidget = StyledDoubleSpinBox() if decimals else StyledSpinBox()
        widget.setRange(minimum, maximum)
        if isinstance(widget, QDoubleSpinBox):
            widget.setDecimals(decimals); widget.setSingleStep(.05)
        else:
            widget.setSingleStep(1)
            if minimum < 0:
                widget.setSpecialValueText("无限")
        widget.setValue(value)
        return widget

    def _control_row(self, widget: QWidget, reset_value: object, field: str) -> QWidget:
        row = QWidget(); layout = QHBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(ui(6))
        widget.setFixedWidth(ui(195))
        layout.addStretch(1); layout.addWidget(widget)
        button = QPushButton("恢复默认"); button.setObjectName("customModResetButton")
        def restore() -> None:
            if isinstance(widget, QComboBox): widget.setCurrentIndex(0 if reset_value else 1)
            elif isinstance(widget, QCheckBox): widget.setChecked(bool(reset_value))
            else: widget.setValue(reset_value)
        button.clicked.connect(restore); layout.addWidget(button)
        return row

    def _label(self, title: str, default: object) -> str:
        return f"{title}（默认 {'无限' if default == -1 else default}）"

    def _build_weapon_form(self, spec: WeaponSpec):
        panel = QFrame(); panel.setObjectName("customModForm")
        form = QFormLayout(panel); form.setContentsMargins(ui(12), ui(7), ui(12), ui(7)); form.setHorizontalSpacing(ui(12)); form.setVerticalSpacing(ui(2))
        controls: dict[str, QWidget] = {}; defaults = self.defaults[spec.key]; values = self.values[spec.key]
        fields = [
            ("damage", "伤害", 0, 10000, 0), ("clip_size", "弹匣容量", 0, 1000, 0),
            ("ammo_max", "总子弹数", -1, 10000, 0), ("cycle_time", "射击间隔 / 射速", .01, 10, 4),
            ("range", "射程", 0, 20000, 0), ("range_modifier", "远距离伤害衰减", 0, 2, 3),
            ("reload_duration", "换弹速度", .01, 30, 3), ("deploy_duration", "切枪速度", .01, 10, 3),
        ]
        for key, label, low, high, decimals in fields:
            widget = self._spin(values[key], low, high, decimals); controls[key] = widget
            form.addRow(self._label(label, defaults[key]), self._control_row(widget, defaults[key], key))
        recoil = ToggleSwitch(bool(values["recoil"])); recoil.setObjectName("customModSwitch")
        controls["recoil"] = recoil; form.addRow(self._label("是否后坐力", "是" if defaults["recoil"] else "否"), self._control_row(recoil, defaults["recoil"], "recoil"))
        return panel, controls

    def _build_melee_form(self, spec: MeleeSpec):
        panel = QFrame(); panel.setObjectName("customModForm")
        form = QFormLayout(panel); form.setContentsMargins(ui(12), ui(7), ui(12), ui(7)); form.setHorizontalSpacing(ui(12)); form.setVerticalSpacing(ui(2))
        controls: dict[str, QWidget] = {}; defaults = self.defaults[spec.key]; values = self.values[spec.key]
        fields = [
            ("damage", "伤害", 0, 10000, 0), ("attack_speed", "攻击速度", .01, 10, 3),
            ("attack_range", "攻击范围", 0, 1000, 0), ("push_force", "推力", 0, 5000, 0),
            ("attack_interval", "攻击间隔", .01, 10, 3),
        ]
        for key, label, low, high, decimals in fields:
            widget = self._spin(values[key], low, high, decimals); controls[key] = widget
            form.addRow(self._label(label, defaults[key]), self._control_row(widget, defaults[key], key))
        continuous = ToggleSwitch(bool(values["continuous"])); continuous.setObjectName("customModSwitch"); controls["continuous"] = continuous
        form.addRow(self._label("连续攻击效果", "否"), self._control_row(continuous, defaults["continuous"], "continuous"))
        return panel, controls

    def _apply_recoil_all(self) -> None:
        for spec in WEAPONS:
            self._controls[spec.key]["recoil"].setChecked(False)

    def _apply_continuous_all(self) -> None:
        for spec in MELEE:
            self._controls[spec.key]["continuous"].setChecked(True)

    def _reset_defaults(self) -> None:
        self.values = default_values(); self._apply_values_to_controls()

    def _apply_values_to_controls(self) -> None:
        for key, controls in self._controls.items():
            for field, widget in controls.items():
                value = self.values[key][field]
                if isinstance(widget, QComboBox): widget.setCurrentIndex(0 if value else 1)
                elif isinstance(widget, QCheckBox): widget.setChecked(bool(value))
                else: widget.setValue(value)

    def _load_preset(self, name: str) -> None:
        preset = self.presets.get(name)
        if not preset: return
        self.values = default_values()
        for key, data in preset.items():
            if key in self.values: self.values[key].update(data)
        self._apply_values_to_controls()

    def current_values(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for key, controls in self._controls.items():
            result[key] = {
                field: (widget.currentIndex() == 0 if isinstance(widget, QComboBox) else widget.isChecked() if isinstance(widget, QCheckBox) else widget.value())
                for field, widget in controls.items()
            }
        return result

    def collected_values(self) -> dict[str, dict[str, object]]:
        current = self.current_values(); defaults = default_values()
        return {key: data for key, data in current.items() if data != defaults[key]}


class CustomModPublishDialog(QDialog):
    def __init__(self, parent=None, initial_name: str = ""):
        super().__init__(parent)
        # Keep this flow consistent with the rest of the application: no
        # operating-system title bar, only the themed in-page heading.
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("customModPublishDialog")
        self.setMinimumWidth(ui(460))
        layout = QVBoxLayout(self); layout.setContentsMargins(ui(22), ui(20), ui(22), ui(18)); layout.setSpacing(ui(12))
        title = QLabel("发布自定义 Mod"); title.setObjectName("customModPublishTitle"); layout.addWidget(title)
        subtitle = QLabel("名称用于管理器显示；封面将保存到 addons 文件夹。未选择图片时自动生成默认封面。")
        subtitle.setObjectName("customModIntro"); subtitle.setWordWrap(True); layout.addWidget(subtitle)
        form = QFormLayout(); form.setSpacing(ui(10))
        name_label = QLabel("Mod 名称"); name_label.setObjectName("customModPublishLabel")
        self.name_edit = QLineEdit(initial_name or "自定义 Mod"); form.addRow(name_label, self.name_edit)
        image_row = QWidget(); image_layout = QHBoxLayout(image_row); image_layout.setContentsMargins(0, 0, 0, 0); image_layout.setSpacing(ui(6))
        self.image_edit = QLineEdit(); self.image_edit.setPlaceholderText("未选择时自动生成封面"); self.image_edit.setReadOnly(True)
        choose = QPushButton("选择图片"); choose.setObjectName("secondaryButton"); choose.clicked.connect(self._choose_image)
        image_layout.addWidget(self.image_edit, 1); image_layout.addWidget(choose)
        image_label = QLabel("封面图片"); image_label.setObjectName("customModPublishLabel")
        form.addRow(image_label, image_row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("生成并安装")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primaryButton")
        buttons.button(QDialogButtonBox.Cancel).setObjectName("secondaryButton")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _choose_image(self) -> None:
        from .components import AppFileDialog
        path, _ = AppFileDialog.getOpenFileName(self, "选择 Mod 封面", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path: self.image_edit.setText(path)

    @property
    def mod_name(self) -> str:
        return self.name_edit.text().strip() or "自定义 Mod"

    @property
    def image_path(self) -> str:
        return self.image_edit.text().strip()
