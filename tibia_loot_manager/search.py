"""Catalog search matching."""


def matches_search(query: str, name: str, client_id: int | None) -> bool:
    """Decide whether a catalog row matches the search box text.

    ``query`` is already stripped and lower-cased and is never empty here.
    ``name`` is the in-game item name or the TibiaWiki name; ``client_id`` is
    None for wiki items whose client ID is unverified.
    """
    # TODO(human): choose the matching rules. Current behaviour: plain substring or exact ID.
    return query in name.lower() or query == str(client_id)
