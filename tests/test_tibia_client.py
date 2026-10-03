import unittest

from tibia_loot_manager.sources import tibia_client


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field(num: int, value) -> bytes:
    if isinstance(value, int):
        return varint(num << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint(num << 3 | 2) + varint(len(value)) + value


def obj(oid, name, take=True, cyclopedia=None, market=None, npcs=(), cumulative=False, trade_as=None):
    flags = b""
    if cumulative:
        flags += field(6, 1)
    if take:
        flags += field(18, 1)
    if market is not None:
        flags += field(36, field(1, market) + field(2, trade_as or oid))
    for npc in npcs:
        flags += field(40, b"".join(field(k, v) for k, v in npc))
    if cyclopedia is not None:
        flags += field(44, field(1, cyclopedia))
    return field(1, field(1, oid) + field(3, flags) + field(4, name))


class AppearancesTest(unittest.TestCase):
    def setUp(self):
        data = b"".join([
            obj(17829, "buckle", cyclopedia=17829, market=1,
                npcs=[[(1, "Rashid"), (2, "Darashia"), (3, 0), (4, 7000)],
                      [(1, "Rashid"), (2, "Darashia"), (3, 0), (4, 7000)]]),  # duplicate offer
            obj(236, "strong health potion", cyclopedia=236, market=10, cumulative=True,
                npcs=[[(1, "Minzy"), (2, "Swamp"), (3, 10), (4, 0), (6, "Favour")]]),
            obj(5000, "variant", cyclopedia=17829),  # points to another item: not canonical
            obj(5001, "wall", take=False, cyclopedia=5001),  # not pickupable
            obj(5002, "", cyclopedia=5002),  # unnamed
            obj(5003, "odd thing", cyclopedia=5003, market=99),
            obj(7184, "buckle", cyclopedia=7184, market=1, trade_as=17829),  # market variant of 17829
            field(2, b"outfits are ignored"),
        ])
        self.items = tibia_client.parse_appearances(data)

    def test_only_named_pickupable_canonical_items(self):
        self.assertEqual(sorted(self.items), [236, 5003, 17829])

    def test_market_variants_fold_into_main_item(self):
        self.assertEqual(self.items[17829]["variants"], [7184])
        self.assertNotIn("variants", self.items[236])

    def test_npc_price_direction_and_dedup(self):
        offers = self.items[17829]["npc_offers"]
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["npc_buys_for"], 7000)
        self.assertIsNone(offers[0]["npc_sells_for"])

    def test_quest_currency(self):
        offer = self.items[236]["npc_offers"][0]
        self.assertEqual((offer["npc_sells_for"], offer["currency"]), (10, "Favour"))
        self.assertTrue(self.items[236]["stackable"])

    def test_categories(self):
        self.assertEqual(self.items[17829]["category"], "Armors")
        self.assertEqual(self.items[5003]["category"], "Category 99")


if __name__ == "__main__":
    unittest.main()
