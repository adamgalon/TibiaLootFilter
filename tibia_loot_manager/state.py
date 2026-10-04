"""The user's own edits and settings, stored separately from any source data.

Edits are stored as overrides on top of the source lists (additions and
exclusions), never as a copy of them. That way a data update can change the
source list without overwriting anything the user chose.
"""

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .storage import read_json, write_json_atomic

SCHEMA_VERSION = 1


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

    schema_version: int = SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> "UserState":
        data = read_json(path, default={}) or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        write_json_atomic(path, asdict(self))
