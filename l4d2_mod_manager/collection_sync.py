from __future__ import annotations

import ctypes
import re
import shutil
import sys
from pathlib import Path
from threading import Lock
from typing import Iterable

from .models import Mod

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_SYNC_LOCK = Lock()
COLLECTIONS_DIR_NAME = ".mods"


def collections_root(addons_root: Path) -> Path:
    """Return the hidden directory used to store saved Mod collections."""
    return addons_root.resolve() / COLLECTIONS_DIR_NAME


def _hide_directory(path: Path) -> None:
    """Mark the collections directory hidden on Windows when possible."""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        get_attributes = kernel32.GetFileAttributesW
        set_attributes = kernel32.SetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attributes.restype = ctypes.c_int
        attributes = get_attributes(str(path))
        invalid_attributes = 0xFFFFFFFF
        if attributes != invalid_attributes and not attributes & 0x2:
            set_attributes(str(path), attributes | 0x2)
    except (AttributeError, OSError):
        # Hiding is a convenience; collection management must still work if
        # the platform API is unavailable or refuses the attribute change.
        pass


def ensure_collections_root(addons_root: Path) -> Path:
    root = collections_root(addons_root)
    root.mkdir(parents=True, exist_ok=True)
    _hide_directory(root)
    return root


def collection_folder(addons_root: Path, collection_name: str) -> Path | None:
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", collection_name).strip(" .")
    if not safe_name or safe_name.casefold() in {"workshop", ".", "..", COLLECTIONS_DIR_NAME}:
        return None
    root = collections_root(addons_root)
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
        ensure_collections_root(addons_root)
        folder.mkdir(parents=True, exist_ok=True)
        removable = {".vpk", *IMAGE_SUFFIXES}
        for path in folder.iterdir():
            if path.is_file() and path.suffix.casefold() in removable and path.name.casefold() not in desired:
                path.unlink()
        for source in desired.values():
            shutil.copy2(source, folder / source.name)


def delete_collection_folder(addons_root: Path, collection_name: str) -> None:
    """Delete the saved files for one collection, if its folder exists."""
    folder = collection_folder(addons_root, collection_name)
    if folder is None or not folder.is_dir():
        return
    collections = collections_root(addons_root)
    try:
        folder.relative_to(collections)
    except ValueError:
        return
    with _SYNC_LOCK:
        if folder.is_dir():
            shutil.rmtree(folder)


def restore_collection_files(addons_root: Path, collection_names: Iterable[str], progress_callback=None) -> int:
    """Restore missing collection VPKs/images to the active addons root."""
    root = addons_root.resolve()
    ensure_collections_root(root)
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
