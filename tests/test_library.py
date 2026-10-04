import unittest

from tibia_loot_manager import library as L
from tibia_loot_manager.state import UserState


def catalog(*items):
    return {"source": {"client_version": "test"},
            "items": {str(i): {"id": i, "name": n, "category": "Others", "npc_offers": []} for i, n in items}}


def delivery(*rows):
    return {"source": {"url": "u"}, "items": {
        t: {"title": t, "task_category": "Armors", "min_qty": 1, "max_qty": 2, "npc_buy_price": None,
            "wiki": {"itemids": ids, "actualname": None}} for t, ids in rows}}


CATALOG = catalog((1, "buckle"), (2, "sliver"), (3, "book"), (4, "book"), (9, "gold coin"))
DELIVERY = delivery(("Buckle", [1]), ("Sliver", [2]), ("Book", [3, 4]), ("Ghost Item", [77]), ("Nameless", []))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.lib = L.Library(CATALOG, DELIVERY, {}, UserState())

    def test_statuses(self):
        r = self.lib.delivery_resolution
        self.assertEqual(r["Buckle"], (1, L.VERIFIED))
        self.assertEqual(r["Book"], (None, L.AMBIGUOUS))  # two client items named "book" both listed
        self.assertEqual(r["Ghost Item"], (None, L.NOT_IN_CLIENT))
        self.assertEqual(r["Nameless"], (None, L.NO_WIKI_ID))

    def test_wiki_id_disagreeing_with_name_is_not_trusted(self):
        lib = L.Library(CATALOG, delivery(("Buckle", [2])), {}, UserState())
        self.assertEqual(lib.delivery_resolution["Buckle"], (None, L.ID_MISMATCH))

    def test_without_client_data_nothing_is_verified(self):
        lib = L.Library(None, DELIVERY, {}, UserState())
        self.assertEqual(lib.delivery_resolution["Buckle"], (None, L.NO_CLIENT_DATA))
        self.assertEqual(lib.accepted_ids(), set())


class ListEditsTest(unittest.TestCase):
    def setUp(self):
        self.state = UserState()
        self.lib = L.Library(CATALOG, DELIVERY, {}, self.state)

    def test_default_accepted_is_delivery_list(self):
        self.assertEqual(self.lib.accepted_ids(), {1, 2})
        names = {e.name for e in self.lib.accepted_entries()[0]}
        self.assertIn("Ghost Item", names)  # shown, but not exportable

    def test_remove_and_add_delivery(self):
        buckle = next(e for e in self.lib.delivery_entries()[0] if e.client_id == 1)
        self.lib.remove_from_delivery(buckle)
        self.assertNotIn(1, self.lib.delivery_ids())
        self.assertNotIn(1, self.lib.accepted_ids())
        self.lib.add_to_delivery(1)  # re-adding a source item un-excludes it rather than duplicating
        self.assertEqual(self.state.delivery_removed, [])
        self.assertEqual(self.state.delivery_added, [])
        self.lib.add_to_delivery(9)
        self.assertIn(9, self.lib.accepted_ids())

    def test_accepted_edits_are_independent_of_delivery_list(self):
        self.lib.add_to_accepted(9)
        sliver = next(e for e in self.lib.accepted_entries()[0] if e.client_id == 2)
        self.lib.remove_from_accepted(sliver)
        self.assertEqual(self.lib.accepted_ids(), {1, 9})
        self.assertIn(2, self.lib.delivery_ids())  # still a delivery candidate
        self.lib.add_to_accepted(2)
        self.assertEqual(self.lib.accepted_ids(), {1, 2, 9})
        self.assertEqual(self.state.accepted_extra, [9])

    def test_not_following_delivery(self):
        self.state.accepted_follow_delivery = False
        self.lib.add_to_accepted(1)
        self.assertEqual(self.lib.accepted_ids(), {1})

    def test_edits_survive_source_update(self):
        self.lib.remove_from_delivery(next(e for e in self.lib.delivery_entries()[0] if e.client_id == 2))
        self.lib.add_to_delivery(9)
        self.lib.set_delivery(delivery(("Buckle", [1]), ("Sliver", [2]), ("Book", [3])))
        self.assertEqual(self.lib.delivery_ids(), {1, 3, 9})
        self.assertEqual(len(self.lib.delivery_entries()[1]), 1)  # Sliver still excluded

    def test_restore_defaults(self):
        self.lib.add_to_accepted(9)
        self.lib.remove_from_delivery(next(e for e in self.lib.delivery_entries()[0] if e.client_id == 1))
        self.lib.restore_delivery_defaults()
        self.assertEqual(self.lib.accepted_ids(), {1, 2, 9})
        self.lib.restore_accepted_defaults()
        self.assertEqual(self.lib.accepted_ids(), {1, 2})


if __name__ == "__main__":
    unittest.main()
