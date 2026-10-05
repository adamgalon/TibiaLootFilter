"""Build the portable Windows version: unzip and double-click, no Python install needed.

    python tools/build_portable.py

Produces ``dist/TibiaLootManager-<version>/`` and a matching ``.zip``:

    Start Tibia Loot List Manager.cmd   double-click to run
    python/                             Python's official "embeddable" runtime (same version as the builder)
    app/tibia_loot_manager/             the app

The runtime is downloaded from python.org and checked against the SHA-256 that
python.org publishes for it. Nothing else is downloaded or installed.
"""

import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tibia_loot_manager import __version__  # noqa: E402

PY = ".".join(map(str, sys.version_info[:3]))
EMBED_NAME = f"python-{PY}-embed-amd64.zip"
FILES_API = "https://www.python.org/api/v2/downloads/release_file/?os=1"
CACHE = ROOT / "build" / "cache"
LAUNCHER = '@echo off\r\nstart "" "%~dp0python\\pythonw.exe" -m tibia_loot_manager\r\n'
README = f"""Tibia Loot List Manager {__version__} (portable)
==========================================

Run: double-click "Start Tibia Loot List Manager.cmd".
The app opens in its own window (Microsoft Edge, built into Windows 10/11).
Your lists, cache and backups are stored in %LOCALAPPDATA%\\TibiaLootManager,
so you can replace this folder with a newer version at any time.

Contents
- python\\  Python {PY} embeddable runtime from python.org (PSF License, see python\\LICENSE.txt)
- app\\     the app. Bundled fonts and icons: Inter (SIL OFL 1.1) and Phosphor Icons (MIT),
           licenses in app\\tibia_loot_manager\\web\\vendor\\
"""


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
    (out / "README.txt").write_text(README, encoding="utf-8")

    archive = shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
    print(f"Built {out}")
    print(f"Zip   {archive} ({Path(archive).stat().st_size / 1e6:.1f} MB)")
    return out


if __name__ == "__main__":
    build()
