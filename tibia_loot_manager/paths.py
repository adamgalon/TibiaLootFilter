"""Well-known filesystem locations."""

import os
from pathlib import Path

APP_DIR_NAME = "TibiaLootManager"


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def app_data_dir() -> Path:
    """Where this app keeps user edits, cached source data and backups."""
    override = os.environ.get("TIBIA_LOOT_MANAGER_HOME")
    path = Path(override) if override else _local_appdata() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = app_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backups_dir() -> Path:
    path = app_data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_characterdata_dir() -> Path:
    """The commonly reported location. A starting point only — never assumed to exist."""
    return _local_appdata() / "Tibia" / "packages" / "Tibia" / "characterdata"


def client_package_dir(characterdata_dir: Path) -> Path:
    """The client package folder (holding ``assets`` and ``package.json``) next to characterdata."""
    return Path(characterdata_dir).parent


def bundled_data_dir() -> Path:
    return Path(__file__).parent / "data"
