# Roadmap: future features

These features are planned but not built. Each should be its own module and tab, using the existing data layers
(client catalog, TibiaWiki data, user state), so none of them delays or changes the core: the item catalog, the
editable Delivery Task list, Accepted Loot generation and safe file installation.

Rules that apply to all of them:

- **No game-memory access and no gameplay automation.** Input is entered by hand, or pasted or imported from files
  the user chooses.
- **No background network activity** unless the user explicitly turns it on for that feature.
- **Keep source types separate.** Official client data, TibiaWiki-derived data and third-party estimates stay
  separate, and each value records its source, world (if any) and update time (`values.ItemValue.authority`).
  Third-party market values are estimates and never authoritative.

## 1. Weekly Task planner

- The user selects the Delivery Tasks assigned to them this week, enters the required quantity, and tracks
  collected and remaining amounts.
- Data: new `UserState` section keyed by week (Monday server save) and Tibia item ID.
- Reuses: Delivery Task candidates (`Library.delivery_entries`), min/max ranges for input validation.

## 2. Item-to-hunt finder

- Shows the creatures that drop the selected items, and groups targets that can be collected from the same
  creatures or hunting areas.
- Data: TibiaWiki `droppedby` (already indexed). Hunting areas and drop rates would need TibiaWiki creature pages
  (`Template:Infobox Creature`, loot statistics).
- Must show the source and a confidence level. Drop lists are community data and are never presented as
  guaranteed.

## 3. World-specific value comparison

- Compares NPC prices (official client data) with market data for a world the user picks. Shows each value's source
  and freshness. Optional sorting by value per ounce, using item weight from the wiki.
- Data: implement `values.MarketValueProvider` for a chosen source, with `authority=THIRD_PARTY_ESTIMATE` and
  `world` set.
- Still never adds items to lists automatically.

## 4. Loot profiles

- Named lists for different characters or purposes, each with backup, restore and change history.
- Data: move the current Accepted Loot overrides into a named profile (the default profile = today's list). Store
  history as timestamped snapshots of overrides, not of source data.

## 5. Hunt-session analysis

- The user imports a hunt report, or pastes supported Hunt Analyzer text, to compare the loot received with
  configured item values.
- Parsing must be tolerant and report lines it could not interpret. No memory reading.

## 6. Market watchlist

- The user marks items for price review and checks values manually.
- Polling only if the user explicitly enables it for the watchlist, with a visible interval and an easy off switch.
