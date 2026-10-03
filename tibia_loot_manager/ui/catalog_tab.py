"""Searchable item catalog."""

import tkinter as tk
from tkinter import ttk

from ..i18n import _
from ..library import ID_STATUS_TEXT, VERIFIED
from .details import ItemDetails
from .widgets import PAD, Tree

ALL = _("All categories")
YES = "✔"


def matches_search(query: str, name: str, client_id: int | None) -> bool:
    """Decide whether a catalog row matches the search box text.

    ``query`` is already stripped and lower-cased and is never empty here.
    ``name`` is the in-game item name; ``client_id`` is None for wiki items
    whose client ID is unverified.
    """
    # TODO(human): choose the matching rules. Current behaviour: plain substring or exact ID.
    return query in name.lower() or query == str(client_id)


class CatalogTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self._after = None

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, PAD))
        ttk.Label(bar, text=_("Search:")).pack(side="left")
        self.query = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.query, width=32)
        entry.pack(side="left", padx=(4, PAD * 2))
        entry.focus_set()
        ttk.Label(bar, text=_("Category:")).pack(side="left")
        self.category = tk.StringVar(value=ALL)
        self.category_box = ttk.Combobox(bar, textvariable=self.category, state="readonly", width=22)
        self.category_box.pack(side="left", padx=(4, PAD * 2))
        self.only_delivery = tk.BooleanVar()
        ttk.Checkbutton(bar, text=_("Delivery Task items only"), variable=self.only_delivery,
                        command=self.refresh).pack(side="left")
        self.only_accepted = tk.BooleanVar()
        ttk.Checkbutton(bar, text=_("On my Accepted Loot list only"), variable=self.only_accepted,
                        command=self.refresh).pack(side="left", padx=(PAD, 0))
        self.query.trace_add("write", lambda *_a: self._debounce())
        self.category_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        self.table = Tree(self, [
            ("name", _("Name"), 200), ("id", _("Client ID"), 70), ("category", _("Category"), 120),
            ("delivery", _("Delivery Task"), 90), ("accepted", _("Accepted Loot"), 90), ("status", _("ID status"), 80),
            ("notes", _("Notes"), 230),
        ])
        self.table.grid(row=1, column=0, sticky="nsew")
        self.table.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())

        self.details = ItemDetails(self, app)
        self.details.grid(row=1, column=1, sticky="nsew", padx=(PAD, 0))

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))
        ttk.Button(buttons, text=_("Add to Accepted Loot"), command=self._add_accepted).pack(side="left")
        ttk.Button(buttons, text=_("Remove from Accepted Loot"), command=self._remove_accepted).pack(side="left", padx=4)
        ttk.Separator(buttons, orient="vertical").pack(side="left", fill="y", padx=PAD)
        ttk.Button(buttons, text=_("Add to Delivery Task list"), command=self._add_delivery).pack(side="left")
        ttk.Button(buttons, text=_("Remove from Delivery Task list"), command=self._remove_delivery).pack(side="left", padx=4)
        self.count = ttk.Label(buttons, foreground="gray40")
        self.count.pack(side="right")

        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)

    def _debounce(self) -> None:
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(250, self.refresh)

    def refresh(self) -> None:
        lib = self.app.library
        categories = sorted({i["category"] for i in lib.items_by_id.values()})
        self.category_box["values"] = [ALL] + categories
        query = self.query.get().strip().lower()
        category = self.category.get()
        accepted = lib.accepted_ids()
        delivery = lib.delivery_ids()
        rows = []
        for cid, item in lib.items_by_id.items():
            wiki_title = lib.wiki_title(cid)
            if query and not (matches_search(query, item["name"], cid)
                              or (wiki_title and matches_search(query, wiki_title, None))):
                continue
            if category != ALL and item["category"] != category:
                continue
            in_delivery, in_accepted = cid in delivery, cid in accepted
            if (self.only_delivery.get() and not in_delivery) or (self.only_accepted.get() and not in_accepted):
                continue
            rows.append((str(cid), (item["name"], cid, item["category"], YES if in_delivery else "",
                                    YES if in_accepted else "", ID_STATUS_TEXT[VERIFIED], lib.item_note(cid)), ()))
        # Delivery items without a verified client ID are listed but cannot be exported.
        if category == ALL and not self.only_accepted.get():
            for entry in lib.unresolved_delivery():
                if query and not matches_search(query, entry.name, None):
                    continue
                rows.append((entry.key, (entry.name, "—", entry.delivery["task_category"], YES, "",
                                         ID_STATUS_TEXT[entry.id_status], ""), ("warn",)))
        self.table.replace(rows)
        self.count.configure(text=_("{shown} of {total} items shown").format(
            shown=len(rows), total=len(lib.items_by_id)))
        self._show_selected()

    def _selected_ids(self) -> list[int]:
        return [int(i) for i in self.table.selection() if i.isdigit()]

    def _show_selected(self) -> None:
        sel = self.table.selection()
        if not sel:
            self.details.show(None)
        elif sel[0].isdigit():
            self.details.show(int(sel[0]))
        else:
            entry = next((e for e in self.app.library.unresolved_delivery() if e.key == sel[0]), None)
            self.details.show(None, entry.name if entry else None, entry.id_status if entry else None)

    def _add_accepted(self) -> None:
        for cid in self._selected_ids():
            self.app.library.add_to_accepted(cid)
        self.app.changed()

    def _remove_accepted(self) -> None:
        lib = self.app.library
        ids = set(self._selected_ids())
        for entry in lib.accepted_entries()[0]:
            if entry.client_id in ids:
                lib.remove_from_accepted(entry)
        self.app.changed()

    def _add_delivery(self) -> None:
        for cid in self._selected_ids():
            self.app.library.add_to_delivery(cid)
        self.app.changed()

    def _remove_delivery(self) -> None:
        lib = self.app.library
        ids = set(self._selected_ids())
        for entry in lib.delivery_entries()[0]:
            if entry.client_id in ids:
                lib.remove_from_delivery(entry)
        self.app.changed()
