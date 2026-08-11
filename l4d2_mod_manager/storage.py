from __future__ import annotations

import json
from pathlib import Path

from .models import Mod, ModCollection


class AppStorage:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.data_dir = base_dir / "data"
        self.data_dir.mkdir(exist_ok=True)
        self.settings_file = self.data_dir / "settings.json"
        self.mods_file = self.data_dir / "mods.json"
        self.steam_cache_file = self.data_dir / "steam_cache.json"
        self.collections_file = self.data_dir / "collections.json"

    def load_settings(self) -> dict:
        return self._read_json(self.settings_file, {"mod_dir": ""})

    def save_settings(self, settings: dict) -> None:
        self._write_json(self.settings_file, settings)

    def load_mods(self) -> dict[str, Mod]:
        raw = self._read_json(self.mods_file, {})
        return {key: Mod.from_dict(value) for key, value in raw.items()}

    def save_mods(self, mods: dict[str, Mod]) -> None:
        self._write_json(self.mods_file, {key: mod.to_dict() for key, mod in mods.items()})

    def load_steam_cache(self) -> dict[str, dict]:
        return self._read_json(self.steam_cache_file, {})

    def save_steam_cache(self, cache: dict[str, dict]) -> None:
        self._write_json(self.steam_cache_file, cache)

    def load_collections(self) -> list[ModCollection]:
        raw = self._read_json(self.collections_file, [])
        return [ModCollection.from_dict(item) for item in raw]

    def save_collections(self, collections: list[ModCollection]) -> None:
        self._write_json(self.collections_file, [collection.to_dict() for collection in collections])

    def reset_mods(self) -> None:
        if self.mods_file.exists():
            self.mods_file.unlink()

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
