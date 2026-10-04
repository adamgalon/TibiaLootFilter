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


def frame_group(ids, pattern=(1, 1, 1, 1), phases=()):
    info = b"".join(field(k, v) for k, v in zip((1, 2, 3, 4), pattern))
    info += field(5, b"".join(varint(i) for i in ids))  # packed repeated sprite IDs
    if phases:
        info += field(6, b"".join(field(6, field(1, ms) + field(2, ms)) for ms in phases))
    return field(2, field(3, info))


def obj(oid, name, take=True, cyclopedia=None, market=None, npcs=(), cumulative=False, trade_as=None, frames=b""):
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
    return field(1, field(1, oid) + frames + field(3, flags) + field(4, name))


class AppearancesTest(unittest.TestCase):
    def setUp(self):
        data = b"".join([
            obj(17829, "buckle", cyclopedia=17829, market=1,
                npcs=[[(1, "Rashid"), (2, "Darashia"), (3, 0), (4, 7000)],
                      [(1, "Rashid"), (2, "Darashia"), (3, 0), (4, 7000)]]),  # duplicate offer
            obj(236, "strong health potion", cyclopedia=236, market=10, cumulative=True,
                npcs=[[(1, "Minzy"), (2, "Swamp"), (3, 10), (4, 0), (6, "Favour")]],
                frames=frame_group([500, 501, 502, 503, 504, 505, 506, 507], pattern=(4, 2, 1, 1))),
            obj(2915, "lit lamp", cyclopedia=2915, frames=frame_group([600, 601, 602], phases=(150, 150, 150))),
            obj(5000, "variant", cyclopedia=17829),  # points to another item: not canonical
            obj(5001, "wall", take=False, cyclopedia=5001),  # not pickupable
            obj(5002, "", cyclopedia=5002),  # unnamed
            obj(5003, "odd thing", cyclopedia=5003, market=99),
            obj(7184, "buckle", cyclopedia=7184, market=1, trade_as=17829),  # market variant of 17829
            field(2, b"outfits are ignored"),
        ])
        self.items = tibia_client.parse_appearances(data)

    def test_only_named_pickupable_canonical_items(self):
        self.assertEqual(sorted(self.items), [236, 2915, 5003, 17829])

    def test_sprite_info(self):
        # a stackable's first pattern (one coin/potion) is its icon; other patterns are larger stacks
        self.assertEqual(self.items[236]["sprite"], {"ids": [500], "durations": []})
        self.assertEqual(self.items[2915]["sprite"], {"ids": [600, 601, 602], "durations": [150, 150, 150]})
        self.assertIsNone(self.items[17829]["sprite"])

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
