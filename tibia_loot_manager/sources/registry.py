"""What each data source is, and how much authority its data carries.

Kinds are kept distinct everywhere data is shown, so official client data,
TibiaWiki-derived data and third-party estimates are never mixed up.
"""

from dataclasses import dataclass

from ..i18n import _

OFFICIAL_CLIENT = "official_client"  # files shipped with the official Tibia client on this PC
OFFICIAL_WEBSITE = "official_website"  # tibia.com
COMMUNITY_WIKI = "community_wiki"  # TibiaWiki: community-maintained, usually accurate, not official
THIRD_PARTY_ESTIMATE = "third_party_estimate"  # fansite market values: estimates, vary by world

KIND_TEXT = {
    OFFICIAL_CLIENT: _("Official client data"),
    OFFICIAL_WEBSITE: _("Official Tibia website"),
    COMMUNITY_WIKI: _("Community wiki"),
    THIRD_PARTY_ESTIMATE: _("Third-party estimate"),
}


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    label: str
    kind: str
    provides: str
    url: str | None = None


SOURCES = {
    s.source_id: s for s in (
        SourceInfo("tibia_client", _("Installed Tibia client"), OFFICIAL_CLIENT,
                   _("Tibia item IDs, in-game names, market categories, NPC trade offers")),
        SourceInfo("tibiawiki_fandom", _("TibiaWiki (Fandom)"), COMMUNITY_WIKI,
                   _("Delivery Task candidates, quantity ranges, NPC buy prices"),
                   "https://tibia.fandom.com/wiki/Delivery_Task"),
        SourceInfo("tibiawiki_fandom_items", _("TibiaWiki item pages"), COMMUNITY_WIKI,
                   _("Item page names and IDs, creature drops, NPC prices"),
                   "https://tibia.fandom.com/wiki/Template:Infobox_Object"),
        SourceInfo("tibiamarket", _("TibiaMarket"), THIRD_PARTY_ESTIMATE,
                   _("Market prices per world: what buyers pay, monthly averages, number of trades"),
                   "https://tibiamarket.top"),
        SourceInfo("tibia_com_guide", _("Official Quick Loot guide"), OFFICIAL_WEBSITE,
                   _("How Accepted Loot is managed in the game (linked, not downloaded)"),
                   "https://www.tibia.com/gameguides/?section=controls&subtopic=manual"),
    )
}
