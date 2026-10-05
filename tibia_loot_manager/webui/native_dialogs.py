"""Native Windows file dialogs (the modern Explorer dialogs) through COM, using only ctypes.

No Tk: the portable build ships Python's embeddable runtime, which has no
tkinter. IFileOpenDialog / IFileSaveDialog are called through their vtables.
Each dialog runs on the calling thread with its own COM apartment; a lock
keeps at most one dialog open at a time.
"""

import ctypes
import sys
import threading
from ctypes import wintypes

from .service import Dialogs

_lock = threading.Lock()

if sys.platform == "win32":
    _ole32 = ctypes.WinDLL("ole32")
    _shell32 = ctypes.WinDLL("shell32")
    _user32 = ctypes.WinDLL("user32")
    _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _ole32.CoInitializeEx.restype = ctypes.HRESULT
    _ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p)]
    _ole32.CoCreateInstance.restype = ctypes.HRESULT
    _ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    _shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                                     ctypes.POINTER(ctypes.c_void_p)]
    _shell32.SHCreateItemFromParsingName.restype = ctypes.HRESULT
    _user32.GetForegroundWindow.restype = wintypes.HWND


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        h = text.strip("{}").replace("-", "")
        self.Data1, self.Data2, self.Data3 = int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16)
        for i in range(8):
            self.Data4[i] = int(h[16 + 2 * i:18 + 2 * i], 16)


class FILTERSPEC(ctypes.Structure):
    _fields_ = [("pszName", wintypes.LPCWSTR), ("pszSpec", wintypes.LPCWSTR)]


CLSID_FileOpenDialog = "{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"
IID_IFileOpenDialog = "{D57C7288-D4AD-4768-BE02-9D969532D960}"
CLSID_FileSaveDialog = "{C0B4E2F3-BA21-4773-8DBA-335EC946EB8B}"
IID_IFileSaveDialog = "{84BCCD23-5FDE-4CDB-AEA4-AF64B83D78AB}"
IID_IShellItem = "{43826D1E-E718-42EE-BC55-A1E261C37BFE}"

CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED = 2
FOS_OVERWRITEPROMPT, FOS_NOCHANGEDIR, FOS_PICKFOLDERS, FOS_FORCEFILESYSTEM = 0x2, 0x8, 0x20, 0x40
SIGDN_FILESYSPATH = 0x80058000
ERROR_CANCELLED_HRESULT = 0x800704C7 - (1 << 32)

# vtable slots (IUnknown 0-2, IModalWindow 3, IFileDialog 4-26)
_RELEASE, _SHOW, _SET_FILE_TYPES, _SET_OPTIONS, _GET_OPTIONS, _SET_FOLDER = 2, 3, 4, 9, 10, 12
_SET_FILE_NAME, _SET_TITLE, _GET_RESULT, _SET_DEFAULT_EXTENSION = 15, 17, 20, 22
_ITEM_GET_DISPLAY_NAME = 5  # IShellItem


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    fn = prototype(vtable[index])
    return lambda *args: fn(obj, *args)


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(
            ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[_RELEASE])(obj)


def _shell_item(path: str):
    item = ctypes.c_void_p()
    try:
        _shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(GUID(IID_IShellItem)), ctypes.byref(item))
    except OSError:
        return None
    return item


def _show(clsid: str, iid: str, options: int, title: str, folder: str | None = None, file_name: str | None = None,
          extension: str | None = None, kinds: list[tuple[str, str]] | None = None) -> str | None:
    """Show one file dialog; returns the chosen path or None if cancelled."""
    try:
        _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    except OSError:
        pass  # already initialised on this thread in another mode: the dialog still works
    dialog = ctypes.c_void_p()
    _ole32.CoCreateInstance(ctypes.byref(GUID(clsid)), None, CLSCTX_INPROC_SERVER, ctypes.byref(GUID(iid)),
                            ctypes.byref(dialog))
    result = ctypes.c_void_p()
    try:
        current = wintypes.DWORD()
        _method(dialog, _GET_OPTIONS, ctypes.POINTER(wintypes.DWORD))(ctypes.byref(current))
        _method(dialog, _SET_OPTIONS, wintypes.DWORD)(current.value | options | FOS_FORCEFILESYSTEM | FOS_NOCHANGEDIR)
        _method(dialog, _SET_TITLE, wintypes.LPCWSTR)(title)
        if kinds:
            specs = (FILTERSPEC * len(kinds))(*[FILTERSPEC(name, spec) for name, spec in kinds])
            _method(dialog, _SET_FILE_TYPES, wintypes.UINT, ctypes.POINTER(FILTERSPEC))(len(kinds), specs)
        if extension:
            _method(dialog, _SET_DEFAULT_EXTENSION, wintypes.LPCWSTR)(extension.lstrip("."))
        if file_name:
            _method(dialog, _SET_FILE_NAME, wintypes.LPCWSTR)(file_name)
        if folder:
            item = _shell_item(folder)
            if item:
                try:
                    _method(dialog, _SET_FOLDER, ctypes.c_void_p)(item)
                finally:
                    _release(item)
        try:
            _method(dialog, _SHOW, wintypes.HWND)(_user32.GetForegroundWindow())
        except OSError as e:
            if e.winerror == ERROR_CANCELLED_HRESULT or getattr(e, "errno", None) == ERROR_CANCELLED_HRESULT:
                return None
            raise
        _method(dialog, _GET_RESULT, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(result))
        name = ctypes.c_wchar_p()
        _method(result, _ITEM_GET_DISPLAY_NAME, wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p))(
            SIGDN_FILESYSPATH, ctypes.byref(name))
        try:
            return name.value
        finally:
            _ole32.CoTaskMemFree(name)
    finally:
        _release(result)
        _release(dialog)


class NativeDialogs(Dialogs):
    def pick_folder(self, initial):
        with _lock:
            return _show(CLSID_FileOpenDialog, IID_IFileOpenDialog, FOS_PICKFOLDERS,
                         "Choose the Tibia characterdata folder", folder=initial)

    def open_file(self, kinds, initial_dir=None):
        with _lock:
            return _show(CLSID_FileOpenDialog, IID_IFileOpenDialog, 0, "Open", folder=initial_dir, kinds=kinds)

    def save_file(self, initial_name, extension, kinds, initial_dir=None):
        with _lock:
            return _show(CLSID_FileSaveDialog, IID_IFileSaveDialog, FOS_OVERWRITEPROMPT, "Save as",
                         folder=initial_dir, file_name=initial_name, extension=extension, kinds=kinds)
