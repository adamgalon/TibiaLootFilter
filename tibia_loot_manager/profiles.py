"""Named loot profiles: separate Accepted Loot lists, with history and import/export.

A profile holds the user's Accepted Loot overrides only (whether it includes
the Delivery Task list, items added, items removed); the Delivery Task list
edits are shared by every profile. The active profile's overrides live in the
``accepted_*`` fields of ``UserState`` (the working copy everything else
uses); switching profiles swaps them.
"""

import re
import secrets
from pathlib import Path

from .i18n import _
from .state import UserState
from .storage import read_json_checked, utc_now_iso, write_json_atomic

EXPORT_FORMAT = "tibia-loot-profile"
SKIP_KEYS = ("skip_recommended", "skip_limit", "skip_items", "skip_extra", "skip_excluded")
EXPORT_VERSION = 1
HISTORY_LIMIT = 100
NAME_LIMIT = 40


class ProfileError(Exception):
    pass


def snapshot(state: UserState) -> dict:
    return {"follow_delivery": state.accepted_follow_delivery, "extra": list(state.accepted_extra),
            "excluded": list(state.accepted_excluded), "preset": state.accepted_preset,
            "preset_items": list(state.accepted_preset_items),
            "skip_recommended": state.skipped_recommended, "skip_limit": state.skipped_limit,
            "skip_items": list(state.skipped_items), "skip_extra": list(state.skipped_extra),
            "skip_excluded": list(state.skipped_excluded)}


def _ints(values) -> list[int]:
    return [int(x) for x in values or [] if isinstance(x, int) and not isinstance(x, bool)]


def apply(state: UserState, snap: dict) -> None:
    state.accepted_follow_delivery = bool(snap.get("follow_delivery", True))
    state.accepted_extra = [int(x) for x in snap.get("extra", []) if isinstance(x, int) and not isinstance(x, bool)]
    state.accepted_excluded = [str(x) for x in snap.get("excluded", []) if isinstance(x, str)]
    state.accepted_preset = snap.get("preset") if isinstance(snap.get("preset"), str) else ""
    state.accepted_preset_items = [int(x) for x in snap.get("preset_items", [])
                                   if isinstance(x, int) and not isinstance(x, bool)]
    state.skipped_recommended = snap.get("skip_recommended") is True
    limit = snap.get("skip_limit", 100)
    state.skipped_limit = limit if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else 100
    state.skipped_items = _ints(snap.get("skip_items"))
    state.skipped_extra = _ints(snap.get("skip_extra"))
    state.skipped_excluded = _ints(snap.get("skip_excluded"))


def ensure(state: UserState) -> bool:
    """Give an older state (no profiles yet) a "Main" profile holding its current list."""
    if state.profiles and state.active_profile in state.profiles:
        return False
    if not state.profiles:
        state.profiles = {"main": {"name": _("Main"), "created": utc_now_iso(), **snapshot(state)}}
    state.active_profile = next(iter(state.profiles))
    apply(state, state.profiles[state.active_profile])
    return True


def sync_active(state: UserState) -> None:
    """Copy the working list into the active profile (done before every save)."""
    if state.active_profile in state.profiles:
        state.profiles[state.active_profile].update(snapshot(state))


def clean_name(name: str, state: UserState, exclude: str | None = None) -> str:
    name = " ".join((name or "").split())[:NAME_LIMIT]
    if not name:
        raise ProfileError(_("Give the profile a name."))
    taken = {p["name"].lower() for pid, p in state.profiles.items() if pid != exclude}
    base, n = name, 2
    while name.lower() in taken:
        suffix = f" ({n})"
        name = base[:NAME_LIMIT - len(suffix)] + suffix
        n += 1
    return name


