"""TibiaWiki (tibia.fandom.com) via its MediaWiki API.

Provides the Delivery Task candidate list and, per item, the infobox fields
``itemid`` (client IDs), ``droppedby``, ``npcvalue`` (price NPCs pay) and
``npcprice`` (price NPCs charge). Wiki content is community-maintained and
licensed CC BY-SA; every record keeps its page URL and revision timestamp.
"""

import re
import urllib.parse

from ..i18n import _
from ..storage import utc_now_iso
from .http import PoliteHttpClient, SourceError

SOURCE_ID = "tibiawiki_fandom"
INDEX_SOURCE_ID = "tibiawiki_fandom_items"
API_URL = "https://tibia.fandom.com/api.php"
WIKI_BASE = "https://tibia.fandom.com/wiki/"
DELIVERY_PAGE = "Delivery Task"
OBJECT_TEMPLATE = "Template:Infobox Object"
TITLES_PER_REQUEST = 50  # MediaWiki limit for anonymous clients when fetching page content
LIST_PER_REQUEST = 500


def page_url(title: str) -> str:
    return WIKI_BASE + urllib.parse.quote(title.replace(" ", "_"), safe="_()'!,:")


# --- parsing ------------------------------------------------------------------

_LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_HEADING = re.compile(r"^(=+)\s*(.*?)\s*\1\s*$")


def _int_or_none(text: str) -> int | None:
    digits = re.sub(r"[,\s.]", "", text or "")
    return int(digits) if digits.isdigit() else None


def parse_delivery_task(wikitext: str) -> list[dict]:
    """Parse the item tables on the Delivery Task page.

    Tables sit under ``== Category ==`` headings. Column positions are taken
    from each table's header row rather than assumed, so a reordered or
    missing price column does not shift values into the wrong field.
    """
    items: list[dict] = []
    category = None
    columns: list[str] = []
    for line in wikitext.splitlines():
        line = line.strip()
        heading = _HEADING.match(line)
        if heading and len(heading.group(1)) == 2:
            category = heading.group(2)
            columns = []
            continue
        if line.startswith("!"):
            columns = [c.strip().lower() for c in line.lstrip("!").split("!!")]
            continue
        if not line.startswith("|") or line.startswith(("|-", "|}", "|+")) or not columns or not category:
            continue
        cells = [c.strip() for c in line[1:].split("||")]
        record = {"task_category": category, "min_qty": None, "max_qty": None, "npc_buy_price": None, "title": None}
        for header, cell in zip(columns, cells):
            if header == "name":
                link = _LINK.search(cell)
                record["title"] = (link.group(1) if link else cell).strip()
            elif header.startswith("min"):
                record["min_qty"] = _int_or_none(cell)
            elif header.startswith("max"):
                record["max_qty"] = _int_or_none(cell)
            elif "price" in header or header.startswith("npc"):
                record["npc_buy_price"] = _int_or_none(cell)
        if record["title"]:
            items.append(record)
    return items


def parse_infobox(content: str) -> dict[str, str]:
    """Extract top-level ``| key = value`` pairs from the first infobox template."""
    start = content.find("{{Infobox")
    if start < 0:
        return {}
    depth, i, end = 0, start, len(content)
    while i < end:
        if content.startswith("{{", i):
            depth += 1
            i += 2
        elif content.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                end = i
                break
        else:
            i += 1
    body = content[start:end]
    fields: dict[str, str] = {}
    key = None
    depth = 0
    for line in body.splitlines()[1:]:
        if depth == 0:
            m = re.match(r"^\|\s*([A-Za-z_][\w ]*?)\s*=(.*)$", line)
            if m:
                key = m.group(1).strip().lower()
                fields[key] = m.group(2).strip()
                depth = max(0, line.count("{{") - line.count("}}"))
                continue
        if key:
            fields[key] += "\n" + line
        depth = max(0, depth + line.count("{{") - line.count("}}"))
    if fields:
        last = list(fields)[-1]
        fields[last] = re.sub(r"\}\}\s*$", "", fields[last]).strip()
    return fields


