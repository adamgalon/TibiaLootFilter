"""Main window."""

import ctypes
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .. import APP_NAME, __version__, lootfile
from ..datastore import DataStore
from ..i18n import _
from ..updates import apply_update, check_for_updates, record_failures
from .accepted_tab import AcceptedTab
from .catalog_tab import CatalogTab
from .delivery_tab import DeliveryTab
from .export_tab import ExportTab
from .install_tab import InstallTab
from .sources_tab import SourcesTab
from .update_dialog import UpdateReviewDialog
from .widgets import PAD, run_in_background


class App:
    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()
        self.library, notices = self.store.load_library()
        self._characters = None
        self._format = None

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {__version__}")
        self.root.geometry("1320x820")
        self.root.minsize(1000, 640)
        if "vista" in ttk.Style().theme_names():
            ttk.Style().theme_use("vista")
        self.root.configure(background=ttk.Style().lookup("TFrame", "background") or "SystemButtonFace")

        top = ttk.Frame(self.root, padding=(PAD, PAD, PAD, 0))
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, font=("Segoe UI", 13, "bold")).pack(side="left")
        self.update_button = ttk.Button(top, text=_("Check for updates"), command=self.check_for_updates)
        self.update_button.pack(side="right")
        ttk.Label(top, foreground="gray40", text=_("A local helper — it never reads game memory or asks for your "
                                                   "account.")).pack(side="left", padx=PAD * 2)

        self.banner = ttk.Label(self.root, background="#fff4ce", padding=PAD, wraplength=1250, justify="left")
        if notices:
            self.banner.configure(text="\n".join(notices))
            self.banner.pack(fill="x", padx=PAD, pady=(PAD, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.tabs = {}
        for key, title, cls in (
            ("catalog", _("Item catalog"), CatalogTab),
            ("delivery", _("Delivery Task list"), DeliveryTab),
            ("accepted", _("My Accepted Loot"), AcceptedTab),
            ("export", _("Copy & export"), ExportTab),
            ("install", _("Install to character"), InstallTab),
            ("sources", _("Data sources"), SourcesTab),
        ):
            tab = cls(self.notebook, self)
            self.notebook.add(tab, text=title)
            self.tabs[key] = tab
        self._dirty = set(self.tabs)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._refresh_visible())

        self.status = ttk.Label(self.root, padding=(PAD, 0, PAD, PAD), foreground="gray30")
        self.status.pack(fill="x")
        self.set_status(_("Data is stored locally in {path}").format(path=self.store.root))
        self._refresh_visible()

    # --- shared state -------------------------------------------------------------

    def characters(self) -> list[lootfile.CharacterFolder]:
        if self._characters is None:
            folder = self.library.state.characterdata_dir
            self._characters = lootfile.list_characters(Path(folder)) if folder else []
        return self._characters

    def format_report(self) -> lootfile.FormatReport:
        if self._format is None:
            self._format = lootfile.detect_format(self.characters())
        return self._format

    def changed(self) -> None:
        """Persist user edits and refresh every view (lazily for hidden tabs)."""
        self.store.save_state(self.library.state)
        self._characters = self._format = None
        self._dirty = set(self.tabs)
        self._refresh_visible()

    def _refresh_visible(self) -> None:
        current = self.notebook.nametowidget(self.notebook.select())
        for key, tab in self.tabs.items():
            if tab is current and key in self._dirty:
                self._dirty.discard(key)
                tab.refresh()

    def show_tab(self, key: str) -> None:
        self.notebook.select(self.tabs[key])

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    # --- updates ----------------------------------------------------------------------

    def check_for_updates(self) -> None:
        self.update_button.state(["disabled"])
        self.set_status(_("Checking for updates… (TibiaWiki is queried slowly, about one request per second)"))
        self.root.configure(cursor="watch")
        progress = {"text": None, "running": True}

        def show_progress():  # the worker only stores text; the Tk thread displays it
            if progress["running"]:
                if progress["text"]:
                    self.set_status(progress["text"])
                self.root.after(300, show_progress)

        show_progress()

        def finish():
            progress["running"] = False
            self.update_button.state(["!disabled"])
            self.root.configure(cursor="")

        def done(review):
            finish()

            def apply(keep_removed):
                apply_update(review, self.library, self.store, keep_removed)
                self.banner.pack_forget()
                failed = [r.label for r in review.results if not r.ok]
                self.set_status(_("Updates applied.") + (" " + _("Not updated: {s}.").format(s=", ".join(failed))
                                                         if failed else ""))
                self.changed()

            def cancel():
                record_failures(review, self.store)
                self.set_status(_("Update cancelled. Nothing was changed."))
                self._dirty.add("sources")
                self._refresh_visible()

            UpdateReviewDialog(self.root, review, apply, cancel)

        def failed(error):
            finish()
            messagebox.showerror(_("Update failed"), _("Could not check for updates: {error}\n\nCached data is "
                                                       "unchanged.").format(error=error), parent=self.root)
            self.set_status(_("Update failed. Using cached data."))

        run_in_background(self.root, lambda: check_for_updates(
            self.library, progress=lambda text: progress.__setitem__("text", text)), done, failed)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    App().run()
