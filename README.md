# Tibia Loot List Manager

A Windows desktop helper for building Tibia **Quick Loot → Accepted Loot** lists. It starts with every item that a
Delivery Task can request already selected. You can edit that list, add any other item, then copy the list for manual
entry or install it into a character's loot file.

It is a local helper only. It never reads or changes game memory, never automates gameplay, never asks for account
details, and never uploads your Tibia files.

## Running it

Requires Python 3.11+ for Windows. Python from python.org includes Tk. There are no third-party packages.

```
python -m tibia_loot_manager      # or double-click TibiaLootManager.pyw
python -m unittest discover -s tests -t .
```

Your edits, cached data and backups are stored in `%LOCALAPPDATA%\TibiaLootManager`. You can set the
`TIBIA_LOOT_MANAGER_HOME` environment variable to use a different folder, for example when testing.

## What each tab does

| Tab | Purpose |
| --- | --- |
| Item catalog | Every lootable item from your installed client. Search it, filter by category, view values, NPC prices and creature drops, and add or remove items from either list. |
| Delivery Task list | The source candidate list with your edits applied. Excluded items stay visible under *Excluded by me*, and *Restore source defaults* discards your edits. |
| My Accepted Loot | Your personal list: the Delivery Task list (switchable) plus items you added minus items you removed. Shows the item count, the limit warning and unverified items. |
| Copy & export | A one-item-per-line checklist for adding items through the Cyclopedia, and a game-format file export. |
| Install to character | Choose the character data folder, label the numbered folders, preview Merge or Replace, install with a backup, and restore backups. |
| Data sources | Run *Check for updates*, see the freshness of each source and the sources that were evaluated, and set an optional list-size limit. |

## How the data fits together

- **Item IDs come from your installed client.** The client's `assets/appearances-*.dat` pairs each client type ID with
  its in-game name. The catalog contains only pickupable items whose Cyclopedia entry points to themselves.
- **Market variants are folded into their main item.** When the client says an object trades on the Market as
  another ID (for example 7184 "baby seal doll" trades as 7183), only the main item is listed. The variant IDs are
  shown in its notes.
- **Same-named items get their TibiaWiki name.** About 250 names belong to several different objects (for example
  "bag" 2853 is the container and "bag" 235 is the quest item TibiaWiki calls *Bag (Ahmet)*). The app keeps an index
  of every TibiaWiki item page and its `itemid` list. A client item gets a wiki name only when exactly one page claims
  its ID. That name appears in the *Notes* column, is searchable, and is added to the manual copy list. The index also
  provides creature drops and NPC prices for every item. It is bundled with the app, and *Check for updates*
  downloads only pages whose revision changed.
- **Delivery Task candidates come from TibiaWiki.** A wiki item gets a client ID only when the wiki's `itemid` field
  and an installed-client item with the same name agree on exactly one ID. IDs are never guessed from a name alone
  (the client has about 250 duplicate names). Items that cannot be confirmed are shown but are never exported or
  installed.
- **Your edits are stored as overrides** (`user_state.json`): items added to or removed from the Delivery Task list,
  and items added to or removed from Accepted Loot. Source updates replace only the cached source data, so your
  overrides carry over.
- **Updates only run when you click.** *Check for updates* re-reads the client and fetches TibiaWiki at about one
  request per second. It then shows new, changed and removed records, and applies nothing until you confirm. If a
  source fails, its cached copy is kept and the error is shown. On first run the app uses the Delivery Task snapshot
  and item index bundled in `tibia_loot_manager/data/` (refresh them with `python tools/build_seed.py`).
- **Values** are stored as provenance-tagged records: what NPCs pay you, what NPCs charge you, the source, the
  retrieval date, and a world for market data. No market source is configured yet (see [SOURCES.md](SOURCES.md)), and
  nothing is selected or ranked by value. `values.MarketValueProvider` is the extension point for adding one later.

## The loot file

No official schema exists. The format below was checked against client 15.33: the existing files in
`characterdata\<id>\lootBlackWhitelist.json` were read, and the key names appear in `client.exe`.

```json
{
    "blacklistTypes": [2920, 236],
    "listType": "whitelist",
    "whitelistTypes": [17829, 28569]
}
```

`listType` is `"whitelist"` for Accepted Loot and `"blacklist"` for Skipped Loot. The lists hold client type IDs.

How installing is kept safe:

- Installing and exporting are enabled only after existing files in the selected Tibia folder have been read and
  match this format. Any unknown fields are kept.
- *Merge* keeps the character's current Accepted items and adds yours. *Replace* sets the Accepted list to exactly
  yours and lists every item it will remove. Both modes switch `listType` to `whitelist` and leave the Skipped list as
  it is. The preview shows the mode change.
- If Tibia is running, the app asks you to close it. It never closes the game itself.
- Before writing, the app saves a timestamped backup to `%LOCALAPPDATA%\TibiaLootManager\backups\<folder>\`. It then
  writes only that character's loot file, reads it back and compares it. If the check fails, the backup is restored
  automatically. Backups can be restored from the Install tab.
- No verified current list-size limit was found, so none is built in. You can set one on the Data sources tab to get
  a warning.
