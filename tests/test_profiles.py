import json
import tempfile
import unittest
from pathlib import Path

from tibia_loot_manager.datastore import DataStore
from tibia_loot_manager.webui.service import AppService, Dialogs, UserError

from .test_webui import make_store


class FileDialogs(Dialogs):
    def __init__(self, root: Path):
        self.root = root
        self.next_open = None

    def save_file(self, initial_name, extension, kinds, initial_dir=None):
        return str(self.root / initial_name)

    def open_file(self, kinds, initial_dir=None):
        return self.next_open


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dialogs = FileDialogs(self.root)
        self.svc = AppService(make_store(self.root), self.dialogs)

    def tearDown(self):
        self.tmp.cleanup()

    def accepted_ids(self):
        return self.svc.library.accepted_ids()

    def test_existing_list_becomes_main_profile(self):
        listing = self.svc.profile_list()
        self.assertEqual([p["name"] for p in listing["profiles"]], ["Main"])
        self.assertEqual(listing["profiles"][0]["count"], 1)  # buckle, from the Delivery Task list

    def test_profiles_keep_separate_lists_but_share_delivery_edits(self):
        self.svc.toggle_accepted("3031")  # Main: buckle + gold coin
        main = self.svc.library.state.active_profile
        self.svc.profile_create("Hunting", start="empty")
        self.assertEqual(self.accepted_ids(), set())
        self.svc.toggle_accepted("3035")
        self.assertEqual(self.accepted_ids(), {3035})
        self.svc.toggle_delivery("wiki:Buckle")  # shared: affects every profile following the list
        self.svc.profile_switch(main)
        self.assertEqual(self.accepted_ids(), {3031})
        self.svc.toggle_delivery("wiki:Buckle")
        self.assertEqual(self.accepted_ids(), {3031, 17829})

    def test_profiles_survive_a_restart(self):
        self.svc.profile_create("Hunting", start="empty")
        self.svc.toggle_accepted("3035")
        again = AppService(DataStore(self.root), self.dialogs)
        self.assertEqual(again.library.state.profiles[again.library.state.active_profile]["name"], "Hunting")
        self.assertEqual(again.library.accepted_ids(), {3035})

    def test_history_and_restore(self):
        pid = self.svc.library.state.active_profile
        self.svc.toggle_accepted("3031")
        self.svc.toggle_accepted("3035")
        entries = self.svc.profile_history(pid)["entries"]
        self.assertEqual([e["description"] for e in entries[:2]], ["Added platinum coin", "Added gold coin"])
        self.svc.profile_restore(pid, entries[1]["index"])  # back to "Added gold coin"
        self.assertEqual(self.accepted_ids(), {17829, 3031})

    def test_export_import_round_trip(self):
        self.svc.toggle_accepted("3031")
        exported = self.svc.profile_export(self.svc.library.state.active_profile)["path"]
        data = json.loads(Path(exported).read_text(encoding="utf-8"))
        self.assertEqual((data["format"], data["extra"]), ("tibia-loot-profile", [3031]))
        self.dialogs.next_open = exported
        result = self.svc.profile_import()
        self.assertEqual(result["name"], "Main (2)")  # never overwrites an existing profile
        self.assertEqual(self.accepted_ids(), {17829, 3031})

    def test_import_rejects_other_files(self):
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"format": "something-else"}))
        self.dialogs.next_open = str(bad)
        with self.assertRaises(UserError):
            self.svc.profile_import()

    def test_profile_from_character_file(self):
        result = self.svc.profile_from_character("111")
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.accepted_ids(), {3031})  # the character's whitelist only

    def test_compare(self):
        main = self.svc.library.state.active_profile
        other = self.svc.profile_create("Hunting", start="empty")["id"]
        self.svc.toggle_accepted("3035")
        diff = self.svc.profile_compare(main, other)
        self.assertEqual((diff["only_a"], diff["only_b"], diff["both"]), (["buckle"], ["platinum coin"], 0))

    def test_names_and_deletion_rules(self):
        only = self.svc.library.state.active_profile
        with self.assertRaises(UserError):
            self.svc.profile_delete(only)
        second = self.svc.profile_create("Main", start="copy")["id"]
        self.assertEqual(self.svc.library.state.profiles[second]["name"], "Main (2)")
        with self.assertRaises(UserError):
            self.svc.profile_rename(second, "   ")
        self.svc.profile_delete(second)  # deleting the active profile switches to another
        self.assertEqual(self.svc.library.state.active_profile, only)


if __name__ == "__main__":
    unittest.main()
