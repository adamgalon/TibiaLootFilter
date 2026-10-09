"""Detect whether the Tibia client is running. Read-only: never touches the process."""

import ctypes
import sys
from ctypes import wintypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_CLIENT_NAMES = ("\\client.exe", "\\client_launcher.exe", "\\tibia.exe")

RUNNING, CLOSED, UNKNOWN = "running", "closed", "unknown"

_api = None


def _windows_api():
    """Load the API once, with full signatures: HANDLE is pointer-sized on 64-bit Windows and
    ctypes would otherwise assume a 32-bit int return value and could truncate it."""
    global _api
    if _api is None:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        psapi.EnumProcesses.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        _api = (psapi, kernel32)
    return _api


def is_tibia_path(path: str) -> bool:
    lower = path.lower()
    return "\\tibia\\" in lower and lower.endswith(_CLIENT_NAMES)


def running_tibia_paths() -> list[str]:
    """Full paths of running processes that look like the Tibia client. Raises OSError if the
    process list can't be read."""
    if sys.platform != "win32":
        return []
    psapi, kernel32 = _windows_api()
    capacity = 4096
    while True:  # grow until the list fits; a full buffer may have been cut short
        pids = (wintypes.DWORD * capacity)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        if needed.value < ctypes.sizeof(pids):
            break
        capacity *= 2
    found = []
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue  # system or protected process: not the game client
        try:
            buf = ctypes.create_unicode_buffer(1024)
            length = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(length)) and is_tibia_path(buf.value):
                found.append(buf.value)
        finally:
            kernel32.CloseHandle(handle)
    return found


def tibia_status() -> str:
    """RUNNING, CLOSED, or UNKNOWN when the check itself failed (never silently 'closed')."""
    try:
        return RUNNING if running_tibia_paths() else CLOSED
    except OSError:
        return UNKNOWN
