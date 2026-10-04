"""Tibia character-folder selection, safe installation, and backup restore."""

import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import lootfile, paths
from ..i18n import _
from ..tibia_process import is_tibia_running
from .accepted_tab import limit_message
from .widgets import PAD, InfoText, Tree

MODE_TEXT = {lootfile.MODE_ACCEPTED: _("Accepted Loot"), lootfile.MODE_SKIPPED: _("Skipped Loot"), None: "—"}


def ask_close_tibia(parent) -> bool:
    """Return True once Tibia is not running. Never closes the game itself."""
    while is_tibia_running():
        if not messagebox.askretrycancel(_("Please close Tibia"), _(
                "Tibia is running. Close the game yourself, then press Retry.\n\nThe client keeps the loot list in "
                "memory and can overwrite the file when it exits, so changes are only made while it is closed."),
                parent=parent):
            return False
    return True


class InstallTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self.characters: dict[str, lootfile.CharacterFolder] = {}

        loc = ttk.LabelFrame(self, text=_("Tibia character data folder"), padding=PAD)
        loc.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.path_var = tk.StringVar()
        ttk.Entry(loc, textvariable=self.path_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(loc, text=_("Browse…"), command=self._browse).pack(side="left", padx=(PAD, 0))
        ttk.Button(loc, text=_("Use default"), command=self._use_default).pack(side="left", padx=4)
        ttk.Button(loc, text=_("Refresh"), command=self.app.changed).pack(side="left")
        self.format_label = ttk.Label(self, wraplength=1000, justify="left")
        self.format_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=PAD)

        left = ttk.Frame(self)
        left.grid(row=2, column=0, sticky="nsew")
        ttk.Label(left, wraplength=560, justify="left", text=_(
            "Character folders are named by number. This app cannot tell which character a folder belongs to — "
            "give folders a label you will recognise.")).pack(anchor="w")
        self.table = Tree(left, [
            ("folder", _("Folder"), 90), ("label", _("Your label"), 150), ("mode", _("Current mode"), 110),
            ("accepted", _("Accepted"), 75), ("skipped", _("Skipped"), 70), ("note", _("Status"), 200),
        ], selectmode="browse", height=8)
        self.table.pack(fill="both", expand=True, pady=PAD)
        self.table.tree.bind("<<TreeviewSelect>>", lambda _e: self._selection_changed())
        ttk.Button(left, text=_("Set label…"), command=self._set_label).pack(anchor="w")

        right = ttk.Frame(self)
        right.grid(row=2, column=1, sticky="nsew", padx=(PAD * 2, 0))
        box = ttk.LabelFrame(right, text=_("Install your Accepted Loot list"), padding=PAD)
        box.pack(fill="x")
        self.mode = tk.StringVar(value=lootfile.MERGE)
        ttk.Radiobutton(box, text=_("Merge — keep the items already on the character's Accepted Loot list and add "
                                    "yours"), value=lootfile.MERGE, variable=self.mode).pack(anchor="w")
        ttk.Radiobutton(box, text=_("Replace — the character's Accepted Loot list becomes exactly your list"),
                        value=lootfile.REPLACE, variable=self.mode).pack(anchor="w")
        ttk.Label(box, foreground="gray40", wraplength=440, justify="left", text=_(
            "Both switch the character to Accepted Loot mode. The Skipped Loot list is kept as it is. You will see "
            "a preview first, and a backup is made before anything is written.")).pack(anchor="w", pady=PAD)
        self.install_button = ttk.Button(box, text=_("Preview and install…"), command=self._preview)
        self.install_button.pack(anchor="w")

        backups = ttk.LabelFrame(right, text=_("Backups for this character"), padding=PAD)
        backups.pack(fill="both", expand=True, pady=(PAD, 0))
        self.backups = Tree(backups, [("file", _("Backup file"), 260), ("date", _("Saved"), 140)],
                            selectmode="browse", height=6)
        self.backups.pack(fill="both", expand=True)
        row = ttk.Frame(backups)
        row.pack(fill="x", pady=(PAD, 0))
        ttk.Button(row, text=_("Restore selected…"), command=self._restore).pack(side="left")
        ttk.Button(row, text=_("Open backups folder"), command=self._open_backups).pack(side="left", padx=4)

        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)

    # --- data -----------------------------------------------------------------

    def refresh(self) -> None:
        state = self.app.library.state
        self.path_var.set(state.characterdata_dir or "")
        report = self.app.format_report()
        self.format_label.configure(text=report.message, foreground="#1a7a2e" if report.validated else "#a33")
        self.characters = {c.folder_id: c for c in self.app.characters()}
        if not self.characters:
            self.format_label.configure(text=_("No character folders were found here. Choose the folder that "
                                               "contains your numbered character folders (usually …\\Tibia\\"
                                               "packages\\Tibia\\characterdata)."), foreground="#a33")
        rows = []
        for fid, c in self.characters.items():
            if c.error:
                note, tags = _("Unreadable: {error}").format(error=c.error), ("warn",)
            elif not c.exists:
                note, tags = _("No loot file yet"), ("muted",)
            else:
                note, tags = _("OK"), ()
            data = c.data or {}
            rows.append((fid, (fid, state.character_labels.get(fid, ""), MODE_TEXT.get(c.mode, c.mode),
                               len(data.get(lootfile.KEY_ACCEPTED, [])) if c.data else "—",
                               len(data.get(lootfile.KEY_SKIPPED, [])) if c.data else "—", note), tags))
        self.table.replace(rows)
        self._selection_changed()

    def _selected(self) -> lootfile.CharacterFolder | None:
        sel = self.table.selection()
        return self.characters.get(sel[0]) if sel else None

    def _selection_changed(self) -> None:
        c = self._selected()
        ok = c is not None and c.error is None and self.app.format_report().validated
        self.install_button.state(["!disabled"] if ok else ["disabled"])
        rows = []
        if c:
            for b in lootfile.list_backups(self.app.store.backups, c.folder_id):
                rows.append((str(b), (b.name, datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")), ()))
        self.backups.replace(rows)

    # --- actions ----------------------------------------------------------------

    def _set_folder(self, folder: Path) -> None:
        if (folder / "characterdata").is_dir():
            folder = folder / "characterdata"
        self.app.library.state.characterdata_dir = str(folder)
        self.app.changed()
        self.app.set_status(_("Tibia folder set. Press Check for updates to reload the item catalog from this "
                              "client if needed."))

    def _browse(self) -> None:
        start = self.app.library.state.characterdata_dir or str(paths.default_characterdata_dir())
        folder = filedialog.askdirectory(parent=self, initialdir=start if os.path.isdir(start) else None,
                                         title=_("Choose the Tibia characterdata folder"))
        if folder:
            self._set_folder(Path(folder))

    def _use_default(self) -> None:
        self._set_folder(paths.default_characterdata_dir())

    def _set_label(self) -> None:
        c = self._selected()
        if not c:
            return
        labels = self.app.library.state.character_labels
        value = simpledialog.askstring(_("Label folder"), _("Label for folder {id} (e.g. your character's name):")
                                       .format(id=c.folder_id), initialvalue=labels.get(c.folder_id, ""), parent=self)
        if value is None:
            return
        if value.strip():
            labels[c.folder_id] = value.strip()
        else:
            labels.pop(c.folder_id, None)
        self.app.changed()

    def _preview(self) -> None:
        c = self._selected()
        if not c or not self.app.format_report().validated or c.error:
            return
        ids = sorted(self.app.library.accepted_ids())
        if not ids:
            messagebox.showinfo(_("Nothing to install"), _("Your Accepted Loot list has no exportable items."), parent=self)
            return
        plan = lootfile.plan_install(c.data, ids, self.mode.get())
        InstallPreview(self, self.app, c, plan)

    def _restore(self) -> None:
        c = self._selected()
        sel = self.backups.selection()
        if not c or not sel:
            return
        backup = Path(sel[0])
        try:
            data = lootfile.read_file(backup)
        except lootfile.LootFileError as e:
            messagebox.showerror(_("Cannot restore"), str(e), parent=self)
            return
        if not messagebox.askyesno(_("Restore backup"), _(
                "Restore {name} for folder {id}?\n\nIt has {a} Accepted and {s} Skipped item(s), mode: {mode}.\n"
                "The current file is backed up first.").format(
                name=backup.name, id=c.folder_id, a=len(data[lootfile.KEY_ACCEPTED]),
                s=len(data[lootfile.KEY_SKIPPED]), mode=MODE_TEXT.get(data[lootfile.KEY_MODE])), parent=self):
            return
        if not ask_close_tibia(self):
            return
        try:
            lootfile.restore_backup(backup, c, self.app.store.backups)
        except (OSError, lootfile.LootFileError) as e:
            messagebox.showerror(_("Restore failed"), str(e), parent=self)
            return
        self.app.changed()
        messagebox.showinfo(_("Restored"), _("The backup was restored and verified."), parent=self)

    def _open_backups(self) -> None:
        c = self._selected()
        folder = lootfile.backup_dir_for(self.app.store.backups, c.folder_id) if c else self.app.store.backups
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)


class InstallPreview(tk.Toplevel):
    def __init__(self, parent: InstallTab, app, folder: lootfile.CharacterFolder, plan: lootfile.InstallPlan):
        super().__init__(parent)
        self.app, self.folder, self.plan, self.tab = app, folder, plan, parent
        label = app.library.state.character_labels.get(folder.folder_id)
        self.title(_("Preview installation"))
        self.geometry("760x600")
        self.transient(parent.winfo_toplevel())

        info = InfoText(self, height=11)
        info.pack(fill="x", padx=PAD, pady=PAD)
        info.add(_("{mode} for folder {id}").format(
            mode=_("Merge") if plan.mode == lootfile.MERGE else _("Replace"),
            id=folder.folder_id + (f" ({label})" if label else "")) + "\n", "h1")
        info.add(str(folder.file_path) + "\n", "muted")
        if plan.creates_file:
            info.add(_("This folder has no loot file yet; a new one will be created.") + "\n", "warn")
        if plan.mode_change:
            info.add(_("Mode change: {old} → Accepted Loot. Only items on the Accepted list will be looted.").format(
                old=MODE_TEXT.get(plan.old_mode, plan.old_mode)) + "\n", "warn")
        info.add(_("Accepted Loot: {a} added, {k} unchanged, {r} removed → {t} in total.").format(
            a=len(plan.added), k=len(plan.kept), r=len(plan.removed),
            t=len(plan.new_data[lootfile.KEY_ACCEPTED])) + "\n")
        if plan.removed:
            info.add(_("Replace will remove {n} item(s) the character currently accepts (listed below).").format(
                n=len(plan.removed)) + "\n", "warn")
        info.add(_("Skipped Loot list: kept unchanged ({n} item(s)).").format(
            n=len(plan.new_data.get(lootfile.KEY_SKIPPED, []))) + "\n")
        if plan.also_skipped:
            info.add(_("{n} item(s) are on both lists: {names}. They are left on both; in Accepted Loot mode the "
                       "Skipped list is not used.").format(
                n=len(plan.also_skipped), names=", ".join(app.library.item_name(i) for i in plan.also_skipped[:8])) + "\n",
                "muted")
        text, warn = limit_message(len(plan.new_data[lootfile.KEY_ACCEPTED]), app.library.state.loot_list_limit)
        info.add(text + "\n", "warn" if warn else "muted")
        info.add(_("A backup of the current file is saved first in: {path}").format(
            path=lootfile.backup_dir_for(app.store.backups, folder.folder_id)) + "\n", "muted")
        info.done()

        table = Tree(self, [("change", _("Change"), 90), ("name", _("Item"), 300), ("id", _("Client ID"), 90)], height=12)
        table.pack(fill="both", expand=True, padx=PAD)
        rows = [(f"r{i}", (_("Remove"), app.library.item_name(i), i), ("warn",)) for i in plan.removed]
        rows += [(f"a{i}", (_("Add"), app.library.item_name(i), i), ()) for i in plan.added]
        rows += [(f"k{i}", (_("Keep"), app.library.item_name(i), i), ("muted",)) for i in plan.kept]
        table.replace(rows)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Button(buttons, text=_("Cancel"), command=self.destroy).pack(side="right")
        ttk.Button(buttons, text=_("Back up and install"), command=self._install).pack(side="right", padx=4)
        self.grab_set()

    def _install(self) -> None:
        if not ask_close_tibia(self):
            return
        try:
            result = lootfile.install(self.plan, self.folder, self.app.store.backups)
        except (OSError, lootfile.LootFileError) as e:
            messagebox.showerror(_("Install failed"), str(e), parent=self)
            return
        backup = result.backup.name if result.backup else _("none (there was no previous file)")
        messagebox.showinfo(_("Installed"), _(
            "Installed and verified {n} Accepted Loot items.\n\nBackup: {backup}\nYou can restore it from the Install "
            "tab.").format(n=len(self.plan.new_data[lootfile.KEY_ACCEPTED]), backup=backup), parent=self)
        self.destroy()
        self.app.changed()
