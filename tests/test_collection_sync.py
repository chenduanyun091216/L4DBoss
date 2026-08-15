from __future__ import annotations

from pathlib import Path

from l4d2_mod_manager.collection_sync import (
    collection_folder,
    collections_root,
    delete_collection_folder,
    rename_collection_folder,
    sanitize_collection_name,
)


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


def test_sanitize_collection_name_replaces_illegal_characters() -> None:
    assert sanitize_collection_name("a/b:c*d") == "a_b_c_d"
    assert sanitize_collection_name("  My Combo  ") == "My Combo"
    assert sanitize_collection_name("...") is None
    assert sanitize_collection_name("workshop") is None
    assert sanitize_collection_name(".mods") is None


def test_rename_collection_folder_moves_saved_files(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    old_folder = collection_folder(addons, "Old Name")
    assert old_folder is not None
    old_folder.mkdir(parents=True)
    (old_folder / "mod.vpk").write_bytes(b"vpk")

    ok, message = rename_collection_folder(addons, "Old Name", "New Name")

    assert ok, message
    assert not old_folder.exists()
    new_folder = collection_folder(addons, "New Name")
    assert new_folder is not None
    assert (new_folder / "mod.vpk").read_bytes() == b"vpk"


def test_rename_collection_folder_keeps_missing_source_as_success(tmp_path: Path) -> None:
    addons = tmp_path / "addons"

    ok, message = rename_collection_folder(addons, "Never Synced", "New Name")

    assert ok, message


def test_rename_collection_folder_rejects_reserved_or_colliding_targets(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    source = collection_folder(addons, "Source")
    assert source is not None
    source.mkdir(parents=True)
    (source / "mod.vpk").write_bytes(b"vpk")
    target = collection_folder(addons, "Target")
    assert target is not None
    target.mkdir(parents=True)
    (target / "other.vpk").write_bytes(b"vpk")

    ok, _message = rename_collection_folder(addons, "Source", "workshop")
    assert not ok
    ok, _message = rename_collection_folder(addons, "Source", "Target")
    assert not ok
    assert (source / "mod.vpk").read_bytes() == b"vpk"
    assert (target / "other.vpk").read_bytes() == b"vpk"
