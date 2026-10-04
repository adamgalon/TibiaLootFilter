import unittest

from tibia_loot_manager import updates as U
from tibia_loot_manager.library import Library, match_wiki_pages
from tibia_loot_manager.sources.tibiawiki import TibiaWikiSource
from tibia_loot_manager.state import UserState

ITEMS = {
    235: {"id": 235, "name": "bag", "category": "Others", "npc_offers": []},
    2853: {"id": 2853, "name": "bag", "category": "Containers", "npc_offers": []},
    7183: {"id": 7183, "name": "baby seal doll", "category": "Decoration", "npc_offers": [], "variants": [7184]},
    903: {"id": 903, "name": "badger fur", "category": "Decoration", "npc_offers": []},
    10299: {"id": 10299, "name": "badger fur", "category": "Creature Products", "npc_offers": []},
    3031: {"id": 3031, "name": "gold coin", "category": "Others", "npc_offers": []},
    3035: {"id": 3035, "name": "platinum coin", "category": "Others", "npc_offers": []},
}
PAGES = {
    "Bag": {"title": "Bag", "itemids": [2853], "actualname": "bag", "primarytype": "Containers"},
    "Bag (Ahmet)": {"title": "Bag (Ahmet)", "itemids": [235], "actualname": "bag", "primarytype": "Quest Items"},
    "Baby Seal Doll": {"title": "Baby Seal Doll", "itemids": [7183, 7184], "actualname": "baby seal doll"},
    "Badger Fur": {"title": "Badger Fur", "itemids": [10299], "actualname": "badger fur"},
    # two pages claiming 903, neither named like the client item: no match rather than a guess
    "Fur Rug A": {"title": "Fur Rug A", "itemids": [903], "actualname": "fur rug"},
    "Fur Rug B": {"title": "Fur Rug B", "itemids": [903], "actualname": "old fur rug"},
}


class MatchTest(unittest.TestCase):
    def test_matches(self):
        matched = match_wiki_pages(ITEMS, PAGES)
        self.assertEqual(matched[235]["title"], "Bag (Ahmet)")
        self.assertEqual(matched[2853]["title"], "Bag")
        self.assertEqual(matched[7183]["title"], "Baby Seal Doll")
        self.assertNotIn(903, matched)

    def test_notes(self):
        catalog = {"source": {}, "items": {str(k): v for k, v in ITEMS.items()}}
        lib = Library(catalog, None, {}, UserState(), {"source": {}, "pages": PAGES})
        self.assertEqual(lib.item_note(235), "Bag (Ahmet) (Quest Items)")
        self.assertEqual(lib.item_note(7183), "variants: 7184")
        self.assertIn("no wiki page confirmed", lib.item_note(903))
        self.assertEqual(lib.wiki_record_for(10299)["title"], "Badger Fur")


class FakeHttp:
    def __init__(self, listing, contents):
        self.listing, self.contents, self.content_requests = listing, contents, []

    def get_json(self, url, params):
        if params.get("generator") == "embeddedin":
            return {"query": {"pages": [{"pageid": p, "title": t, "lastrevid": r} for p, (t, r) in self.listing.items()]}}
        ids = [int(x) for x in params["pageids"].split("|")]
        self.content_requests.append(ids)
        return {"query": {"pages": [{
            "pageid": i, "title": self.listing[i][0],
            "revisions": [{"revid": self.listing[i][1], "timestamp": "2026-10-01T00:00:00Z",
                           "slots": {"main": {"content": self.contents[i]}}}]} for i in ids]}}


def box(itemid):
    return f"{{{{Infobox Object\n| name = X\n| itemid = {itemid}\n}}}}"


class IncrementalFetchTest(unittest.TestCase):
    def test_only_changed_pages_are_downloaded(self):
        http = FakeHttp({1: ("Bag", 10), 2: ("Torch", 20), 3: ("Rookgaard", 5)},
                        {1: box(2853), 2: box(2920), 3: "no infobox ids"})
        first = TibiaWikiSource(http).fetch_object_index(None)
        self.assertEqual(sorted(first["pages"]), ["Bag", "Torch"])  # pages without item IDs are dropped

        http.listing[2] = ("Torch", 21)
        http.contents[2] = box("2920, 2921")
        http.listing.pop(1)
        http.content_requests.clear()
        second = TibiaWikiSource(http).fetch_object_index(first)
        self.assertEqual(sum(http.content_requests, []), [2])  # page 3 (no item IDs) is remembered, not refetched
        self.assertEqual(second["pages"]["Torch"]["itemids"], [2920, 2921])

        changes = {(c.kind, c.key) for c in U.diff_wiki_index(first, second)}
        self.assertEqual(changes, {(U.REMOVED, "Bag"), (U.CHANGED, "Torch")})


if __name__ == "__main__":
    unittest.main()
