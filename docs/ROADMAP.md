# Roadmap: future features

These features are planned. Sections marked **Built** are done and describe what shipped. Each should be its own module and tab, using the existing data layers
(client catalog, TibiaWiki data, user state), so none of them delays or changes the core: the item catalog, the
editable Delivery Task list, Accepted Loot generation and safe file installation.

Rules that apply to all of them:

- **No game-memory access and no gameplay automation.** Input is entered by hand, or pasted or imported from files
  the user chooses.
- **No background network activity** unless the user explicitly turns it on for that feature.
- **Keep source types separate.** Official client data, TibiaWiki-derived data and third-party estimates stay
  separate, and each value records its source, world (if any) and update time (`values.ItemValue.authority`).
  Third-party market values are estimates and never authoritative.

## 1. Weekly Task planner — Built (0.5.0)

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
- Partly built: market prices per world now come from TibiaMarket (`sources/tibiamarket.py`) and appear in item
  details and the recommended Skipped Loot list. Still to do: a side-by-side comparison view, and sorting by value
  per ounce (needs item weight from the wiki).
- Still never adds items to lists automatically.
- Market values could also tier items that no NPC buys for strictness levels (today they're tiered by Market
  category). They'd be shown as estimates, and any change would go through the level's review step.

## 4. Loot profiles — Built (0.5.0)

- Named lists for different characters or purposes, each with backup, restore and change history.
- Data: move the current Accepted Loot overrides into a named profile (the default profile = today's list). Store
  history as timestamped snapshots of overrides, not of source data.

## 5. Hunt-session analysis — Built (0.5.0)

- The user imports a hunt report, or pastes supported Hunt Analyzer text, to compare the loot received with
  configured item values.
- Parsing must be tolerant and report lines it could not interpret. No memory reading.
- Built as the Hunt reports screen (`hunts.py`): plural item names are matched to singular client names; when
  several items share a name, the likeliest is chosen and marked "uncertain". Coins count at face value, other
  items at the best NPC buy price.

## 6. Market watchlist

- The user marks items for price review and checks values manually.
- Polling only if the user explicitly enables it for the watchlist, with a visible interval and an easy off switch.

## 7. Link profiles to characters

- Assign a profile to a character folder. On the Install screen, choosing that character picks its profile, and the
  preview names the pairing ("Main → folder 1234, labelled Knight") before anything is written.
- Data: a `character → profile id` map in `UserState`; a deleted profile falls back to "no link".

## 8. Move all local data to another PC

- Export everything (profiles and their history, saved hunt reports, weekly tasks, favorites, saved searches, labels,
  settings) to one file, and import it on another PC with a preview of what will be replaced.
- Never includes loot files from the game folder or the cached client and wiki data (those are rebuilt locally).
- Data: one versioned JSON bundle; import validates every part with the same checks as loading from disk.

## 9. Edit tier rules in the app

- A Strictness settings view to change price thresholds and category tiers, with a live preview of each level's item
  count and the items that change, instead of editing `tier_rules.json` by hand.
