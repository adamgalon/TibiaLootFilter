"""Local caches, the source log, and first-run bootstrap."""

import shutil
from pathlib import Path

from . import paths
from .library import Library
from .sources import tibia_client
from .state import UserState
from .storage import read_json, read_json_checked, utc_now_iso, write_json_atomic

SEED_DELIVERY = "seed_delivery.json"
SEED_WIKI_INDEX = "seed_wiki_index.json"


class DataStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else paths.app_data_dir()
        self.cache = self.root / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "user_state.json"
        self.catalog_path = self.cache / "catalog.json"
        self.delivery_path = self.cache / "delivery.json"
        self.lookups_path = self.cache / "wiki_lookups.json"
        self.wiki_index_path = self.cache / "wiki_index.json"
        self.source_log_path = self.root / "source_log.json"
        self.backups = self.root / "backups"
        self.state_backup_path = self.root / "user_state.json.bak"
        self.recovery_notices: list[str] = []  # damaged files found while loading

    def _damaged(self, what: str, moved: Path, outcome: str) -> None:
        self.recovery_notices.append(f"{what} was damaged and could not be read. {outcome} The damaged file was "
                                     f"kept as {moved.name} in the app data folder.")

    # --- state -----------------------------------------------------------------

    def load_state(self) -> UserState:
        is_dict = lambda d: isinstance(d, dict)  # noqa: E731
        data, moved = read_json_checked(self.state_path, {}, valid=is_dict)
        if moved:
            backup, bad_backup = read_json_checked(self.state_backup_path, None, valid=is_dict)
            if backup is not None and not bad_backup:
                data = backup
                self._damaged("Your saved lists and settings file", moved, "Your previous save was restored.")
            else:
                self._damaged("Your saved lists and settings file", moved,
                              "No usable previous save was found, so settings start fresh.")
        state = UserState.from_dict(data or {})
        if not state.characterdata_dir:
            state.characterdata_dir = str(paths.default_characterdata_dir())
        return state

    def save_state(self, state: UserState) -> None:
        # Keep the previous save so a damaged file can be recovered next time.
        if self.state_path.is_file():
            try:
                shutil.copyfile(self.state_path, self.state_backup_path)
            except OSError:
                pass
        from .profiles import sync_active
        sync_active(state)
        state.save(self.state_path)

    # --- source log --------------------------------------------------------------

    def source_log(self) -> dict:
        log, moved = read_json_checked(self.source_log_path, {}, valid=lambda d: isinstance(d, dict))
        if moved:
            self._damaged("The data-source log", moved, "Update history starts fresh.")
        return log or {}

    def record_source(self, source_id: str, ok: bool, error: str | None = None, detail: dict | None = None) -> None:
        log = self.source_log()
        entry = log.setdefault(source_id, {})
        entry["last_attempt"] = utc_now_iso()
        if ok:
            entry["last_success"] = entry["last_attempt"]
            entry["last_error"] = None
        else:
            entry["last_error"] = error
        if detail:
            entry.update(detail)
        write_json_atomic(self.source_log_path, log)

    # --- caches ------------------------------------------------------------------

    def save_catalog(self, catalog: dict) -> None:
        write_json_atomic(self.catalog_path, catalog, indent=None)

    def save_delivery(self, delivery: dict) -> None:
        write_json_atomic(self.delivery_path, delivery, indent=1)

    def save_lookups(self, lookups: dict) -> None:
        write_json_atomic(self.lookups_path, lookups, indent=1)

    def save_wiki_index(self, index: dict) -> None:
        write_json_atomic(self.wiki_index_path, index, indent=None)

    def load_library(self) -> tuple[Library, list[str]]:
        """Load everything from cache. Returns the library plus notices for the user."""
        notices: list[str] = []
        state = self.load_state()
        has_items = lambda d: isinstance(d, dict) and isinstance(d.get("items"), dict)  # noqa: E731

        catalog, moved = read_json_checked(self.catalog_path, None, valid=has_items)
        if moved:
            self._damaged("The cached item catalog", moved, "It is rebuilt from your installed client.")
        package_dir = paths.client_package_dir(Path(state.characterdata_dir))
        outdated = catalog is not None and (catalog.get("source") or {}).get("parser_version") != tibia_client.PARSER_VERSION
        if catalog is None or outdated:
            # First run, or this app version reads the client differently: re-read the
            # same local files now (no download, so this is not a source update).
            try:
                fresh = tibia_client.read_catalog(package_dir)
                self.save_catalog(fresh)
                self.record_source(tibia_client.SOURCE_ID, True, detail={
                    "client_version": fresh["source"]["client_version"]})
                if outdated:
                    notices.append("The item catalog was rebuilt from your installed client for this app version.")
                catalog = fresh
            except tibia_client.ClientDataError as e:
                notices.append(f"Item catalog {'could not be rebuilt' if outdated else 'unavailable'}: {e} Choose "
                               f"your Tibia folder on the Install tab, then press Check for updates.")
                self.record_source(tibia_client.SOURCE_ID, False, str(e))
        else:
            try:
                install = tibia_client.locate_client(package_dir)
                cached_file = (catalog.get("source") or {}).get("appearances_file")
                if install.appearances_path.name != cached_file:
                    notices.append(f"The installed Tibia client changed (now {install.version or 'unknown'}). "
                                   f"Press Check for updates to review the item catalog changes.")
            except tibia_client.ClientDataError:
                pass

        delivery, moved = read_json_checked(self.delivery_path, None, valid=has_items)
        if moved:
            self._damaged("The cached Delivery Task list", moved, "The bundled list is used until the next update.")
        if delivery is None:
            seed = read_json(paths.bundled_data_dir() / SEED_DELIVERY)
            if seed:
                seed.setdefault("source", {})["bundled"] = True
                delivery = seed
                self.save_delivery(delivery)
                notices.append("Using the Delivery Task list bundled with the app (fetched {date}). "
                               "Press Check for updates for the latest list."
                               .format(date=(seed["source"].get("fetched_at") or "?")[:10]))

        wiki_index, moved = read_json_checked(
            self.wiki_index_path, None, valid=lambda d: isinstance(d, dict) and isinstance(d.get("pages"), dict))
        if moved:
            self._damaged("The cached TibiaWiki item index", moved, "The bundled index is used until the next update.")
        if wiki_index is None:
            wiki_index = read_json(paths.bundled_data_dir() / SEED_WIKI_INDEX)
            if wiki_index:
                wiki_index.setdefault("source", {})["bundled"] = True
                self.save_wiki_index(wiki_index)

        lookups, moved = read_json_checked(self.lookups_path, {}, valid=lambda d: isinstance(d, dict))
        if moved:
            self._damaged("The cached drop lookups", moved, "They will be looked up again when needed.")
        return Library(catalog, delivery, lookups or {}, state, wiki_index), self.recovery_notices + notices
