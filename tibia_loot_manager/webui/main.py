"""Start the app: local server + Edge app window + native file dialogs.

The HTTP server runs on a background thread; file dialogs run on the request
thread that asks for them (native COM dialogs, no Tk, so the portable build's
embeddable Python works). The main thread only watches the window's lifetime:
the app exits when its window closes (the Edge process ends) or the page stops
pinging.
"""

import ctypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .. import paths
from .server import Server
from .service import AppService, Dialogs

# Seconds without a ping before the app exits. Edge runs a hidden window's timers about once a minute,
# so this must stay well above 60.
PING_TIMEOUT = 150
FIRST_PING_GRACE = 90  # the window may take a while to open on first launch
TITLE = "Tibia Loot List Manager"


def find_edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def message_box(text: str, error: bool = False) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 if error else 0x40)  # MB_ICONERROR / MB_ICONINFORMATION
    else:
        print(text, file=sys.stderr)


def acquire_single_instance_lock():
    """Hold an exclusive lock on a file in the app data folder for the app's lifetime.

    Two copies of the app would both save the same user_state.json and the
    later save would silently undo the other's edits. Returns the open file
    (keep it referenced) or None if another copy already holds the lock.
    """
    if sys.platform != "win32":
        return open(paths.app_data_dir() / "app.lock", "a+")
    import msvcrt
    handle = open(paths.app_data_dir() / "app.lock", "a+")
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def _dialogs() -> Dialogs:
    if sys.platform == "win32":
        from .native_dialogs import NativeDialogs
        return NativeDialogs()
    return Dialogs()


def _run() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    lock = acquire_single_instance_lock()
    if lock is None:
        message_box("The app is already running. Switch to its window.")
        return
    service = AppService(dialogs=_dialogs())

    pings = {"last": None}
    server = Server(service, on_ping=lambda: pings.__setitem__("last", time.monotonic()))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    started = time.monotonic()
    edge = find_edge()
    proc = None
    if edge:
        profile = paths.app_data_dir() / "window"
        try:
            proc = subprocess.Popen([edge, f"--app={server.url}", f"--user-data-dir={profile}", "--no-first-run",
                                     "--no-default-browser-check", "--disable-features=Translate",
                                     "--window-size=1320,860"])
        except OSError:
            proc = None
    if proc is None:
        webbrowser.open(server.url)

    # A window process still running after a few seconds owns the window, so its exit ends the
    # app. One that handed off to an already-running Edge exits at once; then pings decide.
    owner_known = owns_window = False
    try:
        while True:
            time.sleep(0.2)
            now = time.monotonic()
            if proc is not None:
                if not owner_known and now - started > 5:
                    owner_known, owns_window = True, proc.poll() is None
                if owns_window:
                    if proc.poll() is not None:
                        break
                    continue  # the window's process is the reliable signal; pings stall while it's minimized
            last = pings["last"]
            if (last is None and now - started > FIRST_PING_GRACE) or (last is not None and now - last > PING_TIMEOUT):
                break
    finally:
        server.shutdown()
        lock.close()


def _fatal(details: str) -> None:
    """Startup failed: there is no console, so log the error and tell the user in a native dialog."""
    log = None
    try:
        log = paths.app_data_dir() / "crash.log"
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{details}\n")
    except OSError:
        pass
    last = details.strip().splitlines()[-1] if details.strip() else "Unknown error"
    text = f"{TITLE} could not start.\n\n{last}"
    if log:
        text += f"\n\nDetails were saved to:\n{log}"
    message_box(text, error=True)


def main() -> None:
    try:
        _run()
    except Exception:
        import traceback
        _fatal(traceback.format_exc())
