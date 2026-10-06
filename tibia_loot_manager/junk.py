"""Recommended Skipped Loot: items not worth picking up.

An item is junk at a price limit only when *both* sources say it's cheap:

- the highest price an NPC pays for it (official client data) is below the limit, and
- recent Market trades on the user's world (TibiaMarket, a third-party estimate) show what buyers pay is
  below the limit too.

An item without enough recent trades is never called junk: rare and valuable items (boss trophies,
uncommon equipment) often have no trades at all. Only items some creature drops are considered (the
Skipped list only matters for corpses), and Delivery Task items and coins are never junk.
"""

from .hunts import best_npc_price

STOPS = (10, 20, 50, 100, 200, 500, 1000, 2000, 5000)  # slider positions, in gp
DEFAULT_LIMIT = 100
MIN_TRADES = 3  # trades this month before a market price is trusted
NEVER_JUNK = {3031, 3035, 3043}  # gold, platinum and crystal coins


def market_value(rec: dict | None) -> int | None:
    """What a buyer on the Market realistically pays, or None when there are too few trades to tell."""
    if not rec or (rec.get("month_sold") or 0) + (rec.get("month_bought") or 0) < MIN_TRADES:
        return None
    prices = [int(rec[k]) for k in ("month_average_buy", "buy_offer", "day_average_buy") if (rec.get(k) or 0) > 0]
    return max(prices) if prices else 0


def candidates(library, market_items: dict, protect: set[int]) -> dict[int, dict]:
    """{client id: {"npc", "market", "worth"}} for every item that could ever be junk."""
    out = {}
    for cid, item in library.items_by_id.items():
        if cid in NEVER_JUNK or cid in protect:
            continue
        if not (library.wiki_by_id.get(cid) or {}).get("dropped_by"):
            continue
        market = market_value(market_items.get(str(cid)))
        if market is None:
            continue
        npc = best_npc_price(item) or 0
        out[cid] = {"npc": npc, "market": market, "worth": max(npc, market)}
    return out


def junk_ids(cands: dict[int, dict], limit: int) -> list[int]:
    return sorted(cid for cid, c in cands.items() if c["worth"] < limit)


def stop_counts(cands: dict[int, dict]) -> list[dict]:
    return [{"gp": gp, "count": len(junk_ids(cands, gp))} for gp in STOPS]
