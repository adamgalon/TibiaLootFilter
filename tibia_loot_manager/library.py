"""Domain model: catalog, Delivery Task candidates, and the personal Accepted Loot list.

Three layers are kept apart:

* the client catalog (authoritative client IDs and names),
* the source-managed Delivery Task candidate list (from TibiaWiki),
* the user's overrides (``UserState``).

Effective lists are always computed from these, never stored, so a source
update or a user edit can change one layer without losing the others.
"""

import re
from dataclasses import dataclass

from .i18n import _
from .sources import tibiawiki
from .sources.registry import COMMUNITY_WIKI, OFFICIAL_CLIENT
from .state import UserState
from .values import NPC_CHARGES, NPC_PAYS, ItemValue

# --- Tibia item ID mapping -------------------------------------------------------------
#
# The "Tibia item ID" is the client type ID that the official client writes to
# lootBlackWhitelist.json (e.g. gold coin = 3031). It is never confused with a
# wiki page ID or an Open Tibia/TFS server ID; this app does not use those.
#
# Every mapping has one of three states, with a reason code for the details.

VERIFIED, UNVERIFIED, CONFLICTING = "verified", "unverified", "conflicting"

# reason codes → state
OK_CLIENT_AND_WIKI = "ok_client_and_wiki"  # installed client and TibiaWiki agree
OK_CLIENT_ONLY = "ok_client_only"  # from the installed client; no wiki page claims this ID
NO_CLIENT_DATA = "no_client_data"
CLIENT_DATA_SUSPECT = "client_data_suspect"  # reference items did not match: parsing may be wrong
NO_WIKI_ID = "no_wiki_id"
NOT_IN_CLIENT = "not_in_client"
ID_MISMATCH = "id_mismatch"
AMBIGUOUS = "ambiguous"
WIKI_NAME_CONFLICT = "wiki_name_conflict"

MAPPING_STATE = {
    OK_CLIENT_AND_WIKI: VERIFIED, OK_CLIENT_ONLY: VERIFIED,
    NO_CLIENT_DATA: UNVERIFIED, CLIENT_DATA_SUSPECT: UNVERIFIED, NO_WIKI_ID: UNVERIFIED, NOT_IN_CLIENT: UNVERIFIED,
    ID_MISMATCH: CONFLICTING, AMBIGUOUS: CONFLICTING, WIKI_NAME_CONFLICT: CONFLICTING,
}

STATE_TEXT = {VERIFIED: _("Verified"), UNVERIFIED: _("Unverified"), CONFLICTING: _("Conflicting")}

ID_STATUS_TEXT = {
    OK_CLIENT_AND_WIKI: _("Verified"),
    OK_CLIENT_ONLY: _("Verified (client only)"),
    NO_CLIENT_DATA: _("Unverified: no client data loaded"),
    CLIENT_DATA_SUSPECT: _("Unverified: client data failed the reference check"),
    NO_WIKI_ID: _("Unverified: wiki lists no client ID"),
    NOT_IN_CLIENT: _("Unverified: not found in installed client"),
    ID_MISMATCH: _("Conflicting: wiki ID and client name disagree"),
    AMBIGUOUS: _("Conflicting: several client items match"),
    WIKI_NAME_CONFLICT: _("Conflicting: TibiaWiki names this ID differently"),
}

ID_STATUS_DETAIL = {
    OK_CLIENT_AND_WIKI: _("The installed Tibia client and TibiaWiki both list this ID for this item."),
    OK_CLIENT_ONLY: _("The ID and name come from the installed Tibia client. No TibiaWiki page lists this ID."),
    NO_CLIENT_DATA: _("No installed client data is loaded, so the ID cannot be checked."),
    CLIENT_DATA_SUSPECT: _("Known reference items (e.g. gold coin = 3031) did not match the installed client data, "
                           "so no ID is trusted until this is resolved."),
    NO_WIKI_ID: _("TibiaWiki gives no client ID for this item."),
    NOT_IN_CLIENT: _("This ID or name was not found in the installed Tibia client."),
    ID_MISMATCH: _("TibiaWiki's ID points to a client item with a different name."),
    AMBIGUOUS: _("More than one client item matches the wiki's name and IDs."),
    WIKI_NAME_CONFLICT: _("A TibiaWiki page lists this ID under a different item name."),
}

# Known IDs used to check that the installed client data was read correctly.
REFERENCE_ITEMS = {3031: "gold coin", 3035: "platinum coin"}


def name_key(name: str) -> str:
    """Normalise a name for comparison: case, apostrophes/punctuation and spacing are ignored."""
    joined = re.sub(r"\s+(?=['’])", "", name.lower())  # "brainstealer 's" → "brainstealer's"
    return " ".join(re.sub(r"[^\w\s]", "", joined).split())


