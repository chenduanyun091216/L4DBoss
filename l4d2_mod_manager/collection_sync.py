from __future__ import annotations

import re
import shutil
from pathlib import Path
from threading import Lock
from typing import Iterable

from .models import Mod

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_SYNC_LOCK = Lock()


def collection_folder(addons_root: Path, collection_name: str) -> Path | None:
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", collection_name).strip(" .")
    if not safe_name or safe_name.casefold() in {"workshop", ".", ".."}:
        return None
    root = addons_root.resolve()
    folder = (root / safe_name).resolve()
    try:
        folder.relative_to(root)
    except ValueError:
        return None
    return folder


def sync_collection_files(addons_root: Path, collection_name: str, mods: Iterable[Mod]) -> None:
    folder = collection_folder(addons_root, collection_name)
    if folder is None:
        return
    desired: dict[str, Path] = {}
    for mod in mods:
        source = Path(mod.file_path)
        if source.is_file():
            desired[source.name.casefold()] = source
        if mod.image_path:
            image = Path(mod.image_path)
            if image.is_file():
                desired[image.name.casefold()] = image

    with _SYNC_LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        removable = {".vpk", *IMAGE_SUFFIXES}
        for path in folder.iterdir():
            if path.is_file() and path.suffix.casefold() in removable and path.name.casefold() not in desired:
                path.unlink()
        for source in desired.values():
            shutil.copy2(source, folder / source.name)


def restore_collection_files(addons_root: Path, collection_names: Iterable[str], progress_callback=None) -> int:
    """Restore missing collection VPKs/images to the active addons root."""
    root = addons_root.resolve()
    workshop_root = root / "workshop"
    restored = 0
    sources: list[Path] = []
    for collection_name in collection_names:
        folder = collection_folder(root, collection_name)
        if folder is None or not folder.is_dir():
            continue
        sources.extend(
            source for source in folder.iterdir()
            if source.is_file() and source.suffix.casefold() in {".vpk", *IMAGE_SUFFIXES}
        )
    total = len(sources)
    completed = 0
    if progress_callback is not None:
        progress_callback(0, total)
    with _SYNC_LOCK:
        for source in sources:
            root_target = root / source.name
            workshop_target = workshop_root / source.name
            if not root_target.exists() and not workshop_target.exists():
                shutil.copy2(source, root_target)
                restored += 1
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total)
    return restored
