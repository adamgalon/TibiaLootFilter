"""Static Help content: frequently asked questions and release notes."""

from .i18n import _

FAQ = [
    (_("Does this app touch the game?"),
     _("No. It never reads game memory, automates play or asks for your account. It only reads the client's data "
       "files, and writes a character's loot list file when you choose Install.")),
    (_("What is a Tibia item ID?"),
     _("The number the official client uses for an item type, for example gold coin = 3031. The loot list file "
       "stores these numbers, not names. Open Tibia/TFS server IDs and wiki page numbers are different and are "
       "never used.")),
    (_("Why can some items not be exported or installed?"),
     _("Only verified Tibia item IDs are written. An ID is unverified when it cannot be checked against your "
       "installed client, and conflicting when sources disagree, for example a TibiaWiki page lists the ID under "
       "another name. Such items still appear in the manual copy list. Use “Report incorrect item ID” if you know "
       "the right value.")),
    (_("Why do several items have the same name?"),
     _("The game has different objects with the same display name, such as the quest “bag” (235) and the "
       "ordinary bag (2853). The Notes column shows the TibiaWiki name that tells them apart.")),
    (_("How do I undo an installation?"),
     _("Every install saves a timestamped backup first. Select the character on the Install tab and use Restore "
       "selected.")),
    (_("Why must Tibia be closed to install?"),
     _("The client keeps the loot list in memory and may overwrite the file when it exits.")),
    (_("Why are there no market prices?"),
     _("Market prices differ per world and no dependable source is configured. NPC prices are shown with their "
       "source and date. Third-party values would be shown as estimates, never as authoritative.")),
    (_("When does the app go online?"),
     _("Only when you press Check for updates, Look up drops, or open a link or report. There are no background "
       "checks.")),
    (_("How does the hunt report value my loot?"),
     _("Coins count at face value. Every other item counts at the highest price an NPC pays for it, from your "
       "installed client's data. The game's own Loot figure uses different prices, so the two totals can differ. "
       "Items without an NPC buyer, or that couldn't be matched, are listed but not counted.")),
    (_("What is included in a problem report?"),
     _("Only what you see in the report window before sending: your text plus versions, the item and its sources, "
       "and update times. User folders, character folder numbers, character labels and e-mail addresses are "
       "removed. Loot files and character data are never attached.")),
]

RELEASE_NOTES = [
    ("0.5.0", "2026-10-05", [
        _("Profiles: separate Accepted Loot lists with history, compare, and import/export."),
        _("Weekly Tasks: track required, collected and remaining amounts for this week's Delivery Tasks."),
        _("Catalog: favorites, saved searches, and adding or removing everything a search matches at once."),
        _("Hunt reports: paste a Hunt Analyzer session to see what the loot is worth to NPCs, which items aren't on "
          "your Accepted Loot list, and add the looted amounts to this week's tasks."),
        _("Portable version: unzip and run, no Python needed."),
        _("Catalog browsing beyond the first 200 items, safer file writes, recovery from damaged files, and a "
          "working Cancel for updates."),
    ]),
    ("0.4.0", "2026-10-04", [
        _("Item pictures everywhere, made from your installed client's own sprites; animated items move."),
        _("Fixes: report redaction no longer alters words that contain a short character label; the app can't be "
          "opened twice; a failed first install leaves no half-written file."),
    ]),
    ("0.3.0", "2026-10-04", [
        _("New interface from the Loot Manager design: sidebar navigation, dark and light themes, card layouts and "
          "dialogs."),
        _("First-run setup: finds your Tibia folder and lets you choose a starting list."),
        _("Accepted Loot and Delivery Task lists show what you added and removed, with one-click undo."),
    ]),
    ("0.2.0", "2026-10-04", [
        _("Tibia item IDs are tracked as verified, unverified or conflicting; only verified IDs are exported or "
          "installed."),
        _("Reference check (gold coin = 3031) guards against misread client data."),
        _("“Report incorrect item ID” on every item, and a Help & Support tab with FAQ, release notes and problem "
          "reports."),
        _("Data sources are labelled as official client data, community wiki or third-party estimate."),
    ]),
    ("0.1.0", "2026-10-03", [
        _("Item catalog from the installed client, with TibiaWiki names for same-named items."),
        _("Editable Delivery Task list and personal Accepted Loot list that survive data updates."),
        _("Manual copy list, game file export and safe install with backups."),
    ]),
]
