"""Data sources, freshness, and settings."""

import os
import tkinter as tk
from tkinter import messagebox, ttk

from ..i18n import _
from ..sources import tibia_client, tibiawiki
from .widgets import PAD, InfoText, Tree, fmt_date

EVALUATED = [
    (_("TibiaWiki (Fandom) — used"), "https://tibia.fandom.com/wiki/Delivery_Task",
     _("Delivery Task candidates with task category, min/max quantity and NPC buy price; per item: client IDs, "
       "creature drops, NPC prices. Read through the MediaWiki API, about one request per second.")),
    (_("Installed Tibia client — used"), None,
     _("Authoritative client item IDs and names, market categories, NPC trade offers. Read locally, never sent "
       "anywhere.")),
    (_("TibiaWiki BR Weekly Tasks — evaluated, not used"), "https://www.tibiawiki.com.br/wiki/Weekly_Tasks",
     _("Portuguese-language overview of Weekly Tasks; useful for manual cross-checking only.")),
    (_("TibiaWiki API (tibiawiki.dev) — evaluated, not used"), "https://tibiawiki.dev/",
     _("Unofficial JSON mirror of TibiaWiki infoboxes (same fields as the wiki). Not needed because the wiki's own "
       "API provides the data directly.")),
    (_("TibiaPal Deliveries — reference only"), "https://tibiapal.com/deliveries",
     _("Shows delivery-item market values, but states it is no longer maintained for items added in the Summer "
       "2026 update and that market values vary by world. Not used as a data source.")),
    (_("Official Tibia Quick Loot guide"), "https://www.tibia.com/gameguides/?section=controls&subtopic=manual",
     _("Describes managing Accepted Loot through the Cyclopedia.")),
    (_("PCGamingWiki, TibiaQA, TibiaBR forum"), "https://www.pcgamingwiki.com/wiki/Tibia",
     _("Community leads for the loot-file location. The file format is checked against your own client's "
       "files instead.")),
]


class SourcesTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Button(top, text=_("Check for updates"), command=app.check_for_updates).pack(side="left")
        ttk.Label(top, foreground="gray40", text=_(
            "Updates are only checked when you press this button. You review all changes before they are applied, "
            "and your own edits are kept.")).pack(side="left", padx=PAD)

        self.table = Tree(self, [
            ("source", _("Source"), 170), ("version", _("Version / revision"), 380),
            ("success", _("Last successful update"), 150), ("attempt", _("Last attempt"), 130),
            ("error", _("Last problem"), 300),
        ], height=4)
        self.table.grid(row=1, column=0, sticky="ew", pady=PAD)

        info = InfoText(self, height=16)
        info.grid(row=2, column=0, sticky="nsew")
        info.add(_("Sources evaluated") + "\n", "h2")
        for title, url, text in EVALUATED:
            if url:
                info.link(title, url)
            else:
                info.add(title)
            info.add("\n" + text + "\n", "muted")
        info.add(_("Item values") + "\n", "h2")
        info.add(_("NPC prices are shown with their source and date, separating what NPCs pay you from what they "
                   "charge. No market price source is configured: market values vary by world and are not "
                   "estimated. Nothing is added to your list based on value.") + "\n", "muted")
        info.done()

        settings = ttk.LabelFrame(self, text=_("Settings"), padding=PAD)
        settings.grid(row=3, column=0, sticky="ew", pady=(PAD, 0))
        ttk.Label(settings, text=_("Accepted Loot list limit:")).pack(side="left")
        self.limit = tk.StringVar()
        ttk.Entry(settings, textvariable=self.limit, width=8).pack(side="left", padx=4)
        ttk.Button(settings, text=_("Save"), command=self._save_limit).pack(side="left")
        ttk.Label(settings, foreground="gray40", text=_(
            "Leave empty if unknown. No current official limit was found, so none is assumed.")).pack(side="left", padx=PAD)
        ttk.Button(settings, text=_("Open app data folder"),
                   command=lambda: os.startfile(app.store.root)).pack(side="right")

        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)

    def refresh(self) -> None:
        lib = self.app.library
        log = self.app.store.source_log()
        limit = lib.state.loot_list_limit
        self.limit.set("" if limit is None else str(limit))
        csrc = lib.catalog.get("source") or {}
        dsrc = lib.delivery.get("source") or {}
        isrc = lib.wiki_index.get("source") or {}
        rows = []
        for sid, name, version, fallback in (
            (tibia_client.SOURCE_ID, _("Installed Tibia client"),
             _("client {v}, {n} items").format(v=csrc.get("client_version") or "?", n=len(lib.items_by_id))
             if csrc else _("not loaded"), None),
            (tibiawiki.SOURCE_ID, _("TibiaWiki (Fandom)"),
             _("Delivery Task page revised {d}").format(d=fmt_date(dsrc.get("revision_timestamp")))
             + (" " + _("(bundled copy)") if dsrc.get("bundled") else ""),
             dsrc.get("fetched_at") if dsrc.get("bundled") else None),
            (tibiawiki.INDEX_SOURCE_ID, _("TibiaWiki item pages"),
             _("{n} pages, {m} matched to client items").format(n=len(lib.wiki_index["pages"]), m=len(lib.wiki_by_id))
             + (" " + _("(bundled copy)") if isrc.get("bundled") else ""),
             isrc.get("fetched_at") if isrc.get("bundled") else None),
        ):
            entry = log.get(sid, {})
            success = entry.get("last_success") or fallback
            rows.append((sid, (name, version, fmt_date(success), fmt_date(entry.get("last_attempt")),
                               entry.get("last_error") or ""), ("warn",) if entry.get("last_error") else ()))
        self.table.replace(rows)

    def _save_limit(self) -> None:
        text = self.limit.get().strip()
        if text and not (text.isdigit() and int(text) > 0):
            messagebox.showerror(_("Invalid limit"), _("Enter a positive whole number, or leave it empty."), parent=self)
            return
        self.app.library.state.loot_list_limit = int(text) if text else None
        self.app.changed()
        self.app.set_status(_("Limit saved."))
