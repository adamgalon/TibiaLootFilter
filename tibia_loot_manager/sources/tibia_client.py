"""Read the item catalog from the locally installed Tibia client.

The client ships its object definitions as a protobuf file
(``assets/appearances-<hash>.dat``, listed in ``assets/catalog-content.json``).
Each object carries its client type ID next to its in-game name, so this is the
authoritative source for IDs used in ``lootBlackWhitelist.json``. Nothing is
sent anywhere; the file is only read.

Only the handful of fields this app needs are decoded. Field numbers follow
the publicly documented ``appearances.proto`` layout and were checked against
client 15.33 (e.g. object 17829 "buckle": Rashid buys it for 7,000 gold).
"""

import json
from dataclasses import dataclass
from pathlib import Path

from ..storage import utc_now_iso

SOURCE_ID = "tibia_client"
PARSER_VERSION = 3  # bump when parsing rules change so cached catalogs are rebuilt

# appearances.proto field numbers
_APPEARANCES_OBJECT = 1
_APPEARANCE_ID = 1
_APPEARANCE_FRAME_GROUP = 2
_APPEARANCE_FLAGS = 3
_APPEARANCE_NAME = 4
_FLAG_CUMULATIVE = 6
_FLAG_TAKE = 18
_FLAG_MARKET = 36
_FLAG_NPCSALEDATA = 40
_FLAG_CYCLOPEDIA_ITEM = 44
_MARKET_CATEGORY = 1
_MARKET_TRADE_AS = 2
_NPC_NAME = 1
_NPC_LOCATION = 2
_NPC_SALE_PRICE = 3  # price the NPC charges the player
_NPC_BUY_PRICE = 4  # price the NPC pays the player
_NPC_CURRENCY_OBJECT = 5
_NPC_CURRENCY_QUEST_FLAG = 6
_CYCLOPEDIA_TYPE = 1
_FRAME_GROUP_SPRITE_INFO = 3
_SPRITE_PATTERN_WIDTH, _SPRITE_PATTERN_HEIGHT, _SPRITE_PATTERN_DEPTH, _SPRITE_LAYERS = 1, 2, 3, 4
_SPRITE_IDS = 5
_SPRITE_ANIMATION = 6
_ANIMATION_PHASE = 6
_PHASE_DURATION_MIN = 1

# Client market categories. Unknown numbers are shown as "Category N" rather than guessed.
MARKET_CATEGORIES = {
    1: "Armors", 2: "Amulets", 3: "Boots", 4: "Containers", 5: "Decoration",
    6: "Food", 7: "Helmets and Hats", 8: "Legs", 9: "Others", 10: "Potions",
    11: "Rings", 12: "Runes", 13: "Shields", 14: "Tools", 15: "Valuables",
    16: "Ammunition", 17: "Axes", 18: "Clubs", 19: "Distance Weapons",
    20: "Swords", 21: "Wands and Rods", 22: "Premium Scrolls", 23: "Tibia Coins",
    24: "Creature Products", 25: "Quivers", 26: "Soul Cores", 27: "Fist Weapons",
}
NOT_MARKETABLE = "Not marketable"


class ClientDataError(Exception):
    pass


def category_name(market_category: int | None) -> str:
    if market_category is None:
        return NOT_MARKETABLE
    return MARKET_CATEGORIES.get(market_category, f"Category {market_category}")


# --- minimal protobuf wire-format reader -----------------------------------

