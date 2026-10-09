"""Build the portable Windows version: unzip and double-click, no Python install needed.

    python tools/build_portable.py

Produces ``dist/TibiaLootManager-<version>/`` and a matching ``.zip``:

    Start Tibia Loot List Manager.cmd   double-click to run
    Create desktop shortcut.cmd         adds a shortcut (with icon, no console window) to the desktop and Start menu
    TibiaLootManager.ico                the app icon
    python/                             Python's official "embeddable" runtime (same version as the builder)
    app/tibia_loot_manager/             the app

The runtime is downloaded from python.org and checked against the SHA-256 that
python.org publishes for it. Nothing else is downloaded or installed.
"""

import hashlib
import json
import math
import shutil
import struct
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tibia_loot_manager import __version__  # noqa: E402

PY = ".".join(map(str, sys.version_info[:3]))
EMBED_NAME = f"python-{PY}-embed-amd64.zip"
FILES_API = "https://www.python.org/api/v2/downloads/release_file/?os=1"
CACHE = ROOT / "build" / "cache"
LAUNCHER = '@echo off\r\nstart "" "%~dp0python\\pythonw.exe" -m tibia_loot_manager\r\n'
# "%~dp0." rather than "%~dp0": a trailing backslash before the closing quote would escape the quote.
SHORTCUT = ('@echo off\r\n'
            'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\\create_shortcut.ps1" -Root "%~dp0."\r\n'
            'if errorlevel 1 (echo. & echo Could not create the shortcuts. & pause & exit /b 1)\r\n'
            'echo. & echo Done. Start the app from the "Tibia Loot List Manager" icon on your desktop.\r\n'
            'timeout /t 5 >nul\r\n')
README = f"""Tibia Loot List Manager {__version__} (portable)
==========================================

Setup on a new computer
1. If you downloaded the zip: right-click it, choose Properties, tick "Unblock", OK.
   (Otherwise Windows may warn about every file inside.)
2. Extract the zip to a folder that will stay put, for example Documents\\TibiaLootManager.
3. Double-click "Create desktop shortcut.cmd". It adds a "Tibia Loot List Manager" icon
   to your desktop and Start menu. If you move the folder later, run it again.
4. Start the app from that icon (or double-click "Start Tibia Loot List Manager.cmd").

The app opens in its own window (Microsoft Edge, built into Windows 10/11). It looks for
Tibia in its usual place; if Tibia is installed elsewhere, choose the folder on first start.
Your lists, cache and backups are stored in %LOCALAPPDATA%\\TibiaLootManager, not in this
folder, so you can replace this folder with a newer version at any time.

Contents
- python\\  Python {PY} embeddable runtime from python.org (PSF License, see python\\LICENSE.txt)
- app\\     the app. Bundled fonts and icons: Inter (SIL OFL 1.1) and Phosphor Icons (MIT),
           licenses in app\\tibia_loot_manager\\web\\vendor\\
"""


def _png(size: int, pixels: bytes) -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + pixels[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def _icon_image(size: int) -> bytes:
    """The app mark from web/favicon.svg (a 32-unit canvas): a dark rounded square holding a violet diamond
    outline. Drawn with 4x4 supersampling so small sizes stay smooth. Returns RGBA rows."""
    bg, fg, n = (0x16, 0x18, 0x26), (0x91, 0x84, 0xD9), 4
    scale = 32 / size
    out = bytearray()
    for py in range(size):
        for px in range(size):
            cover = ink = 0
            for sy in range(n):
                for sx in range(n):
                    x, y = (px + (sx + .5) / n) * scale, (py + (sy + .5) / n) * scale
                    cx, cy = min(max(x, 7), 25), min(max(y, 7), 25)  # rounded square, corner radius 7
                    if (x - cx) ** 2 + (y - cy) ** 2 > 49:
                        continue
                    cover += 1
                    # a 12x12 square rotated 45 degrees around (16, 16), stroke width 2.5
                    u, v = (x + y - 32) / math.sqrt(2), (y - x) / math.sqrt(2)
                    if abs(max(abs(u), abs(v)) - 6) <= 1.25:
                        ink += 1
            if not cover:
                out += b"\x00\x00\x00\x00"
                continue
            t = ink / cover
            out += bytes(round(b + (f - b) * t) for b, f in zip(bg, fg)) + bytes([round(255 * cover / n / n)])
    return bytes(out)


def make_icon(path: Path) -> None:
    """A Windows .ico holding PNG images at the sizes Explorer and the taskbar use."""
    images = [(s, _png(s, _icon_image(s))) for s in (16, 24, 32, 48, 64, 256)]
    offset, entries, data = 6 + 16 * len(images), b"", b""
    for s, png in images:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + data)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_runtime() -> Path:
    """Download the embeddable runtime once and verify it against python.org's published hash."""
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / EMBED_NAME
    with urllib.request.urlopen(FILES_API, timeout=60) as r:
        files = json.load(r)
    entry = next((f for f in files if f.get("url", "").endswith("/" + EMBED_NAME)), None)
    if not entry or not entry.get("sha256_sum"):
        raise SystemExit(f"python.org lists no checksum for {EMBED_NAME}; refusing to build.")
    expected = entry["sha256_sum"].lower()
    if not target.is_file() or sha256(target) != expected:
        print(f"Downloading {entry['url']}")
        with urllib.request.urlopen(entry["url"], timeout=300) as r, open(target, "wb") as f:
            shutil.copyfileobj(r, f)
    actual = sha256(target)
    if actual != expected:
        target.unlink()
        raise SystemExit(f"Checksum mismatch for {EMBED_NAME}: expected {expected}, got {actual}")
    print(f"Runtime verified: {EMBED_NAME} sha256 {actual}")
    return target


def build() -> Path:
    runtime = fetch_runtime()
    out = ROOT / "dist" / f"TibiaLootManager-{__version__}"
    if out.exists():
        shutil.rmtree(out)
    (out / "python").mkdir(parents=True)
    with zipfile.ZipFile(runtime) as z:
        z.extractall(out / "python")

    # The ._pth file is the embeddable runtime's whole search path: add the app folder to it.
    pth = next((out / "python").glob("python3*._pth"))
    lines = pth.read_text(encoding="utf-8").splitlines()
    pth.write_text("\n".join(lines[:1] + ["..\\app"] + lines[1:]) + "\n", encoding="utf-8")

    shutil.copytree(ROOT / "tibia_loot_manager", out / "app" / "tibia_loot_manager",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (out / "Start Tibia Loot List Manager.cmd").write_text(LAUNCHER, encoding="ascii", newline="")
    (out / "Create desktop shortcut.cmd").write_text(SHORTCUT, encoding="ascii", newline="")
    shutil.copy2(ROOT / "tools" / "create_shortcut.ps1", out / "app" / "create_shortcut.ps1")
    make_icon(out / "TibiaLootManager.ico")
    (out / "README.txt").write_text(README, encoding="utf-8")

    archive = shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
    print(f"Built {out}")
    print(f"Zip   {archive} ({Path(archive).stat().st_size / 1e6:.1f} MB)")
    return out


if __name__ == "__main__":
    build()
