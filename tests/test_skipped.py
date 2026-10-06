import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tibia_loot_manager import junk, lootfile, profiles
from tibia_loot_manager.sources import tibiamarket
from tibia_loot_manager.sources.http import SourceError
from tibia_loot_manager.webui import service as service_mod
from tibia_loot_manager.webui.service import AppService, UserError

from .test_webui import make_store


def trades(buy, n=10):
    return {"month_average_buy": buy, "buy_offer": buy, "month_sold": n, "month_bought": n, "time": 1790000000.0}


MARKET = [  # what the TibiaMarket API returns for one world (trimmed)
    {"id": 900, **trades(3)},  # rusty sword: NPC 10, market 3 -> junk under 20
    {"id": 901, **trades(7)},  # bone shield: NPC 80 -> junk only from 100 up
    {"id": 902, "month_sold": 0, "month_bought": 0, "buy_offer": 1},  # rare trophy: no trades, never junk
    {"id": 903, **trades(500)},  # dragon scale: market 500
    {"id": 17829, **trades(1)},  # buckle: a Delivery Task item, never junk
    {"id": 3031, **trades(1)},  # gold coin: never junk
]


class FakeHttp:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        if self.fail:
            raise SourceError("Network error: offline")
        if url.endswith("/world_data"):
            return [{"name": "Antica", "last_update": "2026-10-05T15:20:09"}, {"name": "bad name!"}]
        return json.loads(json.dumps(MARKET))


def item(cid, name, npc=None):
    offers = [{"npc": "Trader", "location": "Thais", "npc_buys_for": npc, "currency": "gold"}] if npc else []
    return {"id": cid, "name": name, "category": "Others", "npc_offers": offers, "market_category": 1}


class SkippedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.http = FakeHttp()
        self.svc = AppService(make_store(Path(self.tmp.name)), http=self.http)
        lib = self.svc.library
        for it in (item(900, "rusty sword", 10), item(901, "bone shield", 80), item(902, "rare trophy"),
                   item(903, "dragon scale")):
            lib.items_by_id[it["id"]] = it
        for cid in (900, 901, 902, 903, 17829, 3031):
            page = lib.wiki_by_id.setdefault(cid, {"title": f"Item {cid}", "url": f"https://example.invalid/{cid}"})
            page["dropped_by"] = ["Rat"]

    def tearDown(self):
        self.tmp.cleanup()

    def skipped_names(self):
        return sorted(r["name"] for r in self.svc.skipped()["rows"])

    def test_market_value_needs_trades(self):
        self.assertEqual(junk.market_value(trades(40)), 40)
        self.assertIsNone(junk.market_value({"buy_offer": 40, "month_sold": 1, "month_bought": 1}))
        self.assertIsNone(junk.market_value(None))

    def test_fetch_world_and_worlds(self):
        self.assertEqual([w["name"] for w in self.svc.market_worlds()["worlds"]], ["Antica"])  # bad names dropped
        info = self.svc.market_fetch("Antica")["market"]
        self.assertEqual((info["world"], info["items"]), ("Antica", 6))
        self.assertEqual(self.http.calls[-1][1], {"server": "Antica", "limit": 10000})
        with self.assertRaises(UserError):
            self.svc.market_fetch("../etc")

    def test_failed_fetch_keeps_old_prices(self):
        self.svc.market_fetch("Antica")
        self.svc.http = FakeHttp(fail=True)
        with self.assertRaises(UserError):
            self.svc.market_fetch("Antica")
        self.assertEqual(self.svc.skipped()["market"]["items"], 6)

    def test_junk_needs_both_prices_low(self):
        self.svc.market_fetch("Antica")
        stops = {s["gp"]: s["count"] for s in self.svc.skipped()["recommend"]["stops"]}
        self.assertEqual((stops[10], stops[20], stops[100], stops[1000]), (0, 1, 2, 3))
        with self.assertRaises(UserError):
            self.svc.skipped_apply(True, 0)

    def test_recommendation_needs_market_prices(self):
        with self.assertRaises(UserError):
            self.svc.skipped_preview(True, 100)

    def test_apply_preview_and_manual_changes(self):
        self.svc.market_fetch("Antica")
        p = self.svc.skipped_preview(True, 100)
        self.assertEqual((p["add"], p["total_after"]), (["bone shield", "rusty sword"], 2))
        self.assertEqual(self.skipped_names(), [])  # preview changes nothing
        self.svc.skipped_apply(True, 100)
        self.assertEqual(self.skipped_names(), ["bone shield", "rusty sword"])
        self.svc.toggle_skipped("901")  # keep looting bone shields
        self.svc.toggle_skipped("903")  # skip dragon scales by hand
        self.assertEqual(self.skipped_names(), ["dragon scale", "rusty sword"])
        self.svc.skipped_apply(True, 20)  # manual changes survive a new limit
        self.assertEqual(self.skipped_names(), ["dragon scale", "rusty sword"])
        self.svc.skipped_apply(False, 20)
        self.assertEqual(self.skipped_names(), ["dragon scale"])
        with self.assertRaises(UserError):
            self.svc.toggle_skipped("wiki:Buckle")

    def test_new_prices_wait_for_review(self):
        self.svc.market_fetch("Antica")
        self.svc.skipped_apply(True, 100)
        MARKET[3]["month_average_buy"] = MARKET[3]["buy_offer"] = 50  # dragon scales got cheap
        try:
            self.svc.market_fetch("Antica")
        finally:
            MARKET[3]["month_average_buy"] = MARKET[3]["buy_offer"] = 500
        self.assertEqual(self.svc.skipped()["recommend"]["pending"], {"add": ["dragon scale"], "remove": []})
        self.assertNotIn("dragon scale", self.skipped_names())
        self.svc.skipped_accept_changes()
        self.assertIn("dragon scale", self.skipped_names())

    def test_conflicts_with_accepted_list(self):
        self.svc.market_fetch("Antica")
        self.svc.toggle_accepted("900")
        self.svc.skipped_apply(True, 100)
        self.assertEqual(self.svc.skipped()["conflicts"], ["rusty sword"])

    def test_skipped_list_belongs_to_the_profile(self):
        self.svc.market_fetch("Antica")
        self.svc.skipped_apply(True, 100)
        st = self.svc.library.state
        data = profiles.export_data(st, st.active_profile, "t")
        _name, snap = profiles.parse_import(json.loads(json.dumps(data)))
        self.assertEqual((snap["skip_recommended"], snap["skip_limit"], sorted(snap["skip_items"])), (True, 100, [900, 901]))
        self.svc.profile_create("Other", "empty")
        self.assertEqual(self.skipped_names(), [])

    def test_install_skipped_list(self):
        self.svc.market_fetch("Antica")
        self.svc.skipped_apply(True, 100)
        path = Path(self.tmp.name) / "characterdata" / "111" / lootfile.FILE_NAME
        with mock.patch.object(service_mod, "tibia_status", return_value="closed"):
            p = self.svc.install_preview("111", lootfile.REPLACE, lootfile.MODE_SKIPPED)
            self.assertEqual(p["list_name"], "Skipped Loot")
            with self.assertRaises(UserError):  # the preview was for the Skipped list
                self.svc.install_apply("111", lootfile.REPLACE)
            self.svc.install_apply("111", lootfile.REPLACE, target=lootfile.MODE_SKIPPED)
        data = lootfile.read_file(path)
        self.assertEqual((data["listType"], data["blacklistTypes"], data["whitelistTypes"]),
                         ("blacklist", [900, 901], [3031]))

    def test_item_shows_market_value(self):
        self.assertIsNone(self.svc.item("901")["market"])
        self.svc.market_fetch("Antica")
        m = self.svc.item("901")["market"]
        self.assertEqual((m["world"], m["value"], m["trades"]), ("Antica", 7, 20))
        self.assertTrue(self.svc.item("2915")["market"]["none"])


class WorldNameTest(unittest.TestCase):
    def test_valid_world(self):
        self.assertTrue(tibiamarket.valid_world("Antica"))
        for bad in ("", "antica", "Anti ca", "../x", "A" * 1 + "1"):
            self.assertFalse(tibiamarket.valid_world(bad))


if __name__ == "__main__":
    unittest.main()
