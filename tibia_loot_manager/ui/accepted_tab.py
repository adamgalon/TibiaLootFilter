"""The personal Accepted Loot list."""

import tkinter as tk
from tkinter import messagebox, ttk

from ..i18n import _
from ..library import ID_STATUS_TEXT, ORIGIN_TEXT
from .widgets import PAD, Tree

SHOW_ACTIVE, SHOW_EXCLUDED = _("On my list"), _("Removed by me")


def limit_message(count: int, limit: int | None) -> tuple[str, bool]:
    """Return (text, is_warning) describing the item count against the user-set limit."""
    if limit is None:
        return _("Client list limit: unknown (no verified current limit; you can set one on the "
                 "Data Sources tab)."), False
    if count > limit:
        return _("Warning: {count} items exceeds the limit of {limit} you set. Tibia may not accept "
                 "all of them.").format(count=count, limit=limit), True
    return _("Within the limit of {limit} you set.").format(limit=limit), False


class AcceptedTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self._entries = {}

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        self.follow = tk.BooleanVar()
        ttk.Checkbutton(top, text=_("Include my Delivery Task list"), variable=self.follow,
                        command=self._toggle_follow).pack(side="left")
        ttk.Label(top, text=_("Show:")).pack(side="left", padx=(PAD * 3, 0))
        self.show = tk.StringVar(value=SHOW_ACTIVE)
        for value in (SHOW_ACTIVE, SHOW_EXCLUDED):
            ttk.Radiobutton(top, text=value, value=value, variable=self.show, command=self.refresh).pack(side="left", padx=4)

        summary = ttk.Frame(self)
        summary.grid(row=1, column=0, sticky="ew", pady=(0, PAD))
        self.count = ttk.Label(summary, font=("Segoe UI", 10, "bold"))
        self.count.pack(anchor="w")
        self.limit = ttk.Label(summary)
        self.limit.pack(anchor="w")
        self.unverified = ttk.Label(summary, foreground="#a33", wraplength=900)
        self.unverified.pack(anchor="w")

        self.table = Tree(self, [
            ("name", _("Name"), 220), ("id", _("Client ID"), 75), ("origin", _("From"), 170),
            ("status", _("ID status"), 150), ("export", _("Exportable"), 75), ("notes", _("Notes"), 260),
        ])
        self.table.grid(row=2, column=0, sticky="nsew")

        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, sticky="ew", pady=(PAD, 0))
        self.remove_button = ttk.Button(buttons, text=_("Remove from Accepted Loot"), command=self._remove)
        self.remove_button.pack(side="left")
        self.restore_button = ttk.Button(buttons, text=_("Put back"), command=self._restore)
        self.restore_button.pack(side="left", padx=4)
        ttk.Button(buttons, text=_("Add items from catalog…"), command=lambda: app.show_tab("catalog")).pack(side="left")
        ttk.Button(buttons, text=_("Copy or export…"), command=lambda: app.show_tab("export")).pack(side="left", padx=4)
        ttk.Button(buttons, text=_("Restore defaults…"), command=self._defaults).pack(side="right")

        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)

    def refresh(self) -> None:
        lib = self.app.library
        self.follow.set(lib.state.accepted_follow_delivery)
        active, excluded = lib.accepted_entries()
        entries = active if self.show.get() == SHOW_ACTIVE else excluded
        self._entries = {e.key: e for e in entries}
        self.table.replace([
            (e.key, (e.name, e.client_id if e.client_id is not None else "—", ORIGIN_TEXT[e.origin],
                     ID_STATUS_TEXT.get(e.id_status, e.id_status), "✔" if e.exportable else _("No"),
                     lib.item_note(e.client_id) if e.client_id is not None else ""),
             () if e.exportable else ("warn",))
            for e in entries
        ])
        exportable = [e for e in active if e.exportable]
        self.count.configure(text=_("{n} items on your Accepted Loot list ({x} exportable)").format(
            n=len(active), x=len(exportable)))
        text, warn = limit_message(len(exportable), lib.state.loot_list_limit)
        self.limit.configure(text=text, foreground="#a33" if warn else "gray40")
        blocked = len(active) - len(exportable)
        self.unverified.configure(text=_(
            "{n} item(s) cannot be exported or installed because their client ID is unverified. They are still "
            "included in the manual copy list.").format(n=blocked) if blocked else "")
        showing_active = self.show.get() == SHOW_ACTIVE
        self.remove_button.state(["!disabled"] if showing_active else ["disabled"])
        self.restore_button.state(["disabled"] if showing_active else ["!disabled"])

    def _selected(self):
        return [self._entries[k] for k in self.table.selection() if k in self._entries]

    def _toggle_follow(self) -> None:
        self.app.library.state.accepted_follow_delivery = self.follow.get()
        self.app.changed()

    def _remove(self) -> None:
        for entry in self._selected():
            self.app.library.remove_from_accepted(entry)
        self.app.changed()

    def _restore(self) -> None:
        for entry in self._selected():
            self.app.library.restore_accepted_entry(entry)
        self.app.changed()

    def _defaults(self) -> None:
        state = self.app.library.state
        if not messagebox.askyesno(_("Restore defaults"), _(
                "Reset your Accepted Loot list to exactly your Delivery Task list?\n\nThis discards {extra} "
                "item(s) you added directly and {excluded} removal(s). Your Delivery Task list edits are kept."
        ).format(extra=len(state.accepted_extra), excluded=len(state.accepted_excluded)), parent=self):
            return
        self.app.library.restore_accepted_defaults()
        self.app.changed()
