import unittest

from tibia_loot_manager import library as L
from tibia_loot_manager import lootfile
from tibia_loot_manager.state import UserState


def item(cid, name, **kw):
    return {"id": cid, "name": name, "category": "Others", "npc_offers": [], **kw}


ITEMS = {i["id"]: i for i in (
    item(3031, "gold coin"), item(3035, "platinum coin"),
    item(2915, "lit lamp"),  # wiki lists 2915 on the "Lamp" page → conflicting
    item(44440, "yapunac dagger"),  # wiki actualname has a typo but the title matches → verified
    item(36794, "Brainstealer's tissue"),  # apostrophe spacing differs → verified
    item(500, "rare thing"),  # no wiki page lists 500 → verified from the client alone
)}
PAGES = {
    "Gold Coin": {"title": "Gold Coin", "itemids": [3031], "actualname": "gold coin"},
    "Lamp": {"title": "Lamp", "itemids": [2914, 2915], "actualname": "lamp"},
    "Yapunac Dagger": {"title": "Yapunac Dagger", "itemids": [44440], "actualname": "yapnuac dagger"},
    "Brainstealer's Tissue": {"title": "Brainstealer's Tissue", "itemids": [36794],
                              "actualname": "Brainstealer 's tissue"},
}


def make(items=ITEMS, state=None):
    catalog = {"source": {}, "items": {str(k): v for k, v in items.items()}}
    return L.Library(catalog, None, {}, state or UserState(), {"source": {}, "pages": PAGES})


class MappingStateTest(unittest.TestCase):
    def test_states(self):
        lib = make()
        self.assertEqual(lib.client_id_status(3031), L.OK_CLIENT_AND_WIKI)
        self.assertEqual(lib.client_id_status(500), L.OK_CLIENT_ONLY)
        self.assertEqual(lib.client_id_status(44440), L.OK_CLIENT_AND_WIKI)
        self.assertEqual(lib.client_id_status(36794), L.OK_CLIENT_AND_WIKI)
        self.assertEqual(lib.client_id_status(2915), L.WIKI_NAME_CONFLICT)
        self.assertEqual(lib.conflicting_pages(2915), ["Lamp"])
        self.assertEqual(lib.client_id_status(99999), L.NOT_IN_CLIENT)

    def test_conflicting_and_unverified_items_are_never_exported(self):
        state = UserState(accepted_follow_delivery=False, accepted_extra=[3031, 2915, 99999, 500])
        lib = make(state=state)
        self.assertEqual(lib.accepted_ids(), {3031, 500})
        states = {e.client_id: e.mapping_state for e in lib.accepted_entries()[0]}
        self.assertEqual(states[2915], L.CONFLICTING)
        self.assertEqual(states[99999], L.UNVERIFIED)

    def test_reference_check_blocks_everything(self):
        broken = {**ITEMS, 3031: item(3031, "platinum coin")}
        lib = make(broken, UserState(accepted_follow_delivery=False, accepted_extra=[500, 3035]))
        self.assertTrue(lib.reference_issues)
        self.assertEqual(lib.client_id_status(500), L.CLIENT_DATA_SUSPECT)
        self.assertEqual(lib.accepted_ids(), set())

    def test_name_key(self):
        self.assertEqual(L.name_key("Brainstealer 's  tissue"), L.name_key("brainstealer's tissue"))

    def test_file_contains_ids_not_names(self):
        data = lootfile.accepted_only_file(sorted(make(state=UserState(
            accepted_follow_delivery=False, accepted_extra=[3031])).accepted_ids()))
        self.assertEqual(data["whitelistTypes"], [3031])
        self.assertTrue(all(isinstance(x, int) for x in data["whitelistTypes"]))


if __name__ == "__main__":
    unittest.main()
