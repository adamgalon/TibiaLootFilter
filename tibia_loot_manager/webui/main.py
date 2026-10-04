"""Start the app: local server + Edge app window + native file dialogs.

The main thread runs a hidden Tk root so native Windows file dialogs can be
shown on request; the HTTP server runs on a background thread. The app exits
when its window closes (the Edge process ends) or the page stops pinging.
"""

import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog

from .. import paths
from .server import Server
from .service import AppService, Dialogs

PING_TIMEOUT = 25  # seconds without a ping from the page before the app exits
FIRST_PING_GRACE = 90  # the window may take a while to open on first launch


def find_edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return str(candidate)
    return None


class TkDialogs(Dialogs):
    """Runs file dialogs on the Tk (main) thread on behalf of server threads."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.requests: queue.Queue = queue.Queue()

    def _run(self, fn):
        done, box = threading.Event(), {}
        self.requests.put((fn, box, done))
        done.wait()
        return box.get("result")

    def pump(self) -> None:
        while True:
            try:
                fn, box, done = self.requests.get_nowait()
            except queue.Empty:
                return
            try:
                self.root.attributes("-topmost", True)
                box["result"] = fn() or None
            finally:
                self.root.attributes("-topmost", False)
                done.set()

    def pick_folder(self, initial):
        start = initial if initial and os.path.isdir(initial) else None
        return self._run(lambda: filedialog.askdirectory(parent=self.root, initialdir=start,
                                                         title="Choose the Tibia characterdata folder"))

    def save_file(self, initial_name, extension, kinds, initial_dir=None):
        return self._run(lambda: filedialog.asksaveasfilename(parent=self.root, initialfile=initial_name,
                                                              defaultextension=extension, filetypes=kinds,
                                                              initialdir=initial_dir))


def main() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    root.withdraw()
    dialogs = TkDialogs(root)
    service = AppService(dialogs=dialogs)

    pings = {"last": None}
    server = Server(service, on_ping=lambda: pings.__setitem__("last", time.monotonic()))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    started = time.monotonic()
    edge = find_edge()
    proc = None
    if edge:
        profile = paths.app_data_dir() / "window"
        proc = subprocess.Popen([edge, f"--app={server.url}", f"--user-data-dir={profile}", "--no-first-run",
                                 "--no-default-browser-check", "--disable-features=Translate",
                                 "--window-size=1320,860"])
    else:
        webbrowser.open(server.url)

    owner = {"known": False, "owns": False}

    def tick():
        dialogs.pump()
        now = time.monotonic()
        # A window process still running after a few seconds owns the window, so its exit ends
        # the app. One that handed off to an already-running Edge exits at once; then pings decide.
        if proc is not None:
            if not owner["known"] and now - started > 5:
                owner["known"], owner["owns"] = True, proc.poll() is None
            if owner["owns"] and proc.poll() is not None:
                root.quit()
                return
        last = pings["last"]
        if (last is None and now - started > FIRST_PING_GRACE) or (last is not None and now - last > PING_TIMEOUT):
            root.quit()
            return
        root.after(100, tick)

    root.after(100, tick)
    try:
        root.mainloop()
    finally:
        server.shutdown()
        root.destroy()
