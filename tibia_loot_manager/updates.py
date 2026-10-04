"""User-triggered data refresh: fetch, diff for review, then apply.

Nothing here runs on a timer. ``check_for_updates`` only fetches and compares;
``apply_update`` is called after the user has reviewed the changes. A failed
source never replaces its cache.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .datastore import DataStore
from .i18n import _
from .library import Library, client_key, wiki_key
from .sources import tibia_client
from .sources.http import PoliteHttpClient, SourceError
from .sources.tibiawiki import INDEX_SOURCE_ID
from .sources.tibiawiki import SOURCE_ID as WIKI_SOURCE_ID
from .sources.tibiawiki import TibiaWikiSource

NEW, CHANGED, REMOVED = "new", "changed", "removed"


@dataclass
class Change:
    kind: str
    area: str  # "delivery", "catalog" or "wiki_index"
    key: str
    name: str
    details: str = ""
    user_note: str = ""


@dataclass
class SourceResult:
    source_id: str
    label: str
    ok: bool
    error: str | None = None
    snapshot: dict | None = None


@dataclass
class UpdateReview:
    results: list[SourceResult] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)

    def result(self, source_id: str) -> SourceResult | None:
        return next((r for r in self.results if r.source_id == source_id), None)

    @property
    def any_success(self) -> bool:
        return any(r.ok for r in self.results)


def _fmt(value) -> str:
    if value is None or value == []:
        return "—"
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return str(value)


_DELIVERY_FIELDS = [
    ("task_category", _("category")),
    ("min_qty", _("min")),
    ("max_qty", _("max")),
    ("npc_buy_price", _("NPC buy price")),
]
_WIKI_FIELDS = [
    ("itemids", _("client IDs")),
    ("npc_buys_for", _("NPC pays")),
    ("npc_sells_for", _("NPC charges")),
    ("dropped_by", _("dropped by")),
]


def diff_delivery(old: dict, new: dict, library: Library) -> list[Change]:
    old_items, new_items = old.get("items", {}), new.get("items", {})
    removed_by_user = set(library.state.delivery_removed)
    accepted_keys = {e.key for e in library.accepted_entries()[0]}
    changes: list[Change] = []
    for title in sorted(new_items.keys() - old_items.keys()):
        note = ""
        wiki = new_items[title].get("wiki") or {}
        added = [cid for cid in library.state.delivery_added if cid in wiki.get("itemids", [])]
        if added:
            note = _("You already added this item yourself; it will now also come from the source.")
        elif library.state.accepted_follow_delivery:
            note = _("Will be added to your Accepted Loot list (it follows the Delivery Task list).")
        changes.append(Change(NEW, "delivery", title, title, _("{cat}, {lo}–{hi}").format(
            cat=new_items[title].get("task_category"), lo=_fmt(new_items[title].get("min_qty")),
            hi=_fmt(new_items[title].get("max_qty"))), note))
    for title in sorted(old_items.keys() - new_items.keys()):
        key = wiki_key(title)
        if key in removed_by_user:
            note = _("You had excluded this item; your exclusion is kept.")
        elif key in accepted_keys:
            note = _("Currently on your Accepted Loot list via the Delivery Task list.")
        else:
            note = ""
        changes.append(Change(REMOVED, "delivery", title, title, "", note))
    for title in sorted(old_items.keys() & new_items.keys()):
        a, b = old_items[title], new_items[title]
        diffs = [f"{label}: {_fmt(a.get(k))} → {_fmt(b.get(k))}" for k, label in _DELIVERY_FIELDS if a.get(k) != b.get(k)]
        wa, wb = a.get("wiki") or {}, b.get("wiki") or {}
        diffs += [f"{label}: {_fmt(wa.get(k))} → {_fmt(wb.get(k))}" for k, label in _WIKI_FIELDS if wa.get(k) != wb.get(k)]
        if diffs:
            note = _("You excluded this item; your exclusion is kept.") if wiki_key(title) in removed_by_user else ""
            changes.append(Change(CHANGED, "delivery", title, title, "; ".join(diffs), note))
    return changes


def diff_catalog(old: dict, new: dict, library: Library) -> list[Change]:
    old_items, new_items = old.get("items", {}), new.get("items", {})
    mine = set(library.state.accepted_extra) | set(library.state.delivery_added)
    changes: list[Change] = []
    for key in sorted(new_items.keys() - old_items.keys(), key=int):
        changes.append(Change(NEW, "catalog", key, new_items[key]["name"], _("ID {id}, {cat}").format(
            id=key, cat=new_items[key]["category"])))
    for key in sorted(old_items.keys() - new_items.keys(), key=int):
        note = _("On your own list. It stays there but is marked unverified.") if int(key) in mine else ""
        changes.append(Change(REMOVED, "catalog", key, old_items[key]["name"], _("ID {id}").format(id=key), note))
    for key in sorted(old_items.keys() & new_items.keys(), key=int):
        a, b = old_items[key], new_items[key]
        diffs = []
        if a["name"] != b["name"]:
            diffs.append(_("name: {a} → {b}").format(a=a["name"], b=b["name"]))
        if a["category"] != b["category"]:
            diffs.append(_("category: {a} → {b}").format(a=a["category"], b=b["category"]))
        if a.get("npc_offers") != b.get("npc_offers"):
            diffs.append(_("NPC trade offers changed"))
        if a.get("variants") != b.get("variants"):
            diffs.append(_("variants: {a} → {b}").format(a=_fmt(a.get("variants")), b=_fmt(b.get("variants"))))
        if diffs:
            changes.append(Change(CHANGED, "catalog", key, b["name"], "; ".join(diffs)))
    return changes


def diff_wiki_index(old: dict, new: dict) -> list[Change]:
    old_pages, new_pages = old.get("pages", {}), new.get("pages", {})
    changes = [Change(NEW, "wiki_index", t, t, _("client IDs: {ids}").format(ids=_fmt(new_pages[t]["itemids"])))
               for t in sorted(new_pages.keys() - old_pages.keys())]
    changes += [Change(REMOVED, "wiki_index", t, t) for t in sorted(old_pages.keys() - new_pages.keys())]
    for t in sorted(old_pages.keys() & new_pages.keys()):
        a, b = old_pages[t], new_pages[t]
        if a.get("revid") == b.get("revid"):
            continue
        diffs = [f"{label}: {_fmt(a.get(k))} → {_fmt(b.get(k))}" for k, label in _WIKI_FIELDS if a.get(k) != b.get(k)]
        changes.append(Change(CHANGED, "wiki_index", t, t, "; ".join(diffs) or _("page edited (no relevant fields "
                                                                                 "changed)")))
    return changes


def check_for_updates(library: Library, http: PoliteHttpClient | None = None, progress=None) -> UpdateReview:
    """Fetch every configured source. Safe to run in a worker thread (reads only)."""
    report = progress or (lambda _msg: None)
    review = UpdateReview()
    package_dir = paths.client_package_dir(Path(library.state.characterdata_dir or paths.default_characterdata_dir()))

    report(_("Reading the installed Tibia client…"))
    try:
        catalog = tibia_client.read_catalog(package_dir)
        review.results.append(SourceResult(tibia_client.SOURCE_ID, _("Installed Tibia client"), True, snapshot=catalog))
        review.changes += diff_catalog(library.catalog, catalog, library)
    except tibia_client.ClientDataError as e:
        review.results.append(SourceResult(tibia_client.SOURCE_ID, _("Installed Tibia client"), False, str(e)))

    wiki = TibiaWikiSource(http or PoliteHttpClient())
    report(_("Downloading the Delivery Task list from TibiaWiki…"))
    try:
        delivery = wiki.fetch_delivery_snapshot()
        review.results.append(SourceResult(WIKI_SOURCE_ID, _("TibiaWiki (Fandom)"), True, snapshot=delivery))
        review.changes += diff_delivery(library.delivery, delivery, library)
    except SourceError as e:
        review.results.append(SourceResult(WIKI_SOURCE_ID, _("TibiaWiki (Fandom)"), False, str(e)))
    except Exception as e:  # a parser bug must not take down the app or the cache
        review.results.append(SourceResult(WIKI_SOURCE_ID, _("TibiaWiki (Fandom)"), False,
                                           _("Could not parse the data: {error}").format(error=e)))

    label = _("TibiaWiki item pages")
    try:
        index = wiki.fetch_object_index(library.wiki_index, progress=report)
        review.results.append(SourceResult(INDEX_SOURCE_ID, label, True, snapshot=index))
        review.changes += diff_wiki_index(library.wiki_index, index)
    except SourceError as e:
        review.results.append(SourceResult(INDEX_SOURCE_ID, label, False, str(e)))
    except Exception as e:
        review.results.append(SourceResult(INDEX_SOURCE_ID, label, False,
                                           _("Could not parse the data: {error}").format(error=e)))
    return review


def apply_update(review: UpdateReview, library: Library, store: DataStore, keep_removed: bool = False) -> None:
    """Commit reviewed snapshots. User overrides are never touched, except that
    ``keep_removed`` turns delivery items dropped by the source into the user's own additions."""
    catalog = review.result(tibia_client.SOURCE_ID)
    delivery = review.result(WIKI_SOURCE_ID)

    if keep_removed and delivery and delivery.ok:
        active_keys = {e.key: e for e in library.delivery_entries()[0]}
        for change in review.changes:
            if change.area == "delivery" and change.kind == REMOVED:
                entry = active_keys.get(wiki_key(change.key))
                if entry and entry.client_id is not None and entry.client_id not in library.state.delivery_added:
                    library.state.delivery_added.append(entry.client_id)
                    # carry over any Accepted Loot exclusion that was stored under the wiki key
                    if wiki_key(change.key) in library.state.accepted_excluded:
                        library.state.accepted_excluded.append(client_key(entry.client_id))

    for result in review.results:
        detail = None
        if result.ok and result.source_id == tibia_client.SOURCE_ID:
            detail = {"client_version": result.snapshot["source"].get("client_version")}
        elif result.ok and result.source_id == WIKI_SOURCE_ID:
            detail = {"revision_timestamp": result.snapshot["source"].get("revision_timestamp"),
                      "url": result.snapshot["source"].get("url")}
        elif result.ok and result.source_id == INDEX_SOURCE_ID:
            detail = {"pages": len(result.snapshot["pages"])}
        store.record_source(result.source_id, result.ok, result.error, detail)

    index = review.result(INDEX_SOURCE_ID)
    if index and index.ok:
        store.save_wiki_index(index.snapshot)
        library.wiki_index = index.snapshot  # matched below by set_catalog / set_wiki_index
    if catalog and catalog.ok:
        store.save_catalog(catalog.snapshot)
        library.set_catalog(catalog.snapshot)
    elif index and index.ok:
        library.set_wiki_index(index.snapshot)
    if delivery and delivery.ok:
        store.save_delivery(delivery.snapshot)
        library.set_delivery(delivery.snapshot)
    store.save_state(library.state)


def record_failures(review: UpdateReview, store: DataStore) -> None:
    """Log failed attempts when the user cancels the review (successes are not applied)."""
    for result in review.results:
        if not result.ok:
            store.record_source(result.source_id, False, result.error)
