from __future__ import annotations

from pathlib import Path

from l4d2_mod_manager.collection_sync import collection_folder, collections_root, delete_collection_folder


def test_collection_folder_is_stored_under_hidden_collections_root(tmp_path: Path) -> None:
    addons = tmp_path / "addons"

    folder = collection_folder(addons, "My Collection")

    assert folder == addons.resolve() / ".mods" / "My Collection"
    assert collections_root(addons) == addons.resolve() / ".mods"


def test_collection_folder_rejects_empty_or_reserved_names(tmp_path: Path) -> None:
    addons = tmp_path / "addons"

    assert collection_folder(addons, "...") is None
    assert collection_folder(addons, "workshop") is None
    assert collection_folder(addons, ".mods") is None


def test_delete_collection_folder_removes_saved_collection_files(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    folder = collection_folder(addons, "My Collection")
    assert folder is not None
    folder.mkdir(parents=True)
    (folder / "mod.vpk").write_bytes(b"vpk")

    delete_collection_folder(addons, "My Collection")

    assert not folder.exists()
