import json
import tempfile
import unittest
from pathlib import Path

from tibia_loot_manager import profiles, strictness
from tibia_loot_manager.webui.service import AppService, UserError

from .test_webui import make_store

RULES = strictness.load_rules()


def npc(price):
    return [{"npc": "Rashid", "location": "Svargrond", "npc_buys_for": price, "currency": "gold"}]


def tier(item, wiki=None):
    return strictness.tier_of({"category": "Others", "market_category": 1, "npc_offers": [], **item}, wiki, RULES)


class TierTest(unittest.TestCase):
    def test_npc_price_brackets(self):
        self.assertEqual(tier({"id": 1, "npc_offers": npc(150_000)}), ("S", "npc"))
        self.assertEqual(tier({"id": 1, "npc_offers": npc(1_000)}), ("C", "npc"))
        self.assertEqual(tier({"id": 1, "npc_offers": npc(49)}), ("F", "npc"))
        # the highest NPC price counts
        self.assertEqual(tier({"id": 1, "npc_offers": npc(10) + npc(6_000)})[0], "B")

    def test_items_no_npc_buys(self):
        self.assertEqual(tier({"id": 1, "category": "Soul Cores"}), ("B", "category"))
        self.assertEqual(tier({"id": 1, "category": "Decoration"}), ("junk", "category"))
        self.assertEqual(tier({"id": 1, "category": "Soul Cores", "market_category": None}), ("junk", "unsellable"))
        self.assertEqual(tier({"id": 1, "category": "Valuables"}, {"primarytype": "Quest Items"}),
                         ("junk", "quest"))

    def test_fixed_rules_win(self):
        self.assertEqual(tier({"id": 3031, "market_category": None}), ("S", "rule"))

    def test_levels_are_nested(self):
        tiers = {i: (t, "npc") for i, t in enumerate(strictness.TIERS)}
        counts = [len(strictness.level_ids(tiers, lid)) for lid, _n, _t in strictness.LEVELS]
        self.assertEqual(counts, [7, 6, 5, 4, 3, 2, 1])  # Soft takes S..F, Uber+1 only S; junk never

    def test_user_rules_override_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / strictness.RULES_FILE).write_text(json.dumps(
                {"unpriced_category_tiers": {"Decoration": "A"}, "unpriced_default": "nonsense"}))
            rules = strictness.load_rules(Path(tmp))
        self.assertEqual(rules["unpriced_category_tiers"]["Decoration"], "A")
        self.assertEqual(rules["unpriced_category_tiers"]["Soul Cores"], "B")  # untouched entries stay
        self.assertEqual(rules["unpriced_default"], "junk")  # an invalid value is ignored
        self.assertEqual(rules["price_tiers"], RULES["price_tiers"])


class LevelServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AppService(make_store(Path(self.tmp.name)))
        items = self.svc.library.items_by_id
        items[17829].update(npc_offers=npc(7_000), market_category=1)  # buckle: B
        items[2915].update(npc_offers=npc(60), market_category=1)  # lit lamp: E
        self.svc._tiers = None

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        return sorted(r["name"] for r in self.svc.accepted()["rows"])

    def test_overview_counts(self):
        o = self.svc.strictness_overview()
        counts = {lv["id"]: lv["count"] for lv in o["levels"]}
        self.assertEqual((counts["soft"], counts["regular"], counts["very"], counts["uber1"]), (4, 4, 3, 2))

    def test_apply_keeps_manual_changes(self):
        self.svc.set_follow_delivery(False)
        self.svc.toggle_accepted("2915")  # added by hand
        self.svc.strictness_apply("uber1")
        self.assertEqual(self.names(), ["gold coin", "lit lamp", "platinum coin"])
        self.svc.toggle_accepted("3035")  # removed by hand stays removed
        self.svc.strictness_apply("strict")
        self.assertEqual(self.names(), ["buckle", "gold coin", "lit lamp"])
        rows = {r["name"]: r["from"] for r in self.svc.accepted()["rows"]}
        self.assertEqual(rows["buckle"], "Strictness level (Strict)")

    def test_preview_matches_apply(self):
        self.svc.set_follow_delivery(False)
        p = self.svc.strictness_preview("very")
        self.assertEqual((p["add"], p["remove"], p["total_after"]), (["buckle", "gold coin", "platinum coin"], [], 3))
        self.assertEqual(self.names(), [])  # a preview changes nothing
        self.svc.strictness_apply("very")
        self.assertEqual(len(self.names()), p["total_after"])

    def test_price_changes_wait_for_review(self):
        self.svc.set_follow_delivery(False)
        self.svc.strictness_apply("very")
        self.svc.library.items_by_id[2915]["npc_offers"] = npc(25_000)  # lit lamp is now A
        self.svc._tiers = None
        self.assertEqual(self.svc.accepted()["level"]["pending"], {"add": ["lit lamp"], "remove": []})
        self.assertNotIn("lit lamp", self.names())  # not applied yet
        self.svc.strictness_accept_changes()
        self.assertIn("lit lamp", self.names())
        self.assertIsNone(self.svc.accepted()["level"]["pending"])

    def test_pending_counts_only_real_list_changes(self):
        self.svc.strictness_apply("uber1")  # buckle is on the list through the Delivery Task list
        self.svc.library.items_by_id[17829]["npc_offers"] = npc(200_000)  # ...and now also in Uber+1
        self.svc._tiers = None
        self.assertEqual(self.svc.accepted()["level"]["pending"], {"add": [], "remove": []})
        self.svc.strictness_accept_changes()
        self.assertIn(17829, self.svc.library.state.accepted_preset_items)
        self.assertIsNone(self.svc.accepted()["level"]["pending"])

    def test_turn_off_and_unknown_level(self):
        self.svc.strictness_apply("soft")
        self.svc.strictness_apply("")
        self.assertEqual(self.svc.library.state.accepted_preset_items, [])
        with self.assertRaises(UserError):
            self.svc.strictness_apply("ultra")

    def test_level_belongs_to_the_profile(self):
        self.svc.strictness_apply("uber1")
        data = profiles.export_data(self.svc.library.state, self.svc.library.state.active_profile, "t")
        name, snap = profiles.parse_import(json.loads(json.dumps(data)))
        self.assertEqual((snap["preset"], sorted(snap["preset_items"])), ("uber1", [3031, 3035]))
        self.svc.profile_create("Other", "empty")
        self.assertEqual(self.svc.library.state.accepted_preset, "")
        self.svc.profile_switch(next(p for p in self.svc.library.state.profiles if p != self.svc.library.state.active_profile))
        self.assertEqual(self.svc.library.state.accepted_preset, "uber1")

    def test_item_shows_tier(self):
        info = self.svc.item("17829")["tier"]
        self.assertEqual(info["tier"], "B")
        self.assertIn("Very Strict", info["levels"])
        self.assertNotIn("Uber Strict", info["levels"])


if __name__ == "__main__":
    unittest.main()
