"""Detect whether the Tibia client is running. Read-only: never touches the process."""

import ctypes
import sys
from ctypes import wintypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def running_tibia_paths() -> list[str]:
    """Full paths of running processes that look like the Tibia client."""
    if sys.platform != "win32":
        return []
    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    pids = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    found = []
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                path = buf.value
                lower = path.lower()
                if "\\tibia\\" in lower and lower.endswith(("\\client.exe", "\\client_launcher.exe", "\\tibia.exe")):
                    found.append(path)
        finally:
            kernel32.CloseHandle(handle)
    return found


def is_tibia_running() -> bool:
    try:
        return bool(running_tibia_paths())
    except OSError:
        return False
