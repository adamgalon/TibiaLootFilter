"""Item value records.

Values are kept as facts with provenance (who pays whom, which source, which
world, how old) rather than as a single "price". Nothing in this version ranks
or selects loot by value; market sources can be plugged in later through
``MarketValueProvider``.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

NPC_PAYS = "npc_pays"  # an NPC buys the item from the player for this amount
NPC_CHARGES = "npc_charges"  # an NPC sells the item to the player for this amount
MARKET = "market"  # a market observation; always tied to a world

STALE_AFTER_DAYS = 30


@dataclass(frozen=True)
class ItemValue:
    kind: str
    amount: int
    currency: str
    source: str
    source_url: str | None
    retrieved_at: str | None  # when this app obtained the value
    as_of: str | None = None  # when the source says the value was current (e.g. wiki revision)
    world: str | None = None
    detail: str | None = None  # e.g. "Rashid, Darashia City"
    authority: str | None = None  # a kind from sources.registry (official client, community wiki, estimate)

    def age_days(self, now: datetime | None = None) -> int | None:
        if not self.retrieved_at:
            return None
        try:
            then = datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - then).days

    def is_stale(self, now: datetime | None = None) -> bool:
        age = self.age_days(now)
        return age is None or age > STALE_AFTER_DAYS


class MarketValueProvider(Protocol):
    """Extension point for a future market price source (per world).

    Values from fansites must use ``authority=THIRD_PARTY_ESTIMATE`` and a
    ``world``; no single provider is treated as authoritative.
    """

    name: str

    def values_for(self, client_id: int, world: str) -> list[ItemValue]: ...


# No market source is configured in this version (see SOURCES.md).
MARKET_PROVIDERS: list[MarketValueProvider] = []
