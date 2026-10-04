import tempfile
import unittest
from pathlib import Path

from tibia_loot_manager import updates as U
from tibia_loot_manager.datastore import DataStore
from tibia_loot_manager.library import Library
from tibia_loot_manager.sources import tibia_client
from tibia_loot_manager.sources.tibiawiki import SOURCE_ID as WIKI
from tibia_loot_manager.state import UserState
from tibia_loot_manager.storage import read_json

from .test_library import CATALOG, delivery

OLD = delivery(("Buckle", [1]), ("Sliver", [2]), ("Book", [3]))
NEW = delivery(("Buckle", [1]), ("Book", [3]), ("Gold Nugget", [9]))
NEW["items"]["Buckle"]["max_qty"] = 50


class DiffTest(unittest.TestCase):
    def test_delivery_diff(self):
        lib = Library(CATALOG, OLD, {}, UserState())
        changes = {(c.kind, c.key): c for c in U.diff_delivery(OLD, NEW, lib)}
        self.assertEqual(set(changes), {(U.NEW, "Gold Nugget"), (U.REMOVED, "Sliver"), (U.CHANGED, "Buckle")})
        self.assertIn("max: 2 → 50", changes[(U.CHANGED, "Buckle")].details)
        self.assertIn("Accepted Loot", changes[(U.REMOVED, "Sliver")].user_note)

    def test_catalog_diff(self):
        new = {"items": {**CATALOG["items"], "10": {"id": 10, "name": "new thing", "category": "Others"}}}
        new["items"]["1"] = {**new["items"]["1"], "name": "buckle (renamed)"}
        kinds = sorted((c.kind, c.key) for c in U.diff_catalog(CATALOG, new, Library(CATALOG, OLD, {}, UserState())))
        self.assertEqual(kinds, [(U.CHANGED, "1"), (U.NEW, "10")])


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DataStore(Path(self.tmp.name))
        self.state = UserState()
        self.lib = Library(CATALOG, OLD, {}, self.state)
        self.store.save_delivery(OLD)

    def tearDown(self):
        self.tmp.cleanup()

    def review(self, wiki_ok=True):
        review = U.UpdateReview()
        review.results.append(U.SourceResult(tibia_client.SOURCE_ID, "client", False, "not installed"))
        if wiki_ok:
            review.results.append(U.SourceResult(WIKI, "wiki", True, snapshot=NEW))
            review.changes = U.diff_delivery(OLD, NEW, self.lib)
        else:
            review.results.append(U.SourceResult(WIKI, "wiki", False, "HTTP 503"))
        return review

    def test_apply_keeps_user_edits_and_records_sources(self):
        self.lib.add_to_accepted(9)
        self.lib.remove_from_delivery(next(e for e in self.lib.delivery_entries()[0] if e.client_id == 3))
        U.apply_update(self.review(), self.lib, self.store)
        self.assertEqual(self.lib.delivery_ids(), {1, 9})  # Book still excluded; Gold Nugget new
        self.assertIn(9, self.lib.accepted_ids())
        self.assertNotIn(2, self.lib.accepted_ids())  # removed by the source
        log = self.store.source_log()
        self.assertIsNotNone(log[WIKI]["last_success"])
        self.assertEqual(log[tibia_client.SOURCE_ID]["last_error"], "not installed")
        self.assertEqual(read_json(self.store.delivery_path)["items"].keys(), NEW["items"].keys())

    def test_keep_removed_items(self):
        U.apply_update(self.review(), self.lib, self.store, keep_removed=True)
        self.assertIn(2, self.lib.accepted_ids())
        self.assertEqual(self.state.delivery_added, [2])

    def test_failed_source_keeps_cache(self):
        U.apply_update(self.review(wiki_ok=False), self.lib, self.store)
        self.assertEqual(read_json(self.store.delivery_path)["items"].keys(), OLD["items"].keys())
        self.assertEqual(self.lib.delivery_ids(), {1, 2, 3})


if __name__ == "__main__":
    unittest.main()
