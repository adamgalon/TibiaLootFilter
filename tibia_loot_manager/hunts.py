"""Hunt-report import: parse text copied from Tibia's Hunt Analyzer and value the loot.

The Hunt Analyzer's "Copy to clipboard" text looks like this (numbers use
thousands separators; item names in plural form):

    Session data: From 2026-10-05, 14:23:11 to 2026-10-05, 15:23:41
    Session: 01:00h
    Raw XP Gain: 1,234,567
    XP Gain: 1,851,850
    Loot: 456,789
    Supplies: 123,456
    Balance: 333,333
    Killed Monsters:
      123x dragon
    Looted Items:
      1x a dragon shield
      12x dragon hams

Parsing is tolerant: unknown lines are ignored, and every looted line that
can't be matched to exactly one item is reported rather than guessed. When
several items share a name, the likeliest is shown but left out of totals and
task progress until the user picks one (the pick is remembered per name).
"""

import hashlib
import re

from .library import Library, name_key

COIN_VALUES = {3031: 1, 3035: 100, 3043: 10_000}  # gold, platinum and crystal coin: fixed in-game exchange
_COUNT_LINE = re.compile(r"^\s*(\d[\d,.]*)\s*x\s+(.+?)\s*$", re.IGNORECASE)
_FIELD_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z /]*?)\s*:\s*(.*?)\s*$")
_ARTICLES = ("a ", "an ", "the ", "some ")


def _number(text: str) -> int | None:
    digits = re.sub(r"[,.\s]", "", text or "")
    if digits.startswith("-") and digits[1:].isdigit():
        return -int(digits[1:])
    return int(digits) if digits.isdigit() else None


def parse_report(text: str) -> dict:
    """Split the copied text into session fields, killed monsters and looted items."""
    fields: dict[str, str] = {}
    monsters: list[tuple[int, str]] = []
    items: list[tuple[int, str]] = []
    section = None
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower().rstrip(":")
        if lower in ("killed monsters", "looted items"):
            section = lower
            continue
        count = _COUNT_LINE.match(line)
        if count and section:
            amount = _number(count.group(1))
            if amount:
                (monsters if section == "killed monsters" else items).append((amount, count.group(2)))
            continue
        field = _FIELD_LINE.match(line)
        if field:
            section = None
            fields[field.group(1).strip().lower()] = field.group(2)
    session = fields.get("session data", "")
    period = re.search(r"from\s+(.+?)\s+to\s+(.+)$", session, re.IGNORECASE)
    return {
        "from": period.group(1) if period else None,
        "to": period.group(2) if period else None,
        "duration": fields.get("session"),
        "xp": _number(fields.get("xp gain", "")),
        "raw_xp": _number(fields.get("raw xp gain", "")),
        "loot": _number(fields.get("loot", "")),
        "supplies": _number(fields.get("supplies", "")),
        "balance": _number(fields.get("balance", "")),
        "monsters": [{"count": c, "name": n} for c, n in monsters],
        "items": [{"count": c, "name": n} for c, n in items],
    }


def singular_forms(name: str) -> list[str]:
    """Possible singular names for a looted-item name, most likely first."""
    text = " ".join(name.lower().split())
    for article in _ARTICLES:
        if text.startswith(article):
            text = text[len(article):]
            break

    def word_forms(word: str) -> list[str]:
        forms = [word]
        if word.endswith("ies") and len(word) > 4:
            forms.append(word[:-3] + "y")
        if word.endswith("ves"):
            forms += [word[:-3] + "f", word[:-3] + "fe"]
        if word.endswith(("ches", "shes", "sses", "xes", "zes", "oes")):
            forms.append(word[:-2])
        if word.endswith("s") and not word.endswith("ss"):
            forms.append(word[:-1])
        if word.endswith("men"):
            forms.append(word[:-3] + "man")
        return forms

    if " of " in text:  # "pieces of cloth" -> "piece of cloth"
        head, tail = text.split(" of ", 1)
        words = head.split(" ")
        candidates = [" ".join(words[:-1] + [f]) + " of " + tail for f in word_forms(words[-1])]
    else:
        words = text.split(" ")
        candidates = [" ".join(words[:-1] + [f]) for f in word_forms(words[-1])]
    return list(dict.fromkeys(candidates))


def best_npc_price(item: dict) -> int | None:
    prices = [o["npc_buys_for"] for o in item.get("npc_offers", [])
              if o.get("npc_buys_for") and (o.get("currency") or "gold") == "gold"]
    return max(prices) if prices else None


def match_item(name: str, library: Library) -> tuple[int | None, str, list[int]]:
    """(client ID, how it matched, candidates).

    ``how`` is "exact", "plural", "chosen" (several items share the name; the likeliest was picked and
    ``candidates`` lists them all, likeliest first), or "none".
    """
    for i, form in enumerate(singular_forms(name)):
        ids = library.ids_by_name.get(name_key(form), [])
        if not ids:
            continue
        how = "exact" if i == 0 else "plural"
        if len(ids) == 1:
            return ids[0], how, []
        # Same name, several objects: prefer the one on the Delivery Task list, then one NPCs buy, then
        # anything but a quest item, then one NPCs trade at all, then a marketable one. Marked "chosen"
        # so the screen can say it wasn't certain.
        delivery = library.delivery_ids()

        def rank(cid):
            item = library.items_by_id[cid]
            wiki = library.wiki_by_id.get(cid) or {}
            return (cid in delivery, best_npc_price(item) is not None, wiki.get("primarytype") != "Quest Items",
                    bool(item.get("npc_offers")), item.get("market_category") is not None, -cid)
        ranked = sorted(ids, key=rank, reverse=True)
        return ranked[0], "chosen", ranked
    return None, "none", []


def report_id(text: str) -> str:
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()[:16]
