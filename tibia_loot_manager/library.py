"""Domain model: catalog, Delivery Task candidates, and the personal Accepted Loot list.

Three layers are kept apart:

* the client catalog (authoritative client IDs and names),
* the source-managed Delivery Task candidate list (from TibiaWiki),
* the user's overrides (``UserState``).

Effective lists are always computed from these, never stored, so a source
update or a user edit can change one layer without losing the others.
"""

from dataclasses import dataclass

from .i18n import _
from .sources import tibiawiki
from .state import UserState
from .values import NPC_CHARGES, NPC_PAYS, ItemValue

# ID mapping outcomes for items that come from the wiki
VERIFIED = "verified"
NO_CLIENT_DATA = "no_client_data"
NO_WIKI_ID = "no_wiki_id"
ID_MISMATCH = "id_mismatch"
NOT_IN_CLIENT = "not_in_client"
AMBIGUOUS = "ambiguous"

ID_STATUS_TEXT = {
    VERIFIED: _("Verified"),
    NO_CLIENT_DATA: _("Unverified: no client data loaded"),
    NO_WIKI_ID: _("Unverified: wiki lists no client ID"),
    ID_MISMATCH: _("Unverified: wiki ID and client name disagree"),
    NOT_IN_CLIENT: _("Unverified: not found in installed client"),
    AMBIGUOUS: _("Unverified: several client items match"),
}

ORIGIN_DELIVERY_SOURCE = "delivery_source"
ORIGIN_DELIVERY_USER = "delivery_user"
ORIGIN_MANUAL = "manual"

ORIGIN_TEXT = {
    ORIGIN_DELIVERY_SOURCE: _("Delivery Task (source)"),
    ORIGIN_DELIVERY_USER: _("Delivery Task (added by you)"),
    ORIGIN_MANUAL: _("Added by you"),
}


def wiki_key(title: str) -> str:
    return f"wiki:{title}"


def client_key(client_id: int) -> str:
    return f"client:{client_id}"


@dataclass
class Entry:
    key: str
    name: str
    client_id: int | None
    id_status: str
    origin: str
    delivery: dict | None = None  # source Delivery Task record, if any

    @property
    def exportable(self) -> bool:
        return self.client_id is not None and self.id_status == VERIFIED


def resolve_client_id(title: str, wiki: dict | None, ids_by_name: dict[str, list[int]],
                      items_by_id: dict[int, dict]) -> tuple[int | None, str]:
    """Map a wiki item to a client ID only when two independent facts agree.

    The installed client must have an item with this name *and* the wiki's
    ``itemid`` field must list that same ID. Names alone are never trusted:
    the client has hundreds of duplicate names.
    """
    if not items_by_id:
        return None, NO_CLIENT_DATA
    names = {title.lower()}
    if wiki and wiki.get("actualname"):
        names.add(wiki["actualname"].lower())
    by_name = {cid for n in names for cid in ids_by_name.get(n, [])}
    wiki_ids = set(wiki.get("itemids", [])) if wiki else set()
    agreed = by_name & wiki_ids
    if len(agreed) == 1:
        return next(iter(agreed)), VERIFIED
    if len(agreed) > 1:
        return None, AMBIGUOUS
    if not wiki_ids:
        return None, NO_WIKI_ID
    if not by_name and not (wiki_ids & items_by_id.keys()):
        return None, NOT_IN_CLIENT
    return None, ID_MISMATCH


def match_wiki_pages(items_by_id: dict[int, dict], pages: dict[str, dict]) -> dict[int, dict]:
    """Attach a TibiaWiki page to a client item only when that page lists the item's ID
    (or one of its variants) and no other page competes for it, or exactly one competing
    page has the same in-game name."""
    claims: dict[int, list[dict]] = {}
    for record in pages.values():
        for cid in record.get("itemids", []):
            claims.setdefault(cid, []).append(record)
    matched: dict[int, dict] = {}
    for cid, item in items_by_id.items():
        candidates = {r["title"]: r for i in [cid, *item.get("variants", [])] for r in claims.get(i, [])}
        if len(candidates) > 1:
            name = item["name"].lower()
            candidates = {t: r for t, r in candidates.items() if (r.get("actualname") or t).lower() == name}
        if len(candidates) == 1:
            matched[cid] = next(iter(candidates.values()))
    return matched


