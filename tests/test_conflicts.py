import unittest

from l4d2_mod_manager.models import Mod
from l4d2_mod_manager.vpk_scanner import detect_conflicts, is_conflict_relevant_path


def make_mod(mod_id: str, files: list[str]) -> Mod:
    return Mod(
        id=mod_id,
        file_path=f"{mod_id}.vpk",
        file_name=f"{mod_id}.vpk",
        title=mod_id,
        files=files,
        active=True,
    )


class ConflictDetectionTests(unittest.TestCase):
    def test_funky_excludes_root_metadata_and_parallel_vscripts(self):
        self.assertFalse(is_conflict_relevant_path("addoninfo.txt"))
        self.assertFalse(is_conflict_relevant_path("scripts/vscripts/director_base_addon.nut"))
        self.assertFalse(is_conflict_relevant_path("SCRIPTS\\VSCRIPTS\\MAPSPAWN_ADDON.NUT"))

    def test_nested_resources_are_checked(self):
        mods = {
            "one": make_mod("one", ["resource/foo.txt"]),
            "two": make_mod("two", ["resource/foo.txt"]),
        }

        conflicts = detect_conflicts(mods)

        self.assertEqual(conflicts, {"one": ["two"], "two": ["one"]})

    def test_director_base_does_not_create_false_positive_group(self):
        mods = {
            str(index): make_mod(str(index), ["scripts/vscripts/director_base_addon.nut"])
            for index in range(15)
        }

        self.assertEqual(detect_conflicts(mods), {})


if __name__ == "__main__":
    unittest.main()
