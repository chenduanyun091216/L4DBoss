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


def sanitize_collection_name(collection_name: str) -> str | None:
    """Return the folder-safe collection name, or None when it is unusable.

    Windows-illegal characters are replaced with underscores; names that are
    empty after trimming or reserved (``workshop``, ``.``, ``..``, the hidden
    collections folder itself) cannot be used.
    """
    trimmed = collection_name.strip()
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", collection_name).strip(" .")
    if (
        not safe_name
        or trimmed.casefold() in {"workshop", ".", "..", COLLECTIONS_DIR_NAME}
        or safe_name.casefold() in {"workshop", ".", "..", COLLECTIONS_DIR_NAME}
    ):
        return None
    return safe_name


def collection_folder(addons_root: Path, collection_name: str) -> Path | None:
    safe_name = sanitize_collection_name(collection_name)
    if safe_name is None:
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


def rename_collection_folder(addons_root: Path, old_name: str, new_name: str) -> tuple[bool, str]:
    """Rename the on-disk folder of a saved collection.

    Returns ``(True, "")`` on success — including when nothing was synced to
    disk yet, or when both names map to the same folder. Returns
    ``(False, reason)`` when the target name is unusable or the folder cannot
    be moved; the caller should then abort the record rename.
    """
    old_folder = collection_folder(addons_root, old_name)
    new_folder = collection_folder(addons_root, new_name)
    if new_folder is None:
        return False, f"名称「{new_name}」不能作为组合文件夹名称"
    if old_folder is None or not old_folder.is_dir():
        return True, ""
    if new_folder == old_folder:
        return True, ""
    if new_folder.exists():
        return False, f"文件夹「{new_folder.name}」已存在，请换一个名称"
    with _SYNC_LOCK:
        try:
            old_folder.rename(new_folder)
        except OSError as exc:
            return False, f"组合文件夹重命名失败：{exc}"
    return True, ""


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
