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
    file_paths = _scan_files(directories)
    for file_path in sorted(file_paths):
        mod_id = make_mod_id(file_path)
        if not refresh_all and mod_id in result:
            cached = result[mod_id]
            try:
                stat = file_path.stat()
                if (cached.file_size, cached.file_mtime_ns) == (stat.st_size, stat.st_mtime_ns):
                    # VPK 本身未变：旁置预览图可能新增/更换/删除，
                    # 与缓存比对后不一致才需要重新解析。
                    current_image = find_preview_image(file_path)
                    if (str(current_image.resolve()) if current_image else None) == cached.image_path:
                        continue
            except OSError:
                continue
        result[mod_id] = parse_vpk_file(file_path, mod_id)
    valid_paths = {str(path.resolve()) for path in file_paths}
    # A mod whose fingerprint (path:size:mtime) is part of its id can produce a
    # second id after the file is updated (Steam workshop sync, manual replace).
    # Both ids share the same file_path, so keep only the most recent one to
    # avoid the same file appearing as two separate cards.
    best_by_path: dict[str, Mod] = {}
    for mod_id, mod in result.items():
        if str(Path(mod.file_path).resolve()) not in valid_paths:
            continue
        path_key = str(Path(mod.file_path).resolve())
        prev = best_by_path.get(path_key)
        if prev is None or mod.file_mtime_ns > prev.file_mtime_ns:
            best_by_path[path_key] = mod
    return {mod.id: mod for mod in best_by_path.values()}


def _scan_files(directories: list[Path]) -> list[Path]:
    """Scan only the first level of addons and addons/workshop, once each."""
    candidates = [file_path for folder in directories if folder.exists() for file_path in folder.glob("*.vpk")]
    selected: dict[str, Path] = {}
    for file_path in candidates:
        mod_id = make_mod_id(file_path)
        previous = selected.get(mod_id)
        if previous is None or (file_path.parent.name.casefold() == "workshop" and previous.parent.name.casefold() != "workshop"):
            selected[mod_id] = file_path
    return sorted(selected.values(), key=lambda path: str(path).casefold())


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


NON_CONFLICTING_VSCRIPT_FILES = frozenset(
    {
        "scripts/vscripts/mapspawn_addon.nut",
        "scripts/vscripts/response_testbed_addon.nut",
        "scripts/vscripts/scriptedmode_addon.nut",
        "scripts/vscripts/director_base_addon.nut",
    }
)


def is_conflict_relevant_path(path: str) -> bool:
    """Match Funky's conflict rule: nested paths except parallel VScript entry points."""
    normalized = str(path).replace("\\", "/").lower().strip("/")
    return "/" in normalized and normalized not in NON_CONFLICTING_VSCRIPT_FILES
