"""Small JSON persistence helpers."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def quarantine(path: Path) -> Path:
    """Move a damaged file aside (kept for diagnosis) and return its new path."""
    path = Path(path)
    target = path.with_name(f"{path.name}.damaged-{datetime.now():%Y%m%d-%H%M%S}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.name}.damaged-{datetime.now():%Y%m%d-%H%M%S}-{n}")
        n += 1
    os.replace(path, target)
    return target


def read_json_checked(path: Path, default: Any = None, valid=None) -> tuple[Any, Path | None]:
    """Like read_json, but a file that isn't valid JSON (or fails ``valid``) is quarantined
    and ``default`` returned, together with where the damaged file was moved."""
    try:
        data = read_json(path, default)
    except (ValueError, UnicodeDecodeError):
        return default, quarantine(path)
    if data is not default and valid is not None and not valid(data):
        return default, quarantine(path)
    return data, None


def write_json_atomic(path: Path, data: Any, indent: int | None = 2) -> None:
    """Write JSON so that a crash never leaves a half-written file behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
