from __future__ import annotations

import hashlib
from pathlib import Path

import vpk

from .categories import infer_categories
from .models import Mod

IMAGE_SUFFIXES = [".jpg", ".jpeg", ".png", ".webp"]


def scan_mod_directory(directory: Path | list[Path], existing: dict[str, Mod] | None = None, refresh_all: bool = False) -> dict[str, Mod]:
    existing = existing or {}
    result = {} if refresh_all else dict(existing)
    directories = [directory] if isinstance(directory, Path) else directory
    file_paths = [file_path for folder in directories if folder.exists() for file_path in folder.glob("*.vpk")]
    for file_path in sorted(file_paths):
        mod_id = make_mod_id(file_path)
        if not refresh_all and mod_id in result:
            cached = result[mod_id]
            try:
                stat = file_path.stat()
                if (cached.file_size, cached.file_mtime_ns) == (stat.st_size, stat.st_mtime_ns):
                    continue
            except OSError:
                continue
        result[mod_id] = parse_vpk_file(file_path, mod_id)
    valid_paths = {str(path.resolve()) for path in file_paths}
    return {mod_id: mod for mod_id, mod in result.items() if str(Path(mod.file_path).resolve()) in valid_paths}


def parse_vpk_file(file_path: Path, mod_id: str | None = None) -> Mod:
    mod_id = mod_id or make_mod_id(file_path)
    files = read_vpk_paths(file_path)
    title = humanize_title(file_path.stem)
    image_path = find_preview_image(file_path)
    categories = infer_categories(title, files, file_name=file_path.name)
    stat = file_path.stat()
    return Mod(
        id=mod_id,
        file_path=str(file_path.resolve()),
        file_name=file_path.name,
        title=title,
        image_path=str(image_path.resolve()) if image_path else None,
        categories=categories,
        files=files,
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
    )


def read_vpk_paths(file_path: Path) -> list[str]:
    try:
        package = vpk.open(str(file_path))
        return sorted(str(path).replace("\\", "/").lower() for path in package)
    except Exception:
        return []


def make_mod_id(file_path: Path) -> str:
    stem = file_path.stem
    if stem.isdigit():
        return stem
    stat = file_path.stat()
    fingerprint = f"{file_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def find_preview_image(vpk_path: Path) -> Path | None:
    for suffix in IMAGE_SUFFIXES:
        candidate = vpk_path.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def humanize_title(stem: str) -> str:
    cleaned = stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(part.capitalize() for part in cleaned.split()) or stem


def detect_conflicts(mods: dict[str, Mod]) -> dict[str, list[str]]:
    active_mods = [mod for mod in mods.values() if mod.active]
    owner_by_path: dict[str, list[str]] = {}
    for mod in active_mods:
        for path in mod.files:
            if is_conflict_relevant_path(path):
                owner_by_path.setdefault(path, []).append(mod.id)

    conflicts = {mod.id: set() for mod in active_mods}
    for owners in owner_by_path.values():
        if len(owners) > 1:
            for owner in owners:
                conflicts[owner].update(other for other in owners if other != owner)

    for mod in mods.values():
        mod.conflict_with = sorted(conflicts.get(mod.id, set()))
    return {mod_id: sorted(values) for mod_id, values in conflicts.items() if values}


def is_conflict_relevant_path(path: str) -> bool:
    ignored_suffixes = (".txt", ".md", ".res")
    if path.endswith(ignored_suffixes):
        return False
    return path.startswith(("materials/", "models/", "scripts/", "sound/", "soundscape/", "particles/", "missions/", "maps/"))
