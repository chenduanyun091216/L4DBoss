from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Mod:
    id: str
    file_path: str
    file_name: str
    title: str
    author: str = "未知创作者"
    subscriptions: int = 0
    rating: float = 0.0
    description: str = ""
    steam_tags: list[str] = field(default_factory=list)
    image_path: str | None = None
    categories: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    active: bool = False
    favorite: bool = False
    favorite_at: int = 0
    conflict_with: list[str] = field(default_factory=list)
    # 冲突组置顶序号：0 表示未置顶；值越大代表置顶越新。
    # 冲突组内排序时置顶的 Mod 排在最前，addonlist.txt 中同样优先（游戏优先读取靠前条目）。
    conflict_pin: int = 0
    dependencies: list[str] = field(default_factory=list)
    steam_loaded: bool = False
    file_size: int = 0
    file_mtime_ns: int = 0
    custom_title: str = ""

    @property
    def display_subscriptions(self) -> str:
        if self.subscriptions >= 10000:
            return f"{self.subscriptions / 10000:.1f}万"
        return str(self.subscriptions)

    @property
    def workshop_id(self) -> str | None:
        stem = Path(self.file_name).stem
        digits = "".join(ch for ch in stem if ch.isdigit())
        return digits or None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Mod":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class ModCollection:
    name: str
    mod_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModCollection":
        return cls(name=data["name"], mod_ids=list(data.get("mod_ids", [])))
