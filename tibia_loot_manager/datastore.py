"""Local caches, the source log, and first-run bootstrap."""

from pathlib import Path

from . import paths
from .library import Library
from .sources import tibia_client
from .state import UserState
from .storage import read_json, utc_now_iso, write_json_atomic

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

    # --- state -----------------------------------------------------------------

    def load_state(self) -> UserState:
        state = UserState.load(self.state_path)
        if not state.characterdata_dir:
            state.characterdata_dir = str(paths.default_characterdata_dir())
        return state

    def save_state(self, state: UserState) -> None:
        state.save(self.state_path)

    # --- source log --------------------------------------------------------------

    def source_log(self) -> dict:
        return read_json(self.source_log_path, default={}) or {}

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

        catalog = read_json(self.catalog_path)
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

        delivery = read_json(self.delivery_path)
        if delivery is None:
            seed = read_json(paths.bundled_data_dir() / SEED_DELIVERY)
            if seed:
                seed.setdefault("source", {})["bundled"] = True
                delivery = seed
                self.save_delivery(delivery)
                notices.append("Using the Delivery Task list bundled with the app (fetched {date}). "
                               "Press Check for updates for the latest list."
                               .format(date=(seed["source"].get("fetched_at") or "?")[:10]))

        wiki_index = read_json(self.wiki_index_path)
        if wiki_index is None:
            wiki_index = read_json(paths.bundled_data_dir() / SEED_WIKI_INDEX)
            if wiki_index:
                wiki_index.setdefault("source", {})["bundled"] = True
                self.save_wiki_index(wiki_index)

        lookups = read_json(self.lookups_path, default={})
        return Library(catalog, delivery, lookups, state, wiki_index), notices
