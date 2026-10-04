"""Shared Tk helpers."""

import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import ttk

from ..i18n import _

PAD = 6


def fmt_gold(amount: int | None) -> str:
    return "—" if amount is None else f"{amount:,}"


def fmt_date(iso: str | None) -> str:
    """Show an ISO timestamp as a local date and time."""
    if not iso:
        return _("never")
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


class Tree(ttk.Frame):
    """A Treeview with scrollbars, column definitions and click-to-sort headings."""

    def __init__(self, parent, columns: list[tuple[str, str, int]], selectmode="extended", height=15):
        super().__init__(parent)
        ids = [c[0] for c in columns]
        self.tree = ttk.Treeview(self, columns=ids, show="headings", selectmode=selectmode, height=height)
        for cid, title, width in columns:
            self.tree.heading(cid, text=title, command=lambda c=cid: self.sort_by(c))
            self.tree.column(cid, width=width, stretch=width >= 150, anchor="w")
        ys = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._sort = (None, False)
        self.tree.tag_configure("muted", foreground="gray45")
        self.tree.tag_configure("warn", foreground="#a33")

    def sort_by(self, column: str) -> None:
        reverse = self._sort == (column, False)
        rows = [(self.tree.set(k, column), k) for k in self.tree.get_children("")]

        def key(row):
            text = row[0].replace(",", "")
            return (0, int(text), "") if text.isdigit() else (1, 0, text.lower())

        rows.sort(key=key, reverse=reverse)
        for i, (_v, k) in enumerate(rows):
            self.tree.move(k, "", i)
        self._sort = (column, reverse)

    def replace(self, rows: list[tuple[str, tuple, tuple]]) -> None:
        """rows: (iid, values, tags). Keeps the selection where possible."""
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children(""))
        for iid, values, tags in rows:
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)
        keep = [i for i in selected if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        column, reverse = self._sort
        if column:
            self._sort = (column, not reverse)
            self.sort_by(column)

    def selection(self) -> list[str]:
        return list(self.tree.selection())


class InfoText(tk.Text):
    """Read-only rich text with headings and clickable links."""

    def __init__(self, parent, **kw):
        kw.setdefault("wrap", "word")
        kw.setdefault("relief", "flat")
        kw.setdefault("padx", PAD)
        kw.setdefault("pady", PAD)
        super().__init__(parent, **kw)
        base = ttk.Style().lookup("TLabel", "font") or "TkDefaultFont"
        self.configure(font=base, background=ttk.Style().lookup("TFrame", "background") or "SystemButtonFace")
        self.tag_configure("h1", font=("Segoe UI", 12, "bold"), spacing1=2, spacing3=4)
        self.tag_configure("h2", font=("Segoe UI", 10, "bold"), spacing1=8, spacing3=2)
        self.tag_configure("muted", foreground="gray40")
        self.tag_configure("warn", foreground="#a33")
        self.tag_configure("ok", foreground="#1a7a2e")
        self._links = 0  # stays editable until done() so callers can fill it without clear()

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self._links = 0

    def done(self) -> None:
        self.configure(state="disabled")

    def add(self, text: str, *tags: str) -> None:
        self.insert("end", text, tags)

    def link(self, text: str, url: str) -> None:
        self._links += 1
        tag = f"link{self._links}"
        self.tag_configure(tag, foreground="#0a58ca", underline=True)
        self.tag_bind(tag, "<Button-1>", lambda _e: webbrowser.open(url))
        self.tag_bind(tag, "<Enter>", lambda _e: self.configure(cursor="hand2"))
        self.tag_bind(tag, "<Leave>", lambda _e: self.configure(cursor=""))
        self.insert("end", text, (tag,))


def link_label(parent, text: str, url: str) -> ttk.Label:
    label = ttk.Label(parent, text=text, foreground="#0a58ca", cursor="hand2")
    label.bind("<Button-1>", lambda _e: webbrowser.open(url))
    return label


def run_in_background(widget: tk.Misc, work, on_done, on_error) -> None:
    """Run ``work()`` on a thread and deliver its result on the Tk thread."""
    box: dict = {}

    def target():
        try:
            box["result"] = work()
        except Exception as e:  # reported to the user via on_error
            box["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    def poll():
        if thread.is_alive():
            widget.after(150, poll)
        elif "error" in box:
            on_error(box["error"])
        else:
            on_done(box.get("result"))

    widget.after(150, poll)
