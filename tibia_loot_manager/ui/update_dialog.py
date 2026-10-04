"""Review data changes before they are applied."""

import tkinter as tk
from tkinter import ttk

from ..i18n import _
from ..updates import CHANGED, NEW, REMOVED, UpdateReview
from .widgets import PAD, InfoText

KIND_TEXT = {NEW: _("New"), CHANGED: _("Changed"), REMOVED: _("Removed")}
AREA_TEXT = {"delivery": _("Delivery Task candidates"), "catalog": _("Item catalog (client)"),
             "wiki_index": _("TibiaWiki item pages (names, drops)")}


class UpdateReviewDialog(tk.Toplevel):
    def __init__(self, parent, review: UpdateReview, on_apply, on_cancel):
        super().__init__(parent)
        self.title(_("Review data updates"))
        self.geometry("900x620")
        self.transient(parent)
        self.on_apply, self.on_cancel = on_apply, on_cancel
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        info = InfoText(self, height=6)
        info.pack(fill="x", padx=PAD, pady=PAD)
        for r in review.results:
            if r.ok:
                info.add("✔ " + r.label + ": " + _("data retrieved.") + "\n", "ok")
            else:
                info.add("✖ " + r.label + ": " + _("could not be updated — cached data is kept. {error}").format(
                    error=r.error) + "\n", "warn")
        if review.any_success:
            info.add(_("{n} change(s) found. Nothing has been changed yet.").format(n=len(review.changes))
                     if review.changes else _("No changes found. Applying will just record the check time."))
        info.done()

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=PAD)
        tree = ttk.Treeview(frame, columns=("details", "note"), show="tree headings")
        tree.heading("#0", text=_("Item"))
        tree.heading("details", text=_("Details"))
        tree.heading("note", text=_("Effect on your lists"))
        tree.column("#0", width=260)
        tree.column("details", width=330)
        tree.column("note", width=280)
        ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ys.set)
        tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        for area in ("delivery", "catalog", "wiki_index"):
            for kind in (NEW, CHANGED, REMOVED):
                items = [c for c in review.changes if c.area == area and c.kind == kind]
                if not items:
                    continue
                group = tree.insert("", "end", text=f"{AREA_TEXT[area]} — {KIND_TEXT[kind]} ({len(items)})",
                                    open=area == "delivery" and len(items) <= 50)
                for c in items:
                    tree.insert(group, "end", text=c.name, values=(c.details, c.user_note))

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=PAD, pady=PAD)
        self.keep_removed = tk.BooleanVar(value=False)
        removed = [c for c in review.changes if c.area == "delivery" and c.kind == REMOVED]
        if removed:
            ttk.Checkbutton(bottom, variable=self.keep_removed, text=_(
                "Keep the {n} item(s) removed from the source on my Delivery Task list (as my own additions)").format(
                n=len(removed))).pack(anchor="w", pady=(0, PAD))
        ttk.Button(bottom, text=_("Cancel"), command=self._cancel).pack(side="right")
        if review.any_success:
            ttk.Button(bottom, text=_("Apply updates"), command=self._apply).pack(side="right", padx=4)
        self.grab_set()

    def _apply(self) -> None:
        self.destroy()
        self.on_apply(self.keep_removed.get())

    def _cancel(self) -> None:
        self.destroy()
        self.on_cancel()
