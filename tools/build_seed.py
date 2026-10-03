"""Refresh the TibiaWiki snapshots bundled with the app (used until the first update).

    python tools/build_seed.py            # Delivery Task list and item index
    python tools/build_seed.py delivery   # only the Delivery Task list
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tibia_loot_manager import paths  # noqa: E402
from tibia_loot_manager.datastore import SEED_DELIVERY, SEED_WIKI_INDEX  # noqa: E402
from tibia_loot_manager.sources.http import PoliteHttpClient  # noqa: E402
from tibia_loot_manager.sources.tibiawiki import TibiaWikiSource  # noqa: E402
from tibia_loot_manager.storage import read_json, write_json_atomic  # noqa: E402


def main() -> None:
    wiki = TibiaWikiSource(PoliteHttpClient())
    which = sys.argv[1:] or ["delivery", "index"]

    if "delivery" in which:
        snapshot = wiki.fetch_delivery_snapshot()
        target = paths.bundled_data_dir() / SEED_DELIVERY
        write_json_atomic(target, snapshot, indent=1)
        missing = [t for t, r in snapshot["items"].items() if not r.get("wiki")]
        print(f"Wrote {len(snapshot['items'])} delivery items to {target} "
              f"(page revision {snapshot['source']['revision_timestamp']})")
        if missing:
            print(f"{len(missing)} items have no wiki page: {', '.join(missing)}")

    if "index" in which:
        target = paths.bundled_data_dir() / SEED_WIKI_INDEX
        index = wiki.fetch_object_index(read_json(target), progress=print)
        write_json_atomic(target, index, indent=None)
        print(f"Wrote {len(index['pages'])} item pages to {target} "
              f"({index['source']['pages_downloaded']} downloaded)")


if __name__ == "__main__":
    main()