def page_names(record: dict) -> set[str]:
    """Names a wiki page may use for its item: title, title without a "(…)" qualifier, actualname."""
    title = record["title"]
    names = {name_key(title), name_key(re.sub(r"\s*\([^)]*\)\s*$", "", title))}
    if record.get("actualname"):
        names.add(name_key(record["actualname"]))
    return names

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
    def mapping_state(self) -> str:
        return MAPPING_STATE.get(self.id_status, UNVERIFIED)

    @property
    def exportable(self) -> bool:
        """Only verified Tibia item IDs may be exported or installed."""
        return self.client_id is not None and self.mapping_state == VERIFIED


def resolve_client_id(title: str, wiki: dict | None, ids_by_name: dict[str, list[int]],
                      items_by_id: dict[int, dict]) -> tuple[int | None, str]:
    """Map a wiki item to a client ID only when two independent facts agree.

    The installed client must have an item with this name *and* the wiki's
    ``itemid`` field must list that same ID. Names alone are never trusted:
    the client has hundreds of duplicate names.
    """
    if not items_by_id:
        return None, NO_CLIENT_DATA
    names = page_names({"title": title, "actualname": (wiki or {}).get("actualname")})
    by_name = {cid for n in names for cid in ids_by_name.get(n, [])}
    wiki_ids = set(wiki.get("itemids", [])) if wiki else set()
    agreed = by_name & wiki_ids
    if len(agreed) == 1:
        return next(iter(agreed)), OK_CLIENT_AND_WIKI
    if len(agreed) > 1:
        return None, AMBIGUOUS
    if not wiki_ids:
        return None, NO_WIKI_ID
    if not by_name and not (wiki_ids & items_by_id.keys()):
        return None, NOT_IN_CLIENT
    return None, ID_MISMATCH


def wiki_claims(pages: dict[str, dict]) -> dict[int, list[dict]]:
    claims: dict[int, list[dict]] = {}
    for record in pages.values():
        for cid in record.get("itemids", []):
            claims.setdefault(cid, []).append(record)
    return claims


def match_wiki_pages(items_by_id: dict[int, dict], pages: dict[str, dict]) -> dict[int, dict]:
    """Attach a TibiaWiki page to a client item only when that page lists the item's ID
    (or one of its variants) and no other page competes for it, or exactly one competing
    page has the same in-game name."""
    claims = wiki_claims(pages)
    matched: dict[int, dict] = {}
    for cid, item in items_by_id.items():
        candidates = {r["title"]: r for i in [cid, *item.get("variants", [])] for r in claims.get(i, [])}
        if len(candidates) > 1:
            name = name_key(item["name"])
            candidates = {t: r for t, r in candidates.items() if name in page_names(r)}
        if len(candidates) == 1:
            matched[cid] = next(iter(candidates.values()))
    return matched


def client_id_statuses(items_by_id: dict[int, dict], pages: dict[str, dict]) -> dict[int, tuple[str, list[str]]]:
    """For each client item: (reason code, titles of wiki pages that name its ID differently)."""
    claims = wiki_claims(pages)
    out: dict[int, tuple[str, list[str]]] = {}
    for cid, item in items_by_id.items():
        claimants = {r["title"]: r for i in [cid, *item.get("variants", [])] for r in claims.get(i, [])}
        if not claimants:
            out[cid] = (OK_CLIENT_ONLY, [])
        elif any(name_key(item["name"]) in page_names(r) for r in claimants.values()):
            out[cid] = (OK_CLIENT_AND_WIKI, [])
        else:
            out[cid] = (WIKI_NAME_CONFLICT, sorted(claimants))
    return out


