"""The user's own edits and settings, stored separately from any source data.

Edits are stored as overrides on top of the source lists (additions and
exclusions), never as a copy of them. That way a data update can change the
source list without overwriting anything the user chose.
"""

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .storage import read_json, write_json_atomic

SCHEMA_VERSION = 1
SEARCH_FIELDS = ("name", "q", "seg", "cat", "idf")


def _valid_profile(pid, p) -> bool:
    return (isinstance(pid, str) and isinstance(p, dict) and isinstance(p.get("name"), str)
            and isinstance(p.get("follow_delivery", True), bool)
            and isinstance(p.get("extra", []), list) and all(isinstance(x, int) and not isinstance(x, bool)
                                                             for x in p.get("extra", []))
            and isinstance(p.get("excluded", []), list) and all(isinstance(x, str) for x in p.get("excluded", [])))


@dataclass
class UserState:
    # Delivery Task candidate list overrides
    delivery_added: list[int] = field(default_factory=list)  # client IDs the user added
    delivery_removed: list[str] = field(default_factory=list)  # entry keys the user excluded

    # Personal Accepted Loot list overrides
    accepted_follow_delivery: bool = True  # include the (edited) Delivery Task list
    accepted_extra: list[int] = field(default_factory=list)  # client IDs added directly
    accepted_excluded: list[str] = field(default_factory=list)  # entry keys removed by the user

    # Settings
    characterdata_dir: str | None = None
    character_labels: dict[str, str] = field(default_factory=dict)
    loot_list_limit: int | None = None  # no verified current client limit; user may set one
    theme: str = "dark"  # "dark" or "light"
    onboarded: bool = False  # first-run wizard completed

    # Named loot profiles (see profiles.py): {id: {"name", "created", "follow_delivery", "extra", "excluded"}}
    profiles: dict[str, dict] = field(default_factory=dict)
    active_profile: str = ""

    # Weekly Task tracker (see weekly.py)
    weekly: dict = field(default_factory=dict)  # {"week_start", "tasks": [{"key", "name", "required", "collected"}]}
    weekly_archive: list = field(default_factory=list)  # summaries of earlier weeks, newest first

    # Catalog conveniences, shared by all profiles
    favorites: list[str] = field(default_factory=list)  # catalog row keys ("3031" or "wiki:Title")
    saved_searches: list = field(default_factory=list)  # [{"name", "q", "seg", "cat", "idf"}]

    schema_version: int = SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> "UserState":
        return cls.from_dict(read_json(path, default={}) or {})

    @classmethod
    def from_dict(cls, data: dict) -> "UserState":
        """Build state from saved data, dropping unknown keys and values of the wrong type
        (a hand-edited or damaged file must not crash the app later on)."""
        defaults = cls()
        values = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            value, default = data[f.name], getattr(defaults, f.name)
            if f.name == "profiles":
                ok = isinstance(value, dict) and all(_valid_profile(k, v) for k, v in value.items())
            elif f.name == "weekly":
                from .weekly import valid_week
                ok = valid_week(value)
            elif f.name == "weekly_archive":
                ok = isinstance(value, list) and all(isinstance(x, dict) for x in value)
            elif f.name == "favorites":
                ok = isinstance(value, list) and all(isinstance(x, str) for x in value)
            elif f.name == "saved_searches":
                ok = isinstance(value, list) and all(
                    isinstance(x, dict) and all(isinstance(x.get(k), str) for k in SEARCH_FIELDS) for x in value)
            elif isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, list):
                ok = isinstance(value, list) and all(isinstance(x, (int, str)) and not isinstance(x, bool)
                                                     for x in value)
            elif isinstance(default, dict):
                ok = isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                     for k, v in value.items())
            elif f.name == "loot_list_limit":
                ok = value is None or (isinstance(value, int) and not isinstance(value, bool) and value > 0)
            elif isinstance(default, int):
                ok = isinstance(value, int) and not isinstance(value, bool)
            else:  # optional strings
                ok = value is None or isinstance(value, str)
            if ok:
                values[f.name] = value
        return cls(**values)

    def save(self, path: Path) -> None:
        write_json_atomic(path, asdict(self))