def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, pos
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, value) where value is int for varints and bytes otherwise."""
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 2:
            length, pos = _varint(buf, pos)
            value = buf[pos:pos + length]
            pos += length
        elif wire == 5:
            value = buf[pos:pos + 4]
            pos += 4
        elif wire == 1:
            value = buf[pos:pos + 8]
            pos += 8
        else:
            raise ClientDataError(f"Unsupported protobuf wire type {wire}")
        yield field, value


def _group(buf: bytes) -> dict[int, list]:
    out: dict[int, list] = {}
    for field, value in _fields(buf):
        out.setdefault(field, []).append(value)
    return out


def _sub(group: dict[int, list], field: int) -> dict[int, list]:
    """The first nested message in ``field``, or {} if it is absent or not a message."""
    value = group.get(field, [None])[0]
    return _group(value) if isinstance(value, bytes) else {}


def _ints(values: list) -> list[int]:
    """Repeated varints, whether sent one by one or packed into a single bytes field."""
    out = []
    for v in values:
        if isinstance(v, int):
            out.append(v)
            continue
        pos = 0
        while pos < len(v):
            n, pos = _varint(v, pos)
            out.append(n)
    return out


def _sprite_info(obj: dict[int, list]) -> dict | None:
    """Sprite IDs of the item's icon (first pattern and layer) for each animation phase."""
    info = _sub(_sub(obj, _APPEARANCE_FRAME_GROUP), _FRAME_GROUP_SPRITE_INFO)
    ids = _ints(info.get(_SPRITE_IDS, []))
    if not ids:
        return None
    per_phase = 1
    for key in (_SPRITE_PATTERN_WIDTH, _SPRITE_PATTERN_HEIGHT, _SPRITE_PATTERN_DEPTH, _SPRITE_LAYERS):
        value = info.get(key, [1])[0]
        per_phase *= value if isinstance(value, int) and value > 0 else 1
    phases = [_group(p) for p in _sub(info, _SPRITE_ANIMATION).get(_ANIMATION_PHASE, []) if isinstance(p, bytes)]
    count = max(1, min(len(phases), len(ids) // per_phase))
    return {"ids": [ids[i * per_phase] for i in range(count)],
            "durations": [max(20, p.get(_PHASE_DURATION_MIN, [100])[0]) for p in phases[:count]] if count > 1 else []}


# --- discovery ----------------------------------------------------------------

@dataclass
class ClientInstall:
    package_dir: Path
    version: str | None
    appearances_path: Path


def locate_client(package_dir: Path) -> ClientInstall:
    package_dir = Path(package_dir)
    catalog_path = package_dir / "assets" / "catalog-content.json"
    try:
        entries = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ClientDataError(f"Client asset catalog not found: {catalog_path}") from None
    except (OSError, ValueError) as e:
        raise ClientDataError(f"Could not read {catalog_path}: {e}") from None
    files = [e.get("file") for e in entries if isinstance(e, dict) and e.get("type") == "appearances"]
    if not files or not files[0]:
        raise ClientDataError("The client asset catalog lists no appearances file.")
    appearances = package_dir / "assets" / files[0]
    if not appearances.is_file():
        raise ClientDataError(f"Appearances file is missing: {appearances}")
    version = None
    try:
        version = json.loads((package_dir / "package.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        pass
    return ClientInstall(package_dir, version, appearances)


# --- parsing ------------------------------------------------------------------

def _decode(b: bytes) -> str:
    return b.decode("utf-8", errors="replace").strip()


def parse_appearances(data: bytes) -> dict[int, dict]:
    """Return {client_id: item} for every pickupable, named, Cyclopedia-canonical object.

    "Canonical" means the object's Cyclopedia type points to itself. That is the
    ID the Cyclopedia (and therefore the loot list) uses for an item; animation
    and state variants that point elsewhere are skipped. Objects the market
    trades as a different ID (e.g. 7184 "baby seal doll" trades as 7183) are
    also folded into that main item and listed under its ``variants``.
    """
    raw: dict[int, dict] = {}
    names_by_id: dict[int, str] = {}
    variants: dict[int, list[int]] = {}
    for field, value in _fields(data):
        if field != _APPEARANCES_OBJECT or not isinstance(value, bytes):
            continue
        obj = _group(value)
        oid = obj.get(_APPEARANCE_ID, [None])[0]
        name = _decode(obj.get(_APPEARANCE_NAME, [b""])[0])
        if oid is None or not name:
            continue
        names_by_id[oid] = name
        flags = _sub(obj, _APPEARANCE_FLAGS)
        if not flags.get(_FLAG_TAKE, [0])[0]:
            continue
        if _sub(flags, _FLAG_CYCLOPEDIA_ITEM).get(_CYCLOPEDIA_TYPE, [None])[0] != oid:
            continue
        market = _sub(flags, _FLAG_MARKET) or None
        market_category = market.get(_MARKET_CATEGORY, [None])[0] if market else None
        trade_as = market.get(_MARKET_TRADE_AS, [oid])[0] if market else oid
        if trade_as != oid:
            variants.setdefault(trade_as, []).append(oid)
            continue
        raw[oid] = {
            "id": oid,
            "name": name,
            "market_category": market_category,
            "category": category_name(market_category),
            "stackable": bool(flags.get(_FLAG_CUMULATIVE, [0])[0]),
            "sprite": _sprite_info(obj),
            "_npc": [b for b in flags.get(_FLAG_NPCSALEDATA, []) if isinstance(b, bytes)],
        }

    for item in raw.values():
        offers, seen = [], set()
        for blob in item.pop("_npc"):
            npc = _group(blob)
            currency = "gold"
            if npc.get(_NPC_CURRENCY_QUEST_FLAG):
                currency = _decode(npc[_NPC_CURRENCY_QUEST_FLAG][0])
            elif npc.get(_NPC_CURRENCY_OBJECT, [0])[0]:
                cid = npc[_NPC_CURRENCY_OBJECT][0]
                currency = names_by_id.get(cid, f"item #{cid}")
            offer = {
                "npc": _decode(npc.get(_NPC_NAME, [b""])[0]),
                "location": _decode(npc.get(_NPC_LOCATION, [b""])[0]),
                "npc_sells_for": npc.get(_NPC_SALE_PRICE, [0])[0] or None,
                "npc_buys_for": npc.get(_NPC_BUY_PRICE, [0])[0] or None,
                "currency": currency,
            }
            key = tuple(offer.values())
            if key not in seen and (offer["npc_sells_for"] or offer["npc_buys_for"]):
                seen.add(key)
                offers.append(offer)
        item["npc_offers"] = offers
    for main, ids in variants.items():
        if main in raw:
            raw[main]["variants"] = sorted(ids)
    return raw


def read_catalog(package_dir: Path) -> dict:
    """Read the installed client and return a catalog snapshot ready for caching."""
    install = locate_client(package_dir)
    try:
        data = install.appearances_path.read_bytes()
        items = parse_appearances(data)
    except (OSError, IndexError, ValueError, ClientDataError) as e:
        raise ClientDataError(f"Could not parse {install.appearances_path.name}: {e}") from None
    if not items:
        raise ClientDataError("No items were found in the client appearances file.")
    return {
        "source": {
            "id": SOURCE_ID,
            "parser_version": PARSER_VERSION,
            "client_version": install.version,
            "appearances_file": install.appearances_path.name,
            "package_dir": str(install.package_dir),
            "read_at": utc_now_iso(),
        },
        "items": {str(k): v for k, v in sorted(items.items())},
    }
