import json
import tempfile
import unittest
from pathlib import Path

from tibia_loot_manager.state import UserState
from tibia_loot_manager.webui.service import AppService, UserError

from .test_webui import make_store


class CatalogToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.svc = AppService(make_store(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def accepted(self):
        return sorted(r["key"] for r in self.svc.catalog(seg="mine")["rows"])

    def test_favorites(self):
        self.assertTrue(self.svc.toggle_favorite("3031")["favorite"])
        self.assertTrue(self.svc.toggle_favorite("wiki:Buckle")["favorite"] is True)
        self.assertEqual([r["key"] for r in self.svc.catalog(seg="fav")["rows"]], ["3031"])
        self.assertTrue(self.svc.item("3031")["favorite"])
        self.assertFalse(self.svc.toggle_favorite("3031")["favorite"])
        with self.assertRaises(UserError):
            self.svc.toggle_favorite("999999")

    def test_saved_searches(self):
        self.svc.save_search("Coins", "coin", "all", "", "verified")
        self.svc.save_search("  coins ", "gold", "mine", "", "all")  # same name (any case) replaces it
        saved = self.svc.state()["saved_searches"]
        self.assertEqual(saved, [{"name": "coins", "q": "gold", "seg": "mine", "cat": "", "idf": "all"}])
        with self.assertRaises(UserError):
            self.svc.save_search("   ", "x", "all", "", "all")
        self.svc.delete_search("coins")
        self.assertEqual(self.svc.state()["saved_searches"], [])

    def test_bulk_dry_run_counts_only(self):
        r = self.svc.accepted_bulk("coin", "all", "", "all", add=True, dry_run=True)
        self.assertEqual((r["matching"], r["changes"]), (2, 2))
        self.assertEqual(self.accepted(), ["17829"])

    def test_bulk_add_and_remove(self):
        self.assertEqual(self.svc.accepted_bulk("coin", "all", "", "all", add=True)["changed"], 2)
        self.assertEqual(self.accepted(), ["17829", "3031", "3035"])
        self.assertEqual(self.svc.accepted_bulk("", "mine", "", "all", add=False)["changed"], 3)
        self.assertEqual(self.accepted(), [])
        # Re-adding a Delivery Task item lifts its exclusion instead of adding a manual copy.
        self.svc.accepted_bulk("buckle", "all", "", "all", add=True)
        self.assertEqual(self.accepted(), ["17829"])
        self.assertNotIn(17829, self.svc.library.state.accepted_extra)
        history = self.svc.profile_history(self.svc.library.state.active_profile)["entries"]
        self.assertIn("at once", history[0]["description"])

    def test_bulk_matches_one_by_one_toggles(self):
        other = AppService(make_store(self.root / "b"))
        keys = ["3031", "17829", "2915"]
        for k in keys:
            if k in [r["key"] for r in other.catalog(seg="mine")["rows"]]:
                continue
            other.toggle_accepted(k)
        self.svc.library.set_accepted_many(keys, add=True)
        self.assertEqual(self.accepted(), sorted(r["key"] for r in other.catalog(seg="mine")["rows"]))
        for k in keys:
            other.toggle_accepted(k)
        self.svc.library.set_accepted_many(keys, add=False)
        self.assertEqual(self.accepted(), [])
        self.assertEqual(sorted(self.svc.library.state.accepted_excluded),
                         sorted(other.library.state.accepted_excluded))

    def test_damaged_saved_searches_are_dropped(self):
        state = UserState.from_dict(json.loads('{"saved_searches": [{"name": 1}], "favorites": [3031]}'))
        self.assertEqual((state.saved_searches, state.favorites), ([], []))


if __name__ == "__main__":
    unittest.main()