def parse_item_ids(value: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", value or "")]


def parse_dropped_by(value: str) -> list[str]:
    m = re.search(r"\{\{\s*Dropped By\s*((?:\|[^{}]*)?)\}\}", value or "", re.IGNORECASE)
    if not m:
        return []
    names = [n.strip() for n in m.group(1).split("|")]
    return [n for n in names if n and "=" not in n]


def item_record(title: str, content: str, timestamp: str | None, pageid: int | None = None) -> dict:
    """One wiki item page. ``itemids`` are Tibia (client) item IDs as listed by the wiki;
    ``pageid`` is the wiki's own page number and is never used as an item ID."""
    box = parse_infobox(content)
    return {
        "title": title,
        "pageid": pageid,
        "url": page_url(title),
        "page_timestamp": timestamp,
        "itemids": parse_item_ids(box.get("itemid", "")),
        "actualname": box.get("actualname") or None,
        "dropped_by": parse_dropped_by(box.get("droppedby", "")),
        "npc_buys_for": _int_or_none(box.get("npcvalue", "")) or None,
        "npc_sells_for": _int_or_none(box.get("npcprice", "")) or None,
        "primarytype": box.get("primarytype") or None,
        "task_item": box.get("task_item", "").lower() == "yes",
    }


# --- fetching -----------------------------------------------------------------

class TibiaWikiSource:
    def __init__(self, http: PoliteHttpClient):
        self.http = http

    def _query(self, **params) -> dict:
        params.update(action="query", format="json", formatversion="2")
        data = self.http.get_json(API_URL, params)
        if "error" in data:
            raise SourceError(f"TibiaWiki API error: {data['error'].get('info', data['error'])}")
        return data

    def fetch_pages(self, titles: list[str]) -> dict[str, dict]:
        """Return {requested_title: {"title", "content", "timestamp"} or {"missing": True}}."""
        results: dict[str, dict] = {}
        for i in range(0, len(titles), TITLES_PER_REQUEST):
            batch = titles[i:i + TITLES_PER_REQUEST]
            data = self._query(prop="revisions", rvprop="content|timestamp", rvslots="main",
                               redirects="1", titles="|".join(batch))
            query = data.get("query", {})
            alias = {t: t for t in batch}
            for key in ("normalized", "redirects"):
                for hop in query.get(key, []):
                    for original, current in list(alias.items()):
                        if current == hop["from"]:
                            alias[original] = hop["to"]
            pages = {p["title"]: p for p in query.get("pages", [])}
            for original, final in alias.items():
                page = pages.get(final)
                if not page or page.get("missing") or not page.get("revisions"):
                    results[original] = {"missing": True}
                    continue
                rev = page["revisions"][0]
                results[original] = {
                    "title": page["title"],
                    "pageid": page.get("pageid"),
                    "content": rev.get("slots", {}).get("main", {}).get("content", ""),
                    "timestamp": rev.get("timestamp"),
                }
        return results

    def fetch_delivery_snapshot(self) -> dict:
        """Fetch the Delivery Task candidate list plus each item's infobox."""
        page = self.fetch_pages([DELIVERY_PAGE]).get(DELIVERY_PAGE, {})
        if page.get("missing"):
            raise SourceError("The Delivery Task page was not found on TibiaWiki.")
        rows = parse_delivery_task(page["content"])
        if len(rows) < 20:
            raise SourceError(f"The Delivery Task page parsed to only {len(rows)} items; its layout may have changed.")
        item_pages = self.fetch_pages(sorted({r["title"] for r in rows}))
        items: dict[str, dict] = {}
        for row in rows:
            info = item_pages.get(row["title"], {"missing": True})
            wiki = (None if info.get("missing") else
                    item_record(info["title"], info["content"], info["timestamp"], info.get("pageid")))
            key = row["title"]
            if key in items:  # same item listed twice: keep the first, note the extra category
                items[key].setdefault("also_in", []).append(row["task_category"])
                continue
            items[key] = {**row, "url": page_url(row["title"]), "wiki": wiki}
        return {
            "source": {
                "id": SOURCE_ID,
                "page": DELIVERY_PAGE,
                "url": page_url(DELIVERY_PAGE),
                "revision_timestamp": page.get("timestamp"),
                "fetched_at": utc_now_iso(),
            },
            "items": items,
        }

    def list_object_pages(self) -> dict[int, tuple[str, int]]:
        """Return {pageid: (title, lastrevid)} for every article using the item infobox."""
        pages: dict[int, tuple[str, int]] = {}
        cont: dict = {}
        while True:
            data = self._query(generator="embeddedin", geititle=OBJECT_TEMPLATE, geinamespace="0",
                               geilimit=str(LIST_PER_REQUEST), prop="info", **cont)
            for p in data.get("query", {}).get("pages", []):
                pages[p["pageid"]] = (p["title"], p.get("lastrevid", 0))
            if "continue" not in data:
                return pages
            cont = {k: v for k, v in data["continue"].items()}

    def fetch_object_index(self, previous: dict | None = None, progress=None) -> dict:
        """Map every wiki item page to its client IDs, downloading only pages that changed.

        ``previous`` is an earlier result of this method; unchanged pages
        (same revision ID) are reused from it.
        """
        report = progress or (lambda _msg: None)
        report(_("Listing TibiaWiki item pages…"))
        listing = self.list_object_pages()
        old = (previous or {}).get("pages", {})
        old_by_pageid = {r["pageid"]: r for r in old.values() if "pageid" in r}
        old_skipped = {int(k): v for k, v in (previous or {}).get("skipped", {}).items()}
        pages: dict[str, dict] = {}
        skipped: dict[int, int] = {}  # pages without item IDs, remembered so they are not re-downloaded
        stale: list[int] = []
        for pageid, (title, revid) in listing.items():
            cached = old_by_pageid.get(pageid)
            if cached and cached.get("revid") == revid:
                pages[cached["title"]] = cached
            elif old_skipped.get(pageid) == revid:
                skipped[pageid] = revid
            else:
                stale.append(pageid)
        for i in range(0, len(stale), TITLES_PER_REQUEST):
            report(_("Downloading changed TibiaWiki item pages ({done}/{total})…").format(done=i, total=len(stale)))
            batch = stale[i:i + TITLES_PER_REQUEST]
            data = self._query(prop="revisions", rvprop="content|timestamp|ids", rvslots="main",
                               pageids="|".join(map(str, batch)))
            for p in data.get("query", {}).get("pages", []):
                if p.get("missing") or not p.get("revisions"):
                    continue
                rev = p["revisions"][0]
                record = item_record(p["title"], rev.get("slots", {}).get("main", {}).get("content", ""),
                                     rev.get("timestamp"), p["pageid"])
                if not record["itemids"]:
                    skipped[p["pageid"]] = rev.get("revid")
                    continue
                record["revid"] = rev.get("revid")
                pages[p["title"]] = record
        return {
            "source": {"id": INDEX_SOURCE_ID, "url": page_url(OBJECT_TEMPLATE), "fetched_at": utc_now_iso(),
                       "pages_listed": len(listing), "pages_downloaded": len(stale)},
            "pages": dict(sorted(pages.items())),
            "skipped": {str(k): v for k, v in sorted(skipped.items())},
        }

    def lookup_item(self, client_name: str, client_id: int) -> dict | None:
        """Find the wiki page for a client item and confirm it by client ID.

        Returns None if no page lists ``client_id`` in its ``itemid`` field, so
        drop data is never attached to the wrong item.
        """
        data = self._query(list="search", srsearch=client_name, srnamespace="0", srlimit="5")
        titles = [hit["title"] for hit in data.get("query", {}).get("search", [])]
        if not titles:
            return None
        for title, page in self.fetch_pages(titles).items():
            if page.get("missing"):
                continue
            record = item_record(page["title"], page["content"], page["timestamp"], page.get("pageid"))
            if client_id in record["itemids"]:
                record["fetched_at"] = utc_now_iso()
                return record
        return None
