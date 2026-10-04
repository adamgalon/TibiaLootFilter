"""The editable Delivery Task candidate list."""

import tkinter as tk
from tkinter import messagebox, ttk

from ..i18n import _
from ..library import ID_STATUS_TEXT, ORIGIN_TEXT, delivery_url
from .details import ItemDetails
from .widgets import PAD, Tree, fmt_date, fmt_gold, link_label

SHOW_ACTIVE, SHOW_EXCLUDED = _("On my list"), _("Excluded by me")


class DeliveryTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self._entries = {}

        head = ttk.Frame(self)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=_("Items that Delivery Tasks can request. Your Accepted Loot list includes this list "
                               "unless you turn that off on the Accepted Loot tab."), wraplength=900).pack(anchor="w")
        src = ttk.Frame(head)
        src.pack(anchor="w", fill="x", pady=(2, PAD))
        ttk.Label(src, text=_("Source:")).pack(side="left")
        link_label(src, _("TibiaWiki Delivery Task"), delivery_url()).pack(side="left", padx=4)
        self.source_info = ttk.Label(src, foreground="gray40")
        self.source_info.pack(side="left")

        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, PAD))
        ttk.Label(bar, text=_("Show:")).pack(side="left")
        self.show = tk.StringVar(value=SHOW_ACTIVE)
        for value in (SHOW_ACTIVE, SHOW_EXCLUDED):
            ttk.Radiobutton(bar, text=value, value=value, variable=self.show, command=self.refresh).pack(side="left", padx=4)
        self.count = ttk.Label(bar, foreground="gray40")
        self.count.pack(side="right")

        self.table = Tree(self, [
            ("name", _("Name"), 200), ("category", _("Task category"), 120), ("min", _("Min"), 50),
            ("max", _("Max"), 50), ("price", _("NPC buy price"), 95), ("id", _("Client ID"), 75),
            ("status", _("ID status"), 120), ("origin", _("From"), 160),
        ])
        self.table.grid(row=2, column=0, sticky="nsew")
        self.table.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        self.details = ItemDetails(self, app)
        self.details.grid(row=2, column=1, sticky="nsew", padx=(PAD, 0))

        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))
        self.remove_button = ttk.Button(buttons, text=_("Remove from list"), command=self._remove)
        self.remove_button.pack(side="left")
        self.restore_button = ttk.Button(buttons, text=_("Put back on list"), command=self._restore)
        self.restore_button.pack(side="left", padx=4)
        ttk.Button(buttons, text=_("Add items from catalog…"), command=lambda: app.show_tab("catalog")).pack(side="left")
        ttk.Button(buttons, text=_("Restore source defaults…"), command=self._defaults).pack(side="right")

        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)

    def refresh(self) -> None:
        lib = self.app.library
        src = lib.delivery.get("source") or {}
        info = _("page revised {rev}, fetched {fetched}").format(
            rev=fmt_date(src.get("revision_timestamp")), fetched=fmt_date(src.get("fetched_at")))
        if src.get("bundled"):
            info += " " + _("(bundled with the app)")
        self.source_info.configure(text=info)

        active, excluded = lib.delivery_entries()
        entries = active if self.show.get() == SHOW_ACTIVE else excluded
        self._entries = {e.key: e for e in entries}
        rows = []
        for e in entries:
            d = e.delivery or {}
            rows.append((e.key, (e.name, d.get("task_category", "—"), fmt_gold(d.get("min_qty")),
                                 fmt_gold(d.get("max_qty")), fmt_gold(d.get("npc_buy_price")),
                                 e.client_id if e.client_id is not None else "—",
                                 ID_STATUS_TEXT.get(e.id_status, e.id_status), ORIGIN_TEXT[e.origin]),
                         () if e.exportable else ("warn",)))
        self.table.replace(rows)
        self.count.configure(text=_("{active} on your list · {excluded} excluded · {added} added by you").format(
            active=len(active), excluded=len(excluded), added=len(lib.state.delivery_added)))
        showing_active = self.show.get() == SHOW_ACTIVE
        self.remove_button.state(["!disabled"] if showing_active else ["disabled"])
        self.restore_button.state(["disabled"] if showing_active else ["!disabled"])
        self._show_selected()

    def _selected(self):
        return [self._entries[k] for k in self.table.selection() if k in self._entries]

    def _show_selected(self) -> None:
        sel = self._selected()
        if not sel:
            self.details.show(None)
        else:
            e = sel[0]
            title = e.key[5:] if e.key.startswith("wiki:") else None
            self.details.show(e.client_id, title, e.id_status)

    def _remove(self) -> None:
        for entry in self._selected():
            self.app.library.remove_from_delivery(entry)
        self.app.changed()

    def _restore(self) -> None:
        for entry in self._selected():
            self.app.library.restore_delivery_entry(entry)
        self.app.changed()

    def _defaults(self) -> None:
        state = self.app.library.state
        if not messagebox.askyesno(_("Restore source defaults"), _(
                "Discard your Delivery Task list edits ({removed} excluded, {added} added) and use the source "
                "list as it is?\n\nYour Accepted Loot edits are not affected.").format(
                removed=len(state.delivery_removed), added=len(state.delivery_added)), parent=self):
            return
        self.app.library.restore_delivery_defaults()
        self.app.changed()
