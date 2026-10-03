import unittest

from tibia_loot_manager.sources import tibiawiki

DELIVERY = """Intro text.
== Armors ==
{| class="wikitable sortable"
! !! Name !! Min. Required !! Max. Required !! NPC Buy Price
|-
| {{ilink|Buckle}} || [[Buckle]] || 10 || 20 || 7,000
|-
| {{ilink|Book of Faith}} || [[Book of Faith]] || 10 || 20 || --
|}

== Others ==
{| class="wikitable sortable"
! !! Name !! Min. Required !! Max. Required
|-
| {{ilink|Exalted Core}} || [[Exalted Core]] || 5 || 10
|-
| {{ilink|Sliver}} || [[Sliver|Slivers]] || 50 || 100
|}
"""

INFOBOX = """{{Infobox Object|List={{{1|}}}|GetValue={{{GetValue|}}}
| name          = Buckle
| actualname    = buckle
| itemid        = 17829, 17830
| droppedby     = {{Dropped By|Lost Basher|Lost Exile}}
| npcvalue      = 7000
| npcprice      = 0
| notes         = First line with a {{Template|x}}.
| second line of notes
| task_item     = yes
}}
Article text."""


class DeliveryParsingTest(unittest.TestCase):
    def test_rows_and_categories(self):
        rows = tibiawiki.parse_delivery_task(DELIVERY)
        self.assertEqual([r["title"] for r in rows], ["Buckle", "Book of Faith", "Exalted Core", "Sliver"])
        self.assertEqual(rows[0], {"task_category": "Armors", "min_qty": 10, "max_qty": 20,
                                   "npc_buy_price": 7000, "title": "Buckle"})

    def test_missing_price_is_none_not_guessed(self):
        rows = {r["title"]: r for r in tibiawiki.parse_delivery_task(DELIVERY)}
        self.assertIsNone(rows["Book of Faith"]["npc_buy_price"])
        self.assertIsNone(rows["Exalted Core"]["npc_buy_price"])  # table has no price column
        self.assertEqual(rows["Exalted Core"]["task_category"], "Others")

    def test_link_target_not_label(self):
        rows = {r["title"]: r for r in tibiawiki.parse_delivery_task(DELIVERY)}
        self.assertIn("Sliver", rows)


class InfoboxParsingTest(unittest.TestCase):
    def test_fields(self):
        record = tibiawiki.item_record("Buckle", INFOBOX, "2026-08-22T19:31:27Z")
        self.assertEqual(record["itemids"], [17829, 17830])
        self.assertEqual(record["dropped_by"], ["Lost Basher", "Lost Exile"])
        self.assertEqual(record["npc_buys_for"], 7000)
        self.assertIsNone(record["npc_sells_for"])  # 0 means the NPC does not sell it
        self.assertTrue(record["task_item"])
        self.assertEqual(record["url"], "https://tibia.fandom.com/wiki/Buckle")

    def test_multiline_and_nested_values(self):
        box = tibiawiki.parse_infobox(INFOBOX)
        self.assertIn("second line of notes", box["notes"])
        self.assertEqual(box["task_item"], "yes")

    def test_empty_dropped_by(self):
        self.assertEqual(tibiawiki.parse_dropped_by("{{Dropped By}}"), [])


if __name__ == "__main__":
    unittest.main()