def reference_problems(items_by_id: dict[int, dict]) -> list[str]:
    """Check known items; any mismatch means the client data was not read correctly."""
    if not items_by_id:
        return []
    problems = []
    for cid, expected in REFERENCE_ITEMS.items():
        actual = items_by_id.get(cid, {}).get("name")
        if actual is None or name_key(actual) != name_key(expected):
            problems.append(_("ID {id} should be “{expected}” but is {actual}.").format(
                id=cid, expected=expected, actual=f"“{actual}”" if actual else _("missing")))
    return problems


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
        self.ids_by_name: dict[str, list[int]] = {}  # keyed by name_key()
        for cid, item in self.items_by_id.items():
            self.ids_by_name.setdefault(name_key(item["name"]), []).append(cid)
        self.reference_issues = reference_problems(self.items_by_id)
        self._match_wiki()
        self._resolve_delivery()

    def set_wiki_index(self, index: dict | None) -> None:
        self.wiki_index = index or {"source": None, "pages": {}}
        self._match_wiki()

    def _match_wiki(self) -> None:
        pages = self.wiki_index["pages"]
        self.wiki_by_id = match_wiki_pages(self.items_by_id, pages)
        self.id_statuses = client_id_statuses(self.items_by_id, pages)

    def client_id_status(self, client_id: int) -> str:
        """Reason code for a Tibia item ID taken from the installed client."""
        if client_id not in self.items_by_id:
            return NO_CLIENT_DATA if not self.items_by_id else NOT_IN_CLIENT
        if self.reference_issues:
            return CLIENT_DATA_SUSPECT
        return self.id_statuses.get(client_id, (OK_CLIENT_ONLY, []))[0]

    def conflicting_pages(self, client_id: int) -> list[str]:
        return self.id_statuses.get(client_id, ("", []))[1]

    def _status_for(self, client_id: int | None, resolution: str) -> str:
        """Combine a wiki→client resolution with the per-ID check; the stricter one wins."""
        if client_id is None or MAPPING_STATE.get(resolution) != VERIFIED:
            return resolution
        return self.client_id_status(client_id)

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
        return bool(item) and len(self.ids_by_name.get(name_key(item["name"]), [])) > 1

    def same_name_ids(self, client_id: int) -> list[int]:
        item = self.items_by_id.get(client_id)
        return [i for i in self.ids_by_name.get(name_key(item["name"]), []) if i != client_id] if item else []

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
            entry = Entry(wiki_key(title), name, cid, self._status_for(cid, status), ORIGIN_DELIVERY_SOURCE, record)
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
            status = self.client_id_status(cid)
            active.append(Entry(client_key(cid), self.item_name(cid), cid, status, ORIGIN_DELIVERY_USER))
        return active, excluded

    def accepted_entries(self) -> tuple[list[Entry], list[Entry]]:
        """Return (active, excluded) personal Accepted Loot entries."""
        excluded_keys = set(self.state.accepted_excluded)
        candidates: list[Entry] = []
        if self.state.accepted_follow_delivery:
            candidates.extend(self.delivery_entries()[0])
        for cid in self.state.accepted_extra:
            status = self.client_id_status(cid)
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

    def set_accepted_many(self, keys: list[str], add: bool) -> list[str]:
        """Add or remove many rows at once (catalog keys: "3031" or "wiki:Title").

        Same result as toggling each key, but the list is rebuilt a fixed number of times instead of
        once per key. Returns the keys whose membership actually changed.
        """
        active = self.accepted_entries()[0]
        ids = {e.client_id for e in active if e.client_id is not None}
        present = {e.key for e in active}

        def member(key):
            return key in present or (key.isdigit() and int(key) in ids)

        wanted = [k for k in dict.fromkeys(keys) if member(k) != add]
        if not wanted:
            return []
        if add:
            unexclude = set()
            for key in wanted:
                unexclude |= self._keys_for(int(key)) if key.isdigit() else {key}
            self.state.accepted_excluded = [k for k in self.state.accepted_excluded if k not in unexclude]
            ids = {e.client_id for e in self.accepted_entries()[0] if e.client_id is not None}
            for key in wanted:
                if key.isdigit() and int(key) not in ids and int(key) not in self.state.accepted_extra:
                    self.state.accepted_extra.append(int(key))
        else:
            drop = {int(k) for k in wanted if k.isdigit()}
            self.state.accepted_extra = [c for c in self.state.accepted_extra if c not in drop]
            still = self.accepted_entries()[0]
            targets = set(wanted)
            excluded = set(self.state.accepted_excluded)
            for entry in still:
                if entry.key in targets or (entry.client_id is not None and str(entry.client_id) in targets):
                    keys_now = {entry.key} | (self._keys_for(entry.client_id) if entry.client_id is not None else set())
                    for k in sorted(keys_now - excluded):
                        self.state.accepted_excluded.append(k)
                        excluded.add(k)
        active = self.accepted_entries()[0]
        ids = {e.client_id for e in active if e.client_id is not None}
        present = {e.key for e in active}
        return [k for k in wanted if member(k) == add]

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
                                                csrc.get("read_at"), detail=detail, authority=OFFICIAL_CLIENT))
        wiki = self.wiki_record_for(client_id, title)
        if wiki:
            for kind, amount in ((NPC_PAYS, wiki.get("npc_buys_for")), (NPC_CHARGES, wiki.get("npc_sells_for"))):
                if amount:
                    values.append(ItemValue(kind, amount, "gold", _("TibiaWiki item page"), wiki.get("url"),
                                            wiki.get("fetched_at"), as_of=wiki.get("page_timestamp"),
                                            authority=COMMUNITY_WIKI))
        title = title or (self.delivery_title_for(client_id) if client_id is not None else None)
        record = self.delivery["items"].get(title) if title else None
        if record and record.get("npc_buy_price"):
            dsrc = self.delivery.get("source") or {}
            values.append(ItemValue(NPC_PAYS, record["npc_buy_price"], "gold", _("TibiaWiki Delivery Task page"),
                                    dsrc.get("url"), dsrc.get("fetched_at"), as_of=dsrc.get("revision_timestamp"),
                                    authority=COMMUNITY_WIKI))
        return values

    def lookup_title_hint(self, client_id: int) -> str:
        return self.delivery_title_for(client_id) or self.item_name(client_id)

    def store_wiki_lookup(self, client_id: int, record: dict | None, fetched_at: str) -> None:
        self.wiki_lookups[str(client_id)] = record or {"not_found": True, "fetched_at": fetched_at}


def delivery_url() -> str:
    return tibiawiki.page_url(tibiawiki.DELIVERY_PAGE)
