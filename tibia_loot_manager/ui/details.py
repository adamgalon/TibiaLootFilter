"""Item detail panel: identifiers, Delivery Task metadata, values with provenance, and drops."""

from tkinter import ttk

from ..i18n import _
from ..library import ID_STATUS_TEXT, VERIFIED
from ..sources.http import PoliteHttpClient
from ..sources.tibiawiki import TibiaWikiSource
from ..storage import utc_now_iso
from ..values import NPC_CHARGES, NPC_PAYS, STALE_AFTER_DAYS
from .widgets import PAD, InfoText, fmt_date, fmt_gold, run_in_background


class ItemDetails(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.text = InfoText(self, width=46, height=20)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=ys.set)
        ys.grid(row=0, column=1, sticky="ns")
        self.lookup_button = ttk.Button(self, text=_("Look up drops on TibiaWiki"), command=self._lookup)
        self.lookup_button.grid(row=1, column=0, sticky="w", pady=(PAD, 0))
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.client_id = None
        self.title = None
        self.show(None)

    def show(self, client_id: int | None, title: str | None = None, id_status: str | None = None) -> None:
        self.client_id, self.title = client_id, title
        lib = self.app.library
        t = self.text
        t.clear()
        if client_id is None and title is None:
            t.add(_("Select an item to see its details."), "muted")
            t.done()
            self.lookup_button.state(["disabled"])
            return

        item = lib.items_by_id.get(client_id) if client_id is not None else None
        t.add((item["name"] if item else title) + "\n", "h1")
        if client_id is not None:
            t.add(_("Client ID: {id}").format(id=client_id) + "  ")
        status = id_status or (VERIFIED if item else None)
        if status:
            t.add(ID_STATUS_TEXT.get(status, status) + "\n", "ok" if status == VERIFIED else "warn")
        if item:
            t.add(_("Market category: {cat}").format(cat=item["category"]) + "\n")
            if item.get("stackable"):
                t.add(_("Stackable") + "\n", "muted")
            wiki_title = lib.wiki_title(client_id)
            if wiki_title:
                record = lib.wiki_by_id[client_id]
                t.add(_("TibiaWiki name: "))
                t.link(wiki_title, record["url"])
                if record.get("primarytype"):
                    t.add(f" ({record['primarytype']})", "muted")
                t.add("\n")
            if lib.shares_name(client_id):
                others = [i for i in lib.ids_by_name[item["name"].lower()] if i != client_id]
                t.add(_("Other items with the same in-game name: {ids}").format(
                    ids=", ".join(f"{i}" + (f" ({lib.wiki_title(i)})" if lib.wiki_title(i) else "") for i in others))
                      + "\n", "warn")
            if item.get("variants"):
                t.add(_("Variants traded on the Market as this item: {ids}").format(
                    ids=", ".join(map(str, item["variants"]))) + "\n", "muted")

        # Delivery Task
        t.add(_("Delivery Task") + "\n", "h2")
        dtitle = title or (lib.delivery_title_for(client_id) if client_id is not None else None)
        record = lib.delivery["items"].get(dtitle) if dtitle else None
        in_list = client_id in lib.delivery_ids() if client_id is not None else False
        if record:
            t.add(_("Source candidate · {cat} · requests {lo}–{hi}").format(
                cat=record["task_category"], lo=fmt_gold(record.get("min_qty")), hi=fmt_gold(record.get("max_qty"))) + "\n")
            if not in_list:
                t.add(_("Excluded from your Delivery Task list.") + "\n", "warn")
            src = lib.delivery.get("source") or {}
            t.add(_("Source: "), "muted")
            t.link(_("TibiaWiki Delivery Task"), src.get("url") or record.get("url"))
            t.add(_(" · page revised {rev} · fetched {fetched}").format(
                rev=fmt_date(src.get("revision_timestamp")), fetched=fmt_date(src.get("fetched_at"))) + "\n", "muted")
        elif in_list:
            t.add(_("Added to your Delivery Task list by you (not in the source list).") + "\n")
        else:
            t.add(_("Not a Delivery Task candidate.") + "\n", "muted")

        # Values
        t.add(_("Values") + "\n", "h2")
        values = lib.values_for(client_id, dtitle)
        for kind, label in ((NPC_PAYS, _("NPCs pay you (sell to NPC)")), (NPC_CHARGES, _("NPCs charge you (buy from NPC)"))):
            rows = [v for v in values if v.kind == kind]
            if not rows:
                t.add(label + ": " + _("no data") + "\n", "muted")
                continue
            t.add(label + ":\n")
            # Same price from the same source at several NPCs/locations: show once.
            groups: dict[tuple, list] = {}
            for v in rows:
                groups.setdefault((v.amount, v.currency, v.source, v.source_url), []).append(v)
            for members in groups.values():
                v = members[0]
                t.add(f"  {fmt_gold(v.amount)} {v.currency}")
                npcs = list(dict.fromkeys(m.detail.split(",")[0] for m in members if m.detail))
                if len(members) > 1 and npcs:
                    shown = ", ".join(npcs[:3]) + (_(" +{n} more").format(n=len(npcs) - 3) if len(npcs) > 3 else "")
                    t.add(_(" — {npcs} ({n} locations)").format(npcs=shown, n=len(members)))
                elif v.detail:
                    t.add(f" — {v.detail}")
                t.add("  ")
                if v.source_url:
                    t.link(v.source, v.source_url)
                else:
                    t.add(v.source, "muted")
                t.add(_(", retrieved {date}").format(date=fmt_date(v.retrieved_at)), "muted")
                if v.is_stale():
                    t.add(" " + _("(older than {n} days — may be outdated)").format(n=STALE_AFTER_DAYS), "warn")
                t.add("\n")
        t.add(_("Market value: not available. No market source is configured; market prices vary by world.") + "\n",
              "muted")

        # Drops
        t.add(_("Dropped by") + "\n", "h2")
        wiki = lib.wiki_record_for(client_id, dtitle)
        if wiki and not wiki.get("not_found"):
            drops = wiki.get("dropped_by") or []
            t.add((", ".join(drops) if drops else _("No creatures listed.")) + "\n")
            t.add(_("Source: "), "muted")
            t.link(wiki.get("title") or _("TibiaWiki"), wiki["url"])
            t.add(_(" · page revised {rev} · fetched {fetched}").format(
                rev=fmt_date(wiki.get("page_timestamp")), fetched=fmt_date(wiki.get("fetched_at"))) + "\n", "muted")
        elif wiki and wiki.get("not_found"):
            t.add(_("No TibiaWiki page confirmed for client ID {id} (looked up {date}).").format(
                id=client_id, date=fmt_date(wiki.get("fetched_at"))) + "\n", "muted")
        else:
            t.add(_("Not loaded. Use the button below to look it up on TibiaWiki.") + "\n", "muted")
        t.done()
        can_lookup = client_id is not None and item is not None and not (wiki and wiki.get("dropped_by") is not None
                                                                           and not wiki.get("not_found"))
        self.lookup_button.state(["!disabled"] if can_lookup else ["disabled"])

    def _lookup(self) -> None:
        client_id = self.client_id
        if client_id is None:
            return
        name = self.app.library.lookup_title_hint(client_id)
        self.lookup_button.state(["disabled"])
        self.app.set_status(_("Looking up “{name}” on TibiaWiki…").format(name=name))

        def done(record):
            self.app.library.store_wiki_lookup(client_id, record, utc_now_iso())
            self.app.store.save_lookups(self.app.library.wiki_lookups)
            self.app.set_status(_("TibiaWiki lookup finished.") if record else
                                _("No TibiaWiki page lists client ID {id}.").format(id=client_id))
            if self.client_id == client_id:
                self.show(client_id, self.title)

        def failed(error):
            self.app.set_status(_("TibiaWiki lookup failed: {error}").format(error=error))
            self.lookup_button.state(["!disabled"])

        run_in_background(self, lambda: TibiaWikiSource(PoliteHttpClient()).lookup_item(name, client_id), done, failed)