class Library:
    def __init__(self, catalog: dict | None, delivery: dict | None, wiki_lookups: dict | None, state: UserState,
                 wiki_index: dict | None = None):
        self.state = state
        self.wiki_index = wiki_index or {"source": None, "pages": {}}
        self.set_catalog(catalog)
        self.set_delivery(delivery)
        self.wiki_lookups: dict[str, dict] = wiki_lookups or {}

    # --- data layers -----------------------------------------------------------

    def set_catalog(self, catalog: dict | None) -> None:
        self.catalog = catalog or {"source": None, "items": {}}
        self.items_by_id: dict[int, dict] = {int(k): v for k, v in self.catalog["items"].items()}
        self.ids_by_name: dict[str, list[int]] = {}
        for cid, item in self.items_by_id.items():
            self.ids_by_name.setdefault(item["name"].lower(), []).append(cid)
        self.wiki_by_id = match_wiki_pages(self.items_by_id, self.wiki_index["pages"])
        self._resolve_delivery()

    def set_wiki_index(self, index: dict | None) -> None:
        self.wiki_index = index or {"source": None, "pages": {}}
        self.wiki_by_id = match_wiki_pages(self.items_by_id, self.wiki_index["pages"])

    def set_delivery(self, delivery: dict | None) -> None:
        self.delivery = delivery or {"source": None, "items": {}}
        self._resolve_delivery()

    def _resolve_delivery(self) -> None:
        if not hasattr(self, "delivery") or not hasattr(self, "items_by_id"):
            return
        self.delivery_resolution: dict[str, tuple[int | None, str]] = {}
        self.delivery_title_by_id: dict[int, str] = {}
        for title, record in self.delivery["items"].items():
            cid, status = resolve_client_id(title, record.get("wiki"), self.ids_by_name, self.items_by_id)
            self.delivery_resolution[title] = (cid, status)
            if cid is not None:
                self.delivery_title_by_id.setdefault(cid, title)

    # --- lookups ---------------------------------------------------------------

    def item_name(self, client_id: int) -> str:
        item = self.items_by_id.get(client_id)
        return item["name"] if item else _("Unknown item #{id}").format(id=client_id)

    def delivery_title_for(self, client_id: int) -> str | None:
        return self.delivery_title_by_id.get(client_id)

    def shares_name(self, client_id: int) -> bool:
        item = self.items_by_id.get(client_id)
        return bool(item) and len(self.ids_by_name.get(item["name"].lower(), [])) > 1

    def wiki_title(self, client_id: int) -> str | None:
        record = self.wiki_by_id.get(client_id)
        return record["title"] if record else None

    def item_note(self, client_id: int) -> str:
        """Short hint that tells same-named items apart, plus any hidden variants."""
        item = self.items_by_id.get(client_id)
        if not item:
            return ""
        parts = []
        if self.shares_name(client_id):
            record = self.wiki_by_id.get(client_id)
            if record:
                kind = record.get("primarytype")
                redundant = kind and kind.lower().rstrip("s") in record["title"].lower()
                parts.append(record["title"] + (f" ({kind})" if kind and not redundant else ""))
            else:
                parts.append(_("Same name as another item; no wiki page confirmed"))
        if item.get("variants"):
            parts.append(_("variants: {ids}").format(ids=", ".join(map(str, item["variants"]))))
        return " · ".join(parts)

    def unresolved_delivery(self) -> list[Entry]:
        """Delivery items without a verified client ID (shown in the catalog, never exported)."""
        out = []
        for title, record in self.delivery["items"].items():
            cid, status = self.delivery_resolution[title]
            if cid is None:
                out.append(Entry(wiki_key(title), title, None, status, ORIGIN_DELIVERY_SOURCE, record))
        return out

    # --- effective lists -------------------------------------------------------

    def delivery_entries(self) -> tuple[list[Entry], list[Entry]]:
        """Return (active, excluded) Delivery Task candidates after the user's edits."""
        removed = set(self.state.delivery_removed)
        active, excluded, seen_ids = [], [], set()
        for title, record in self.delivery["items"].items():
            cid, status = self.delivery_resolution[title]
            name = self.items_by_id[cid]["name"] if cid is not None else title
            entry = Entry(wiki_key(title), name, cid, status, ORIGIN_DELIVERY_SOURCE, record)
            if entry.key in removed or (cid is not None and client_key(cid) in removed):
                excluded.append(entry)
            else:
                active.append(entry)
                if cid is not None:
                    seen_ids.add(cid)
        for cid in self.state.delivery_added:
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            status = VERIFIED if cid in self.items_by_id else NOT_IN_CLIENT
            active.append(Entry(client_key(cid), self.item_name(cid), cid, status, ORIGIN_DELIVERY_USER))
        return active, excluded

    def accepted_entries(self) -> tuple[list[Entry], list[Entry]]:
        """Return (active, excluded) personal Accepted Loot entries."""
        excluded_keys = set(self.state.accepted_excluded)
        candidates: list[Entry] = []
        if self.state.accepted_follow_delivery:
            candidates.extend(self.delivery_entries()[0])
        for cid in self.state.accepted_extra:
            status = VERIFIED if cid in self.items_by_id else NOT_IN_CLIENT
            candidates.append(Entry(client_key(cid), self.item_name(cid), cid, status, ORIGIN_MANUAL))
        active, excluded, seen = [], [], set()
        for entry in candidates:
            ident = entry.client_id if entry.client_id is not None else entry.key
            if ident in seen:
                continue
            seen.add(ident)
            is_excluded = entry.key in excluded_keys or (
                entry.client_id is not None and client_key(entry.client_id) in excluded_keys)
            (excluded if is_excluded else active).append(entry)
        return active, excluded

    def accepted_ids(self) -> set[int]:
        return {e.client_id for e in self.accepted_entries()[0] if e.exportable}

    def delivery_ids(self) -> set[int]:
        return {e.client_id for e in self.delivery_entries()[0] if e.client_id is not None}

    # --- edits -----------------------------------------------------------------

    def _keys_for(self, client_id: int) -> set[str]:
        keys = {client_key(client_id)}
        title = self.delivery_title_for(client_id)
        if title:
            keys.add(wiki_key(title))
        return keys

    def add_to_accepted(self, client_id: int) -> None:
        keys = self._keys_for(client_id)
        self.state.accepted_excluded = [k for k in self.state.accepted_excluded if k not in keys]
        if client_id not in self.accepted_ids() and client_id not in self.state.accepted_extra:
            self.state.accepted_extra.append(client_id)

    def restore_accepted_entry(self, entry: Entry) -> None:
        if entry.client_id is not None:
            self.add_to_accepted(entry.client_id)
        else:
            self.state.accepted_excluded = [k for k in self.state.accepted_excluded if k != entry.key]

    def remove_from_accepted(self, entry: Entry) -> None:
        if entry.client_id is not None and entry.client_id in self.state.accepted_extra:
            self.state.accepted_extra.remove(entry.client_id)
        still_there = any(e.key == entry.key or (entry.client_id is not None and e.client_id == entry.client_id)
                          for e in self.accepted_entries()[0])
        if still_there:
            keys = {entry.key} | (self._keys_for(entry.client_id) if entry.client_id is not None else set())
            for key in sorted(keys):
                if key not in self.state.accepted_excluded:
                    self.state.accepted_excluded.append(key)

    def add_to_delivery(self, client_id: int) -> None:
        keys = self._keys_for(client_id)
        self.state.delivery_removed = [k for k in self.state.delivery_removed if k not in keys]
        if client_id not in self.delivery_ids() and client_id not in self.state.delivery_added:
            self.state.delivery_added.append(client_id)

    def restore_delivery_entry(self, entry: Entry) -> None:
        self.state.delivery_removed = [k for k in self.state.delivery_removed if k != entry.key]
        if entry.client_id is not None:
            self.state.delivery_removed = [k for k in self.state.delivery_removed
                                           if k != client_key(entry.client_id)]

    def remove_from_delivery(self, entry: Entry) -> None:
        if entry.origin == ORIGIN_DELIVERY_USER:
            if entry.client_id in self.state.delivery_added:
                self.state.delivery_added.remove(entry.client_id)
        elif entry.key not in self.state.delivery_removed:
            self.state.delivery_removed.append(entry.key)

    def restore_delivery_defaults(self) -> None:
        self.state.delivery_added = []
        self.state.delivery_removed = []

    def restore_accepted_defaults(self) -> None:
        self.state.accepted_follow_delivery = True
        self.state.accepted_extra = []
        self.state.accepted_excluded = []

    # --- details ---------------------------------------------------------------

    def wiki_record_for(self, client_id: int | None, title: str | None = None) -> dict | None:
        title = title or (self.delivery_title_for(client_id) if client_id is not None else None)
        if title and title in self.delivery["items"]:
            wiki = self.delivery["items"][title].get("wiki")
            if wiki:
                return {**wiki, "fetched_at": (self.delivery.get("source") or {}).get("fetched_at")}
        if client_id is None:
            return None
        if str(client_id) in self.wiki_lookups:
            return self.wiki_lookups[str(client_id)]
        record = self.wiki_by_id.get(client_id)
        if record:
            return {**record, "fetched_at": (self.wiki_index.get("source") or {}).get("fetched_at")}
        return None

    def values_for(self, client_id: int | None, title: str | None = None) -> list[ItemValue]:
        values: list[ItemValue] = []
        item = self.items_by_id.get(client_id) if client_id is not None else None
        csrc = self.catalog.get("source") or {}
        if item:
            label = _("Tibia client {version}").format(version=csrc.get("client_version") or "?")
            for offer in item.get("npc_offers", []):
                detail = f"{offer['npc']}, {offer['location']}".strip(", ")
                for kind, amount in ((NPC_PAYS, offer.get("npc_buys_for")), (NPC_CHARGES, offer.get("npc_sells_for"))):
                    if amount:
                        values.append(ItemValue(kind, amount, offer.get("currency") or "gold", label, None,
                                                csrc.get("read_at"), detail=detail))
        wiki = self.wiki_record_for(client_id, title)
        if wiki:
            for kind, amount in ((NPC_PAYS, wiki.get("npc_buys_for")), (NPC_CHARGES, wiki.get("npc_sells_for"))):
                if amount:
                    values.append(ItemValue(kind, amount, "gold", _("TibiaWiki item page"), wiki.get("url"),
                                            wiki.get("fetched_at"), as_of=wiki.get("page_timestamp")))
        title = title or (self.delivery_title_for(client_id) if client_id is not None else None)
        record = self.delivery["items"].get(title) if title else None
        if record and record.get("npc_buy_price"):
            dsrc = self.delivery.get("source") or {}
            values.append(ItemValue(NPC_PAYS, record["npc_buy_price"], "gold", _("TibiaWiki Delivery Task page"),
                                    dsrc.get("url"), dsrc.get("fetched_at"), as_of=dsrc.get("revision_timestamp")))
        return values

    def lookup_title_hint(self, client_id: int) -> str:
        return self.delivery_title_for(client_id) or self.item_name(client_id)

    def store_wiki_lookup(self, client_id: int, record: dict | None, fetched_at: str) -> None:
        self.wiki_lookups[str(client_id)] = record or {"not_found": True, "fetched_at": fetched_at}


def delivery_url() -> str:
    return tibiawiki.page_url(tibiawiki.DELIVERY_PAGE)
