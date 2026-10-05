"""Strictness levels: ready-made Accepted Loot lists, from Soft (almost everything worth anything) to
Uber+1 Strict (only the most valuable items), like loot-filter strictness in other games.

Every item gets a tier, S (best) to F, or junk:

1. a fixed rule for that item ID, if the rules have one (coins are always S);
2. otherwise the highest price an NPC pays for it (official client data), bracketed by ``price_tiers``;
3. otherwise, for items no NPC buys: quest items and items that can't be sold on the Market are junk, and
   the rest are tiered by Market category (``unpriced_category_tiers``), since market-only gear and soul
   cores are often worth more than anything an NPC pays.

A level accepts every tier from S down to its lowest tier. The rules ship in ``data/tier_rules.json``; a
``tier_rules.json`` in the app's data folder overrides them entry by entry (so it only needs the lines
you want to change).
"""

import json
from pathlib import Path

from .hunts import best_npc_price
from .i18n import _
from .storage import read_json

TIERS = ("S", "A", "B", "C", "D", "E", "F", "junk")
# (id, name, lowest tier accepted)
LEVELS = (
    ("soft", _("Soft"), "F"),
    ("regular", _("Regular"), "E"),
    ("semi", _("Semi-Strict"), "D"),
    ("strict", _("Strict"), "C"),
    ("very", _("Very Strict"), "B"),
    ("uber", _("Uber Strict"), "A"),
    ("uber1", _("Uber+1 Strict"), "S"),
)
LEVEL_IDS = {lid for lid, _name, _tier in LEVELS}
RULES_FILE = "tier_rules.json"
_BUNDLED = Path(__file__).parent / "data" / RULES_FILE

REASON_RULE, REASON_NPC, REASON_QUEST, REASON_UNSELLABLE, REASON_CATEGORY = (
    "rule", "npc", "quest", "unsellable", "category")
REASON_TEXT = {
    REASON_RULE: _("Fixed in the tier rules"),
    REASON_NPC: _("By the highest NPC buy price"),
    REASON_QUEST: _("Quest item that no NPC buys"),
    REASON_UNSELLABLE: _("No NPC buys it and it can't be sold on the Market"),
    REASON_CATEGORY: _("No NPC buys it; tiered by its Market category"),
}


def level_name(level: str) -> str:
    return next((name for lid, name, _tier in LEVELS if lid == level), "")


def load_rules(user_dir: Path | None = None) -> dict:
    """The bundled rules, with each entry in the user's own tier_rules.json overriding the same entry."""
    rules = json.loads(_BUNDLED.read_text(encoding="utf-8"))
    if user_dir is not None:
        mine = read_json(user_dir / RULES_FILE, default=None)
        if isinstance(mine, dict):
            for key in ("price_tiers", "unpriced_category_tiers", "items"):
                if isinstance(mine.get(key), dict):
                    rules[key] = {**rules[key], **mine[key]}
            if mine.get("unpriced_default") in TIERS:
                rules["unpriced_default"] = mine["unpriced_default"]
    return rules


def tier_of(item: dict, wiki: dict | None, rules: dict) -> tuple[str, str]:
    """(tier, reason) for one client item."""
    fixed = rules.get("items", {}).get(str(item["id"]))
    if fixed in TIERS:
        return fixed, REASON_RULE
    price = best_npc_price(item)
    if price:
        brackets = sorted(((v, t) for t, v in rules["price_tiers"].items() if t in TIERS and isinstance(v, int)),
                          reverse=True)
        return next((t for floor, t in brackets if price >= floor), "junk"), REASON_NPC
    if (wiki or {}).get("primarytype") == "Quest Items":
        return "junk", REASON_QUEST
    if item.get("market_category") is None:
        return "junk", REASON_UNSELLABLE
    tier = rules.get("unpriced_category_tiers", {}).get(item["category"], rules.get("unpriced_default", "junk"))
    return (tier if tier in TIERS else "junk"), REASON_CATEGORY


def tier_map(library, rules: dict) -> dict[int, tuple[str, str]]:
    return {cid: tier_of(item, library.wiki_by_id.get(cid), rules) for cid, item in library.items_by_id.items()}


def accepts(level: str, tier: str) -> bool:
    lowest = next((t for lid, _name, t in LEVELS if lid == level), None)
    return lowest is not None and tier != "junk" and TIERS.index(tier) <= TIERS.index(lowest)


def level_ids(tiers: dict[int, tuple[str, str]], level: str) -> list[int]:
    return sorted(cid for cid, (tier, _reason) in tiers.items() if accepts(level, tier))
