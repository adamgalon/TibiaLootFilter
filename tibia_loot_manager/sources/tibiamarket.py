"""Market prices per game world from TibiaMarket (https://tibiamarket.top), a fansite that records the
in-game Market. Third-party estimates: they vary by world and can be out of date.

Only fetched when the user asks (one request per world, about 3 MB), then kept in the cache for offline use.
"""

import re

from ..storage import utc_now_iso
from .http import PoliteHttpClient, SourceError

API = "https://api.tibiamarket.top"
SITE = "https://tibiamarket.top"
SOURCE_ID = "tibiamarket"
# The fields the app uses; the API returns more.
KEEP = ("buy_offer", "sell_offer", "month_average_buy", "month_average_sell", "month_sold", "month_bought",
        "day_average_buy", "time")
_WORLD = re.compile(r"^[A-Z][a-z]+$")


def valid_world(name: str) -> bool:
    return bool(_WORLD.match(name or ""))


def fetch_worlds(http: PoliteHttpClient) -> list[dict]:
    """[{"name", "last_update"}] for every world TibiaMarket tracks, by name."""
    data = http.get_json(f"{API}/world_data")
    if not isinstance(data, list):
        raise SourceError("Unexpected world list from TibiaMarket")
    worlds = [{"name": w["name"], "last_update": w.get("last_update")} for w in data
              if isinstance(w, dict) and valid_world(w.get("name"))]
    return sorted(worlds, key=lambda w: w["name"])


def fetch_world(http: PoliteHttpClient, world: str) -> dict:
    """Every item's market values on one world: {"source": {...}, "items": {"<client id>": {...}}}."""
    if not valid_world(world):
        raise SourceError(f"Not a world name: {world!r}")
    data = http.get_json(f"{API}/market_values", {"server": world, "limit": 10000})
    if not isinstance(data, list):
        raise SourceError("Unexpected market data from TibiaMarket")
    items = {}
    for rec in data:
        if isinstance(rec, dict) and isinstance(rec.get("id"), int):
            items[str(rec["id"])] = {k: rec[k] for k in KEEP if isinstance(rec.get(k), (int, float))}
    if not items:
        raise SourceError(f"TibiaMarket has no market data for {world}")
    newest = max((r.get("time", 0) for r in items.values()), default=0)
    return {"source": {"id": SOURCE_ID, "url": SITE, "world": world, "fetched_at": utc_now_iso(),
                       "data_time": newest}, "items": items}