def _new_id(state: UserState, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or "profile"
    while True:
        pid = f"{slug}-{secrets.token_hex(3)}"
        if pid not in state.profiles:
            return pid


def create(state: UserState, name: str, snap: dict) -> str:
    sync_active(state)
    pid = _new_id(state, name)
    state.profiles[pid] = {"name": clean_name(name, state), "created": utc_now_iso(),
                           "follow_delivery": bool(snap.get("follow_delivery", True)),
                           "extra": list(snap.get("extra", [])), "excluded": list(snap.get("excluded", [])),
                           "preset": snap.get("preset", ""), "preset_items": list(snap.get("preset_items", [])),
                           **{k: snap[k] for k in SKIP_KEYS if k in snap}}
    return pid


def switch(state: UserState, pid: str) -> None:
    if pid not in state.profiles:
        raise ProfileError(_("That profile no longer exists."))
    sync_active(state)
    state.active_profile = pid
    apply(state, state.profiles[pid])


def rename(state: UserState, pid: str, name: str) -> None:
    if pid not in state.profiles:
        raise ProfileError(_("That profile no longer exists."))
    state.profiles[pid]["name"] = clean_name(name, state, exclude=pid)


def delete(state: UserState, pid: str) -> None:
    if pid not in state.profiles:
        raise ProfileError(_("That profile no longer exists."))
    if len(state.profiles) == 1:
        raise ProfileError(_("You can't delete your only profile."))
    if pid == state.active_profile:
        switch(state, next(p for p in state.profiles if p != pid))
    del state.profiles[pid]


# --- import / export ---------------------------------------------------------------------

def export_data(state: UserState, pid: str, app_version: str) -> dict:
    sync_active(state)
    p = state.profiles[pid]
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "app_version": app_version,
            "exported_at": utc_now_iso(), "name": p["name"], "follow_delivery": p["follow_delivery"],
            "extra": p["extra"], "excluded": p["excluded"], "preset": p.get("preset", ""),
            "preset_items": p.get("preset_items", []), **{k: p[k] for k in SKIP_KEYS if k in p}}


def parse_import(data) -> tuple[str, dict]:
    """Validate an exported profile file; returns (name, snapshot)."""
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise ProfileError(_("This isn't a Tibia Loot List Manager profile file."))
    if not isinstance(data.get("version"), int) or data["version"] > EXPORT_VERSION:
        raise ProfileError(_("This profile was made by a newer version of the app."))
    extra, excluded = data.get("extra", []), data.get("excluded", [])
    if not (isinstance(extra, list) and all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in extra)):
        raise ProfileError(_("The profile's item list is damaged."))
    if not (isinstance(excluded, list) and all(isinstance(x, str) for x in excluded)):
        raise ProfileError(_("The profile's removed-items list is damaged."))
    preset, preset_items = data.get("preset", ""), data.get("preset_items", [])
    if not (isinstance(preset, str) and isinstance(preset_items, list)
            and all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in preset_items)):
        raise ProfileError(_("The profile's strictness level is damaged."))
    skip = {k: data[k] for k in SKIP_KEYS if k in data}
    for k in ("skip_items", "skip_extra", "skip_excluded"):
        if k in skip and not (isinstance(skip[k], list)
                              and all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in skip[k])):
            raise ProfileError(_("The profile's Skipped Loot list is damaged."))
    if not isinstance(skip.get("skip_recommended", False), bool) or not (
            isinstance(skip.get("skip_limit", 100), int) and skip.get("skip_limit", 100) > 0):
        raise ProfileError(_("The profile's Skipped Loot settings are damaged."))
    name = data.get("name") if isinstance(data.get("name"), str) else _("Imported profile")
    return name, {"follow_delivery": bool(data.get("follow_delivery", True)), "extra": extra, "excluded": excluded,
                  "preset": preset, "preset_items": preset_items, **skip}


# --- history -------------------------------------------------------------------------------

class History:
    """Per-profile list of snapshots, newest last, kept in the app data folder."""

    def __init__(self, root: Path):
        self.dir = Path(root) / "history"

    def _path(self, pid: str) -> Path:
        return self.dir / f"{re.sub(r'[^A-Za-z0-9_-]', '_', pid)}.json"

    def entries(self, pid: str) -> list[dict]:
        data, _moved = read_json_checked(self._path(pid), [], valid=lambda d: isinstance(d, list))
        return [e for e in data if isinstance(e, dict) and "snapshot" in e]

    def record(self, pid: str, description: str, snap: dict, count: int | None = None) -> None:
        entries = self.entries(pid)
        if entries and entries[-1]["snapshot"] == snap:
            return  # nothing changed
        entries.append({"at": utc_now_iso(), "description": description, "snapshot": snap, "count": count})
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self._path(pid), entries[-HISTORY_LIMIT:], indent=None)

    def forget(self, pid: str) -> None:
        try:
            self._path(pid).unlink()
        except FileNotFoundError:
            pass

