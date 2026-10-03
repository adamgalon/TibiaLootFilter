"""Manual copy list and game-format file export."""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import lootfile
from ..i18n import _
from .widgets import PAD, link_label

OFFICIAL_GUIDE = "https://www.tibia.com/gameguides/?section=controls&subtopic=manual"
SORT_NAME, SORT_CATEGORY = _("Name"), _("Category, then name")


class ExportTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app

        manual = ttk.LabelFrame(self, text=_("Manual list"), padding=PAD)
        manual.grid(row=0, column=0, sticky="nsew", padx=(0, PAD))
        ttk.Label(manual, wraplength=430, justify="left", text=_(
            "Tibia's official guide describes managing Accepted Loot through the Cyclopedia: find each item there "
            "and add it to your Accepted Loot list. Use this list as a checklist while you do that. Pasting "
            "names directly into the game has not been verified to work.")).pack(anchor="w")
        link_label(manual, _("Official Tibia guide: Quick Loot (Controls → Manual)"), OFFICIAL_GUIDE).pack(anchor="w", pady=(2, PAD))

        opts = ttk.Frame(manual)
        opts.pack(fill="x")
        ttk.Label(opts, text=_("Sort by:")).pack(side="left")
        self.sort = tk.StringVar(value=SORT_NAME)
        box = ttk.Combobox(opts, textvariable=self.sort, values=[SORT_NAME, SORT_CATEGORY], state="readonly", width=18)
        box.pack(side="left", padx=4)
        box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        self.with_ids = tk.BooleanVar()
        ttk.Checkbutton(opts, text=_("Include client IDs"), variable=self.with_ids, command=self.refresh).pack(side="left", padx=PAD)

        text_frame = ttk.Frame(manual)
        text_frame.pack(fill="both", expand=True, pady=PAD)
        self.text = tk.Text(text_frame, width=48, height=20, wrap="none", font=("Segoe UI", 10))
        ys = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=ys.set)
        self.text.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")

        row = ttk.Frame(manual)
        row.pack(fill="x")
        ttk.Button(row, text=_("Copy to clipboard"), command=self._copy).pack(side="left")
        ttk.Button(row, text=_("Save as text file…"), command=self._save_text).pack(side="left", padx=4)
        self.manual_count = ttk.Label(row, foreground="gray40")
        self.manual_count.pack(side="right")

        game = ttk.LabelFrame(self, text=_("Game file export"), padding=PAD)
        game.grid(row=0, column=1, sticky="nsew")
        ttk.Label(game, wraplength=430, justify="left", text=_(
            "Saves your list in the client's lootBlackWhitelist.json format with Accepted Loot turned on. An "
            "exported file contains only this list: its Skipped Loot list is empty. To keep a character's existing "
            "settings, use the Install tab instead, which can merge and makes a backup.")).pack(anchor="w")
        self.format_status = ttk.Label(game, wraplength=430, justify="left")
        self.format_status.pack(anchor="w", pady=PAD)
        self.export_count = ttk.Label(game, wraplength=430, justify="left")
        self.export_count.pack(anchor="w")
        self.export_button = ttk.Button(game, text=_("Export lootBlackWhitelist.json…"), command=self._export)
        self.export_button.pack(anchor="w", pady=PAD)

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

    def _lines(self) -> list[str]:
        lib = self.app.library
        entries = lib.accepted_entries()[0]

        def category(e):
            item = lib.items_by_id.get(e.client_id) if e.client_id is not None else None
            return item["category"] if item else (e.delivery or {}).get("task_category", "")

        if self.sort.get() == SORT_CATEGORY:
            entries.sort(key=lambda e: (category(e).lower(), e.name.lower()))
        else:
            entries.sort(key=lambda e: e.name.lower())
        def line(e):
            text = e.name
            if self.with_ids.get():
                text += f"\t{e.client_id if e.client_id is not None else '?'}"
            if e.client_id is not None and lib.shares_name(e.client_id):
                # The Cyclopedia shows several items with this name: say which one is meant.
                hint = lib.wiki_title(e.client_id)
                text += "\t" + (_("(the one TibiaWiki calls “{title}”, ID {id})").format(title=hint, id=e.client_id)
                                if hint else _("(several items share this name: ID {id})").format(id=e.client_id))
            return text

        return [line(e) for e in entries]

    def refresh(self) -> None:
        lines = self._lines()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self.text.configure(state="disabled")
        self.manual_count.configure(text=_("{n} items").format(n=len(lines)))

        report = self.app.format_report()
        self.format_status.configure(text=report.message, foreground="#1a7a2e" if report.validated else "#a33")
        entries = self.app.library.accepted_entries()[0]
        blocked = [e for e in entries if not e.exportable]
        text = _("{n} items will be exported.").format(n=len(entries) - len(blocked))
        if blocked:
            text += " " + _("{n} item(s) with unverified client IDs are left out: {names}").format(
                n=len(blocked), names=", ".join(e.name for e in blocked[:10]) + ("…" if len(blocked) > 10 else ""))
        self.export_count.configure(text=text, foreground="#a33" if blocked else "")
        self.export_button.state(["!disabled"] if report.validated and len(entries) > len(blocked) else ["disabled"])

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append("\n".join(self._lines()))
        self.app.set_status(_("Copied the list to the clipboard."))

    def _save_text(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", initialfile="accepted-loot.txt",
                                            filetypes=[(_("Text file"), "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._lines()) + "\n")
            self.app.set_status(_("Saved {path}").format(path=path))

    def _export(self) -> None:
        if not self.app.format_report().validated:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile=lootfile.FILE_NAME,
                                            filetypes=[(_("JSON file"), "*.json")])
        if not path:
            return
        ids = sorted(self.app.library.accepted_ids())
        data = lootfile.accepted_only_file(ids)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(lootfile.serialize(data))
            lootfile.read_file(path)
        except (OSError, lootfile.LootFileError) as e:
            messagebox.showerror(_("Export failed"), str(e), parent=self)
            return
        self.app.set_status(_("Exported {n} items to {path}").format(n=len(ids), path=path))
