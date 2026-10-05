"""Everything the interface can do, as plain methods returning JSON-ready data.

The HTTP layer only routes requests here; nothing in this module knows about
HTML. All access to the library and user state goes through ``self.lock``.
"""

import copy
import json
import os
import threading
import webbrowser
from pathlib import Path

from .. import __version__, lootfile, paths, profiles, support, weekly
from ..datastore import DataStore
from ..help_content import FAQ, RELEASE_NOTES
from ..i18n import _
from ..library import (
    ID_STATUS_DETAIL, MAPPING_STATE, ORIGIN_DELIVERY_SOURCE, ORIGIN_DELIVERY_USER, ORIGIN_MANUAL, STATE_TEXT,
    UNVERIFIED, VERIFIED, Entry, delivery_url,
)
from ..search import matches_search
from ..sources import sprites, tibia_client, tibiawiki
from ..sources.http import PoliteHttpClient
from ..sources.registry import KIND_TEXT, SOURCES
from ..sources.tibiawiki import TibiaWikiSource
from ..storage import utc_now_iso
from ..tibia_process import RUNNING, UNKNOWN, tibia_status
from ..updates import CHANGED, NEW, REMOVED, apply_update, check_for_updates, record_failures
from ..values import NPC_CHARGES, NPC_PAYS, STALE_AFTER_DAYS

PAGE_SIZE = 200
MAX_PAGE = 7000  # enough for the whole catalog in one refresh


class UserError(Exception):
    """A problem to show to the user as-is (not a bug)."""


class Dialogs:
    """Native file dialogs. The real one lives in webui.main; tests pass a stub."""

    def pick_folder(self, initial: str | None) -> str | None:
        return None

    def save_file(self, initial_name: str, extension: str, kinds: list[tuple[str, str]],
                  initial_dir: str | None = None) -> str | None:
        return None

    def open_file(self, kinds: list[tuple[str, str]], initial_dir: str | None = None) -> str | None:
        return None


def _state_of(code: str) -> str:
    return MAPPING_STATE.get(code, UNVERIFIED)


def limit_message(count: int, limit: int | None) -> dict:
    if limit is None:
        return {"warn": False, "title": _("No list limit set"),
                "text": _("No verified client limit was found. Set one in Data sources to get a warning.")}
    if count > limit:
        return {"warn": True, "title": _("Over your limit of {limit}").format(limit=limit),
                "text": _("{count} items exceeds the limit you set. Tibia may not accept all of them.").format(
                    count=count)}
    return {"warn": False, "title": _("Within your limit of {limit}").format(limit=limit),
            "text": _("{count} of {limit} items.").format(count=count, limit=limit)}


class AppService:
    def __init__(self, store: DataStore | None = None, dialogs: Dialogs | None = None, http=None):
        self.store = store or DataStore()
        self.dialogs = dialogs or Dialogs()
        self.http = http  # a PoliteHttpClient, or None for the default
        self.lock = threading.RLock()
        self.library, self.notices = self.store.load_library()
        self.history = profiles.History(self.store.root)
        if profiles.ensure(self.library.state):
            self._save()
            self._accepted_changed(_("Profile created"))
        if self.library.reference_issues:
            self.notices.insert(0, _("Item IDs cannot be trusted: the installed client data failed the reference "
                                     "check ({problems}). Exporting and installing are blocked.").format(
                problems=" ".join(self.library.reference_issues)))
        self._update = None  # {"thread", "progress", "review", "error"}
        self._pending_install = None  # (folder, plan) shown in the last preview
        self._images = None  # (key, sprites.ItemImages | None)

    # --- shared helpers ---------------------------------------------------------------

    def _save(self) -> None:
        self.store.save_state(self.library.state)

    def _characters(self) -> list[lootfile.CharacterFolder]:
        folder = self.library.state.characterdata_dir
        return lootfile.list_characters(Path(folder)) if folder else []

    def _format(self, chars=None) -> lootfile.FormatReport:
        return lootfile.detect_format(chars if chars is not None else self._characters())

    def _id_state(self, entry_or_id) -> tuple[str, str]:
        """(state, reason code) for an Entry or a client ID."""
        if isinstance(entry_or_id, Entry):
            return entry_or_id.mapping_state, entry_or_id.id_status
        code = self.library.client_id_status(entry_or_id)
        return _state_of(code), code

    def _accepted_members(self) -> tuple[set[int], set[str]]:
        active = self.library.accepted_entries()[0]
        return {e.client_id for e in active if e.client_id is not None}, {e.key for e in active}

    def _find(self, entries: list[Entry], key: str) -> Entry | None:
        cid = int(key) if key.isdigit() else None
        return next((e for e in entries if e.key == key or (cid is not None and e.client_id == cid)), None)

    # --- item images -----------------------------------------------------------------

    def _sprite_version(self) -> str:
        src = self.library.catalog.get("source") or {}
        name = src.get("appearances_file") or ""
        return name.removeprefix("appearances-")[:16] or (src.get("client_version") or "none")

    def item_image(self, client_id: int) -> bytes | None:
        """The item's picture, made from the installed client's sprites (cached on disk)."""
        with self.lock:
            item = self.library.items_by_id.get(client_id)
            if not item or not item.get("sprite"):
                return None
            src = self.library.catalog.get("source") or {}
            package = src.get("package_dir") or str(paths.client_package_dir(
                Path(self.library.state.characterdata_dir or paths.default_characterdata_dir())))
            key = (package, self._sprite_version())
            if self._images is None or self._images[0] != key:
                try:
                    images = sprites.ItemImages(Path(package), self.store.cache / "sprites", key[1])
                except (sprites.SpriteError, OSError):
                    images = None
                self._images = (key, images)
            images, sprite = self._images[1], item["sprite"]
        if images is None:
            return None
        try:
            return images.image(client_id, sprite)
        except (sprites.SpriteError, OSError):
            return None

    # --- overview ----------------------------------------------------------------------

    def state(self) -> dict:
        with self.lock:
            lib = self.library
            active = lib.accepted_entries()[0]
            exportable = sum(e.exportable for e in active)
            csrc = lib.catalog.get("source") or {}
            log = self.store.source_log()
            dsrc = lib.delivery.get("source") or {}
            isrc = lib.wiki_index.get("source") or {}
            successes = [v.get("last_success") for v in log.values() if v.get("last_success")]
            # Snapshots carry their own read/fetch times; use them when the log has no entry yet.
            successes += [t for t in (csrc.get("read_at"), dsrc.get("fetched_at"), isrc.get("fetched_at")) if t]
            config = support.SupportConfig.load(self.store.root)
            return {
                "version": __version__,
                "theme": lib.state.theme,
                "onboarded": lib.state.onboarded,
                "profile": {"id": lib.state.active_profile,
                            "name": lib.state.profiles.get(lib.state.active_profile, {}).get("name", ""),
                            "count": len(lib.state.profiles)},
                "notices": self.notices,
                "reference_ok": not lib.reference_issues,
                "reference_issues": lib.reference_issues,
                "counts": {"catalog": len(lib.items_by_id), "delivery": len(lib.delivery_entries()[0]),
                           "accepted": len(active), "exportable": exportable, "blocked": len(active) - exportable},
                "client": {"version": csrc.get("client_version"), "items": len(lib.items_by_id),
                           "read_at": csrc.get("read_at")},
                "last_update": max(successes) if successes else None,
                "any_source_error": any(v.get("last_error") for v in log.values()),
                "limit": lib.state.loot_list_limit,
                "categories": sorted({i["category"] for i in lib.items_by_id.values()}),
                "support": {"can_send": config.can_send_reports, "contact": config.contact_link(),
                            "has_contact": config.has_contact},
                "app_data": str(self.store.root),
                "sprite_version": self._sprite_version(),
            }

    def set_theme(self, theme: str) -> dict:
        with self.lock:
            self.library.state.theme = "light" if theme == "light" else "dark"
            self._save()
            return {"theme": self.library.state.theme}

    def dismiss_notices(self) -> dict:
        self.notices = []
        return {}

    # --- onboarding ----------------------------------------------------------------------

    def onboarding(self) -> dict:
        with self.lock:
            lib = self.library
            folder = lib.state.characterdata_dir
            chars = self._characters()
            report = self._format(chars)
            src = lib.catalog.get("source") or {}
            delivery = lib.delivery_entries()[0]
            dsrc = lib.delivery.get("source") or {}
            return {
                "folder": folder,
                "client_found": bool(lib.items_by_id),
                "client_version": src.get("client_version"),
                "items": len(lib.items_by_id),
                "characters": len(chars),
                "format_ok": report.validated,
                "format_message": report.message,
                "delivery_items": len(delivery),
                "delivery_blocked": sum(not e.exportable for e in delivery),
                "delivery_revised": dsrc.get("revision_timestamp"),
                "follow_delivery": lib.state.accepted_follow_delivery,
            }

    def finish_onboarding(self, start_full: bool) -> dict:
        with self.lock:
            self.library.state.accepted_follow_delivery = bool(start_full)
            self.library.state.onboarded = True
            self._save()
            self._accepted_changed(_("Starting list chosen"))
            return {}

    # --- catalog ---------------------------------------------------------------------------

    def catalog(self, q: str = "", seg: str = "all", cat: str = "", idf: str = "all", offset: int = 0,
                limit: int = PAGE_SIZE) -> dict:
        with self.lock:
            lib = self.library
            query = (q or "").strip().lower()
            accepted_ids, accepted_keys = self._accepted_members()
            delivery_ids = lib.delivery_ids()
            rows = []
            for cid, item in lib.items_by_id.items():
                if query:
                    wiki = lib.wiki_title(cid)
                    if not (matches_search(query, item["name"], cid) or (wiki and matches_search(query, wiki, None))):
                        continue
                if cat and item["category"] != cat:
                    continue
                state, _code = self._id_state(cid)
                if idf != "all" and state != idf:
                    continue
                in_del, in_acc = cid in delivery_ids, cid in accepted_ids
                if (seg == "del" and not in_del) or (seg == "mine" and not in_acc):
                    continue
                note = lib.item_note(cid)
                rows.append({"key": str(cid), "id": cid, "name": item["name"], "category": item["category"],
                             "sub": item["category"] + (" · " + note if note else ""), "state": state,
                             "in_delivery": in_del, "in_accepted": in_acc})
            # Delivery items with no verified client ID are listed but never exported.
            if not cat and idf != VERIFIED:
                active_delivery = {e.key for e in lib.delivery_entries()[0]}
                for entry in lib.unresolved_delivery():
                    if query and not matches_search(query, entry.name, None):
                        continue
                    if idf != "all" and entry.mapping_state != idf:
                        continue
                    in_del = entry.key in active_delivery  # the user may have excluded it
                    in_acc = entry.key in accepted_keys
                    if (seg == "del" and not in_del) or (seg == "mine" and not in_acc):
                        continue
                    rows.append({"key": entry.key, "id": None, "name": entry.name,
                                 "category": entry.delivery["task_category"],
                                 "sub": entry.delivery["task_category"] + " · " + STATE_TEXT[entry.mapping_state],
                                 "state": entry.mapping_state, "in_delivery": in_del, "in_accepted": in_acc})
            rows.sort(key=lambda r: (r["name"].lower(), r["id"] or 0))
            offset = max(0, offset)
            limit = min(max(1, limit), MAX_PAGE)
            return {"total": len(rows), "rows": rows[offset:offset + limit], "offset": offset}

    def item(self, key: str) -> dict:
        with self.lock:
            lib = self.library
            cid = int(key) if key.isdigit() else None
            title = key[5:] if key.startswith("wiki:") else None
            item = lib.items_by_id.get(cid) if cid is not None else None
            if cid is not None and item is None:
                raise UserError(_("Unknown item {id}.").format(id=cid))
            if item is None and title not in lib.delivery["items"]:
                raise UserError(_("Unknown item."))
            if item is not None:
                code = lib.client_id_status(cid)
                dtitle = lib.delivery_title_for(cid)
            else:
                code = lib.delivery_resolution[title][1]
                dtitle = title
            accepted_ids, accepted_keys = self._accepted_members()
            in_acc = (cid in accepted_ids) if cid is not None else (key in accepted_keys)
            record = lib.delivery["items"].get(dtitle) if dtitle else None
            in_del = (cid in lib.delivery_ids()) if cid is not None else (
                key in {e.key for e in lib.delivery_entries()[0]})
            wiki_page = lib.wiki_by_id.get(cid) if cid is not None else None
            drops = lib.wiki_record_for(cid, dtitle)
            values = lib.values_for(cid, dtitle)
            dsrc = lib.delivery.get("source") or {}

            def value_rows(kind):
                groups: dict[tuple, dict] = {}
                for v in values:
                    if v.kind != kind:
                        continue
                    g = groups.setdefault((v.amount, v.currency, v.source, v.source_url), {
                        "amount": v.amount, "currency": v.currency, "source": v.source, "url": v.source_url,
                        "authority": KIND_TEXT.get(v.authority, "") if v.authority else "",
                        "retrieved": v.retrieved_at, "stale": v.is_stale(), "npcs": []})
                    if v.detail:
                        g["npcs"].append(v.detail.split(",")[0])
                out = []
                for g in groups.values():
                    g["npcs"] = list(dict.fromkeys(g["npcs"]))
                    out.append(g)
                return out

            if record:
                delivery_text = record["task_category"] if in_del else _("Excluded by me")
            else:
                delivery_text = _("Added by me") if in_del else _("Not a candidate")
            return {
                "key": key, "id": cid, "name": item["name"] if item else title,
                "category": item["category"] if item else (record or {}).get("task_category", ""),
                "stackable": bool(item and item.get("stackable")),
                "state": _state_of(code), "state_label": STATE_TEXT[_state_of(code)],
                "reason": ID_STATUS_DETAIL.get(code, code),
                "conflicting_pages": lib.conflicting_pages(cid) if cid is not None else [],
                "note": lib.item_note(cid) if cid is not None else "",
                "same_name": [{"id": i, "wiki": lib.wiki_title(i)} for i in lib.same_name_ids(cid)] if cid else [],
                "variants": (item or {}).get("variants", []),
                "wiki": {"title": wiki_page["title"], "url": wiki_page["url"], "pageid": wiki_page.get("pageid"),
                         "kind": wiki_page.get("primarytype")} if wiki_page else None,
                "in_accepted": in_acc, "in_delivery": in_del, "is_candidate": bool(record),
                "delivery_text": delivery_text,
                "qty": f"{record['min_qty']}–{record['max_qty']}" if record and record.get("min_qty") else None,
                "delivery_source": {"url": dsrc.get("url"), "revised": dsrc.get("revision_timestamp"),
                                    "fetched": dsrc.get("fetched_at")} if record else None,
                "pays": value_rows(NPC_PAYS), "charges": value_rows(NPC_CHARGES), "stale_days": STALE_AFTER_DAYS,
                "drops": None if not drops else {
                    "not_found": bool(drops.get("not_found")), "list": drops.get("dropped_by") or [],
                    "title": drops.get("title"), "url": drops.get("url"),
                    "revised": drops.get("page_timestamp"), "fetched": drops.get("fetched_at")},
                "can_lookup": cid is not None and not drops,
            }

    def lookup_drops(self, key: str) -> dict:
        if not key.isdigit():
            raise UserError(_("Only items with a Tibia item ID can be looked up."))
        cid = int(key)
        with self.lock:
            name = self.library.lookup_title_hint(cid)
        record = TibiaWikiSource(self.http or PoliteHttpClient()).lookup_item(name, cid)  # network, outside the lock
        with self.lock:
            self.library.store_wiki_lookup(cid, record, utc_now_iso())
            self.store.save_lookups(self.library.wiki_lookups)
        return self.item(key)

    # --- list edits -------------------------------------------------------------------------

    def toggle_accepted(self, key: str) -> dict:
        with self.lock:
            lib = self.library
            active, excluded = lib.accepted_entries()
            entry = self._find(active, key)
            name = entry.name if entry else (lib.item_name(int(key)) if key.isdigit() else key.removeprefix("wiki:"))
            if entry:
                lib.remove_from_accepted(entry)
            elif key.isdigit():
                lib.add_to_accepted(int(key))
            else:
                gone = self._find(excluded, key)
                if gone:
                    lib.restore_accepted_entry(gone)
            self._save()
            now_in = self._find(lib.accepted_entries()[0], key) is not None
            self._accepted_changed((_("Added {name}") if now_in else _("Removed {name}")).format(name=name))
            return {"in_accepted": now_in}

    def toggle_delivery(self, key: str) -> dict:
        with self.lock:
            lib = self.library
            active, excluded = lib.delivery_entries()
            entry = self._find(active, key)
            if entry:
                lib.remove_from_delivery(entry)
            else:
                gone = self._find(excluded, key)
                if gone:
                    lib.restore_delivery_entry(gone)
                elif key.isdigit():
                    lib.add_to_delivery(int(key))
            self._save()
            return {"in_delivery": self._find(lib.delivery_entries()[0], key) is not None}

    def restore_delivery_defaults(self) -> dict:
        with self.lock:
            self.library.restore_delivery_defaults()
            self._save()
            return {}

    def restore_accepted_defaults(self) -> dict:
        with self.lock:
            self.library.restore_accepted_defaults()
            self._save()
            self._accepted_changed(_("Restored defaults"))
            return {}

    def set_follow_delivery(self, on: bool) -> dict:
        with self.lock:
            self.library.state.accepted_follow_delivery = bool(on)
            self._save()
            self._accepted_changed(_("Included the Delivery Task list") if on
                                   else _("Stopped including the Delivery Task list"))
            return {}

    # --- profiles --------------------------------------------------------------------------

    def _accepted_changed(self, description: str) -> None:
        """Record the active profile's list in its history (skipped when nothing changed)."""
        st = self.library.state
        self.history.record(st.active_profile, description, profiles.snapshot(st),
                            len(self.library.accepted_entries()[0]))

    def _entries_for(self, snap: dict) -> list[Entry]:
        """Accepted Loot entries a profile would have, computed without switching to it."""
        st = self.library.state
        saved = profiles.snapshot(st)
        profiles.apply(st, snap)
        try:
            return self.library.accepted_entries()[0]
        finally:
            profiles.apply(st, saved)

    def _snap(self, pid: str) -> dict:
        st = self.library.state
        if pid not in st.profiles:
            raise UserError(_("That profile no longer exists."))
        profiles.sync_active(st)
        return st.profiles[pid]

    def profile_list(self) -> dict:
        with self.lock:
            st = self.library.state
            profiles.sync_active(st)
            rows = []
            for pid, p in st.profiles.items():
                entries = self._entries_for(p)
                rows.append({"id": pid, "name": p["name"], "created": p.get("created"),
                             "active": pid == st.active_profile, "count": len(entries),
                             "exportable": sum(e.exportable for e in entries),
                             "follow_delivery": p.get("follow_delivery", True)})
            chars = [{"id": c.folder_id, "label": st.character_labels.get(c.folder_id, ""),
                      "accepted": len((c.data or {}).get(lootfile.KEY_ACCEPTED, []))}
                     for c in self._characters() if c.data]
            return {"profiles": rows, "active": st.active_profile, "characters": chars}

    def _run_profile_op(self, op, *args):
        try:
            return op(*args)
        except profiles.ProfileError as e:
            raise UserError(str(e)) from None

    def profile_create(self, name: str, start: str = "delivery") -> dict:
        with self.lock:
            st = self.library.state
            snap = {"follow_delivery": start == "delivery", "extra": [], "excluded": []}
            if start == "copy":
                snap = profiles.snapshot(st)
            pid = self._run_profile_op(profiles.create, st, name, snap)
            profiles.switch(st, pid)
            self._save()
            self._accepted_changed(_("Profile created"))
            return {"id": pid}

    def profile_switch(self, pid: str) -> dict:
        with self.lock:
            self._run_profile_op(profiles.switch, self.library.state, pid)
            self._save()
            return {}

    def profile_rename(self, pid: str, name: str) -> dict:
        with self.lock:
            self._run_profile_op(profiles.rename, self.library.state, pid, name)
            self._save()
            return {"name": self.library.state.profiles[pid]["name"]}

    def profile_duplicate(self, pid: str) -> dict:
        with self.lock:
            st = self.library.state
            source = dict(self._snap(pid))
            new = self._run_profile_op(profiles.create, st, _("{name} copy").format(name=source["name"]), source)
            self._save()
            return {"id": new}

    def profile_delete(self, pid: str) -> dict:
        with self.lock:
            self._run_profile_op(profiles.delete, self.library.state, pid)
            self.history.forget(pid)
            self._save()
            return {}

    def profile_history(self, pid: str) -> dict:
        with self.lock:
            self._snap(pid)
            entries = self.history.entries(pid)
            return {"entries": [{"index": i, "at": e["at"], "description": e.get("description", ""),
                                 "count": e.get("count")} for i, e in reversed(list(enumerate(entries)))]}

    def profile_restore(self, pid: str, index: int) -> dict:
        with self.lock:
            st = self.library.state
            self._snap(pid)
            entries = self.history.entries(pid)
            if not 0 <= index < len(entries):
                raise UserError(_("That version is no longer in the history."))
            snap = entries[index]["snapshot"]
            if pid == st.active_profile:
                profiles.apply(st, snap)
            else:
                st.profiles[pid].update(snap)
            self._save()
            if pid == st.active_profile:
                self._accepted_changed(_("Restored the version from {at}").format(at=entries[index]["at"][:16]))
            return {}

    def profile_compare(self, a: str, b: str) -> dict:
        with self.lock:
            def keyed(pid):
                return {(e.client_id if e.client_id is not None else e.key): e.name
                        for e in self._entries_for(self._snap(pid))}
            left, right = keyed(a), keyed(b)
            st = self.library.state
            return {"a": st.profiles[a]["name"], "b": st.profiles[b]["name"],
                    "only_a": sorted(left[k] for k in left.keys() - right.keys()),
                    "only_b": sorted(right[k] for k in right.keys() - left.keys()),
                    "both": len(left.keys() & right.keys())}

    def profile_export(self, pid: str) -> dict:
        with self.lock:
            st = self.library.state
            data = profiles.export_data(st, pid, __version__) if pid in st.profiles else None
        if data is None:
            raise UserError(_("That profile no longer exists."))
        safe = "".join(c for c in data["name"] if c.isalnum() or c in " -_").strip() or "profile"
        path = self.dialogs.save_file(f"{safe}.lootprofile.json", ".json", [(_("Loot profile"), "*.json")])
        if not path:
            return {"cancelled": True}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {"path": path}

    def profile_import(self) -> dict:
        path = self.dialogs.open_file([(_("Loot profile"), "*.json"), (_("All files"), "*.*")])
        if not path:
            return {"cancelled": True}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise UserError(_("Couldn't read that file: {error}").format(error=e)) from None
        name, snap = self._run_profile_op(profiles.parse_import, data)
        with self.lock:
            pid = self._run_profile_op(profiles.create, self.library.state, name, snap)
            profiles.switch(self.library.state, pid)
            self._save()
            self._accepted_changed(_("Imported from {file}").format(file=Path(path).name))
            return {"id": pid, "name": self.library.state.profiles[pid]["name"]}

    def profile_from_character(self, folder_id: str) -> dict:
        """A new profile holding exactly a character's current in-game Accepted Loot list."""
        with self.lock:
            st = self.library.state
            folder = self._folder(folder_id)
            if not folder.data:
                raise UserError(_("This character has no readable loot file."))
            ids = list(dict.fromkeys(folder.data.get(lootfile.KEY_ACCEPTED, [])))
            label = st.character_labels.get(folder_id) or _("Folder {id}").format(id=folder_id)
            pid = self._run_profile_op(profiles.create, st, _("From {label}").format(label=label),
                                       {"follow_delivery": False, "extra": ids, "excluded": []})
            profiles.switch(st, pid)
            self._save()
            self._accepted_changed(_("Copied from the character's loot file"))
            return {"id": pid, "count": len(ids)}

    # --- Weekly Task tracker ----------------------------------------------------------------

    def _week(self) -> dict:
        """This week's tracker data, starting a new week (and archiving the old one) after server save."""
        st = self.library.state
        week, archive, rolled = weekly.roll_over(st.weekly, st.weekly_archive)
        if rolled or week is not st.weekly:
            st.weekly, st.weekly_archive = week, archive
            self._save()
        self._week_rolled = rolled or getattr(self, "_week_rolled", False)
        return st.weekly

    def _delivery_record(self, key: str) -> dict:
        entry = self._find(self.library.delivery_entries()[0], key)
        return (entry.delivery or {}) if entry else {}

    def weekly_overview(self) -> dict:
        with self.lock:
            week = self._week()
            accepted_ids, accepted_keys = self._accepted_members()
            active = {e.key: e for e in self.library.delivery_entries()[0]}
            tasks = []
            for t in week["tasks"]:
                entry = active.get(t["key"])
                cid = entry.client_id if entry else None
                record = (entry.delivery or {}) if entry else {}
                tasks.append({**t, "id": cid, "remaining": max(0, t["required"] - t["collected"]),
                              "done": t["collected"] >= t["required"],
                              "min": record.get("min_qty"), "max": record.get("max_qty"),
                              "category": record.get("task_category"),
                              "in_accepted": (cid in accepted_ids) if cid is not None else (t["key"] in accepted_keys),
                              "exportable": bool(entry and entry.exportable)})
            new_week = getattr(self, "_week_rolled", False)
            self._week_rolled = False
            return {"week_start": week["week_start"], "next_reset": weekly.next_reset().isoformat(),
                    "tasks": tasks, "archive": self.library.state.weekly_archive, "new_week": new_week,
                    "profile": self.library.state.profiles.get(self.library.state.active_profile, {}).get("name")}

    def weekly_search(self, q: str) -> dict:
        with self.lock:
            query = (q or "").strip().lower()
            tracked = {t["key"] for t in self._week()["tasks"]}
            rows = []
            for e in self.library.delivery_entries()[0]:
                if query and query not in e.name.lower() and query != str(e.client_id):
                    continue
                d = e.delivery or {}
                rows.append({"key": e.key, "name": e.name, "id": e.client_id, "min": d.get("min_qty"),
                             "max": d.get("max_qty"), "category": d.get("task_category"),
                             "tracked": e.key in tracked})
            rows.sort(key=lambda r: (not r["name"].lower().startswith(query), r["name"].lower()))
            return {"rows": rows[:12]}

    def weekly_add(self, key: str, required) -> dict:
        with self.lock:
            entry = self._find(self.library.delivery_entries()[0], key)
            if entry is None:
                raise UserError(_("Only items on your Delivery Task list can be tracked as weekly tasks."))
            week = self._week()
            if any(t["key"] == entry.key for t in week["tasks"]):
                raise UserError(_("{name} is already on this week's list.").format(name=entry.name))
            d = entry.delivery or {}
            try:
                amount = weekly.clamp_required(int(required or d.get("min_qty") or 1), d.get("min_qty"), d.get("max_qty"))
            except (TypeError, ValueError) as e:
                raise UserError(str(e) if str(e) else _("Enter the required amount.")) from None
            week["tasks"].append({"key": entry.key, "name": entry.name, "required": amount, "collected": 0})
            self._save()
            return {}

    def weekly_set(self, key: str, required=None, collected=None, delta=None) -> dict:
        with self.lock:
            task = next((t for t in self._week()["tasks"] if t["key"] == key), None)
            if task is None:
                raise UserError(_("That task is no longer on this week's list."))
            try:
                if required is not None:
                    d = self._delivery_record(key)
                    task["required"] = weekly.clamp_required(int(required), d.get("min_qty"), d.get("max_qty"))
                if collected is not None:
                    task["collected"] = max(0, int(collected))
                if delta is not None:
                    task["collected"] = max(0, task["collected"] + int(delta))
            except (TypeError, ValueError) as e:
                raise UserError(str(e) if str(e) else _("Enter a whole number.")) from None
            self._save()
            return {"collected": task["collected"], "required": task["required"]}

    def weekly_remove(self, key: str) -> dict:
        with self.lock:
            week = self._week()
            week["tasks"] = [t for t in week["tasks"] if t["key"] != key]
            self._save()
            return {}

    def weekly_add_missing_to_accepted(self) -> dict:
        """Put every tracked task item on the active Accepted Loot list."""
        with self.lock:
            accepted_ids, accepted_keys = self._accepted_members()
            added = []
            active = {e.key: e for e in self.library.delivery_entries()[0]}
            for t in self._week()["tasks"]:
                entry = active.get(t["key"])
                if entry and entry.client_id is not None and entry.client_id not in accepted_ids:
                    self.library.add_to_accepted(entry.client_id)
                    added.append(entry.name)
            if added:
                self._save()
                self._accepted_changed(_("Added this week's task items ({n})").format(n=len(added)))
            return {"added": added}

    # --- Delivery Task list ---------------------------------------------------------------

    def delivery(self, tab: str = "active") -> dict:
        with self.lock:
            lib = self.library
            active, excluded = lib.delivery_entries()
            added = [e for e in active if e.origin == ORIGIN_DELIVERY_USER]
            pool = {"active": active, "added": added, "excluded": excluded}.get(tab, active)
            active_keys = {e.key for e in active}
            groups: dict[str, list] = {}
            for e in pool:
                d = e.delivery or {}
                category = d.get("task_category") or (lib.items_by_id.get(e.client_id, {}).get("category")
                                                      if e.client_id else "") or _("Other")
                on = e.key in active_keys
                if not on:
                    status, tone = _("Excluded by me"), "neutral"
                elif not e.exportable:
                    status, tone = STATE_TEXT[e.mapping_state] + " " + _("ID"), e.mapping_state
                elif e.origin == ORIGIN_DELIVERY_USER:
                    status, tone = _("Added by me"), "accent"
                else:
                    status, tone = _("From source"), "plain"
                pays = d.get("npc_buy_price")
                groups.setdefault(category, []).append({
                    "key": e.key, "id": e.client_id, "name": e.name, "state": e.mapping_state,
                    "qty": f"{d['min_qty']}–{d['max_qty']}" if d.get("min_qty") else "—", "pays": pays,
                    "status": status, "tone": tone, "on": on,
                    "action": _("Exclude") if on and e.origin == ORIGIN_DELIVERY_SOURCE
                    else _("Remove") if on else _("Include again")})
            src = lib.delivery.get("source") or {}
            return {
                "tab": tab,
                "counts": {"active": len(active), "added": len(added), "excluded": len(excluded)},
                "groups": [{"category": c, "rows": sorted(r, key=lambda x: x["name"].lower())}
                           for c, r in sorted(groups.items())],
                "source": {"url": src.get("url") or delivery_url(), "revised": src.get("revision_timestamp"),
                           "fetched": src.get("fetched_at"), "bundled": bool(src.get("bundled"))},
            }

    # --- Accepted Loot ----------------------------------------------------------------------

    def accepted(self, tab: str = "active") -> dict:
        with self.lock:
            lib = self.library
            active, excluded = lib.accepted_entries()
            from_delivery = [e for e in active if e.origin != ORIGIN_MANUAL]
            added = [e for e in active if e.origin == ORIGIN_MANUAL]
            pool = {"active": active, "added": added, "removed": excluded}.get(tab, active)
            active_keys = {e.key for e in active}
            exportable = sum(e.exportable for e in active)
            rows = [{"key": e.key, "id": e.client_id, "name": e.name, "state": e.mapping_state,
                     "state_label": STATE_TEXT[e.mapping_state], "exportable": e.exportable,
                     "on": e.key in active_keys,
                     "from": _("Added by me") if e.origin == ORIGIN_MANUAL else _("Delivery Task list")}
                    for e in sorted(pool, key=lambda x: x.name.lower())]
            return {
                "tab": tab, "rows": rows,
                "counts": {"total": len(active), "from_delivery": len(from_delivery), "added": len(added),
                           "removed": len(excluded), "exportable": exportable, "blocked": len(active) - exportable},
                "follow_delivery": lib.state.accepted_follow_delivery,
                "limit": limit_message(exportable, lib.state.loot_list_limit),
            }

    # --- export ------------------------------------------------------------------------------

    def _manual_lines(self, sort: str, with_ids: bool) -> list[str]:
        lib = self.library
        entries = lib.accepted_entries()[0]

        def category(e):
            item = lib.items_by_id.get(e.client_id) if e.client_id is not None else None
            return item["category"] if item else (e.delivery or {}).get("task_category", "")

        if sort == "category":
            entries.sort(key=lambda e: (category(e).lower(), e.name.lower()))
        else:
            entries.sort(key=lambda e: e.name.lower())
        lines = []
        for e in entries:
            text = e.name
            if with_ids:
                text += f"\t{e.client_id if e.client_id is not None else '?'}"
            if e.client_id is not None and lib.shares_name(e.client_id):
                hint = lib.wiki_title(e.client_id)
                text += "\t" + (_("(the one TibiaWiki calls “{title}”, ID {id})").format(title=hint, id=e.client_id)
                                if hint else _("(several items share this name: ID {id})").format(id=e.client_id))
            lines.append(text)
        return lines

    def export(self, sort: str = "name", with_ids: bool = False) -> dict:
        with self.lock:
            lib = self.library
            entries = lib.accepted_entries()[0]
            ids = sorted(lib.accepted_ids())
            blocked = [e.name for e in entries if not e.exportable]
            report = self._format()
            preview = lootfile.accepted_only_file(ids[:8])
            json_text = lootfile.serialize(preview).rstrip()
            if len(ids) > 8:
                json_text = json_text.replace(str(ids[7]), f"{ids[7]},\n        …")
            return {
                "lines": self._manual_lines(sort, with_ids), "count": len(entries), "export_count": len(ids),
                "blocked": blocked, "format_ok": report.validated, "format_message": report.message,
                "json_preview": json_text, "client_version": (lib.catalog.get("source") or {}).get("client_version"),
            }

    def export_file(self) -> dict:
        with self.lock:
            if not self._format().validated:
                raise UserError(_("The loot file format could not be confirmed, so exporting is disabled."))
            ids = sorted(self.library.accepted_ids())
        if not ids:
            raise UserError(_("Your Accepted Loot list has no exportable items."))
        path = self.dialogs.save_file(lootfile.FILE_NAME, ".json", [(_("JSON file"), "*.json")])
        if not path:
            return {"cancelled": True}
        data = lootfile.accepted_only_file(ids)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(lootfile.serialize(data))
        lootfile.read_file(Path(path))
        return {"path": path, "count": len(ids)}

    def export_text(self, sort: str = "name", with_ids: bool = False) -> dict:
        with self.lock:
            lines = self._manual_lines(sort, with_ids)
        path = self.dialogs.save_file("accepted-loot.txt", ".txt", [(_("Text file"), "*.txt")])
        if not path:
            return {"cancelled": True}
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return {"path": path}

    # --- install --------------------------------------------------------------------------------

    def _folder(self, folder_id: str) -> lootfile.CharacterFolder:
        folder = next((c for c in self._characters() if c.folder_id == folder_id), None)
        if folder is None:
            raise UserError(_("Character folder {id} was not found.").format(id=folder_id))
        return folder

    def install(self, selected: str | None = None) -> dict:
        with self.lock:
            lib = self.library
            chars = self._characters()
            report = self._format(chars)
            labels = lib.state.character_labels
            mode_text = {lootfile.MODE_ACCEPTED: _("Accepted Loot"), lootfile.MODE_SKIPPED: _("Skipped Loot")}
            rows = []
            for c in chars:
                data = c.data or {}
                rows.append({
                    "id": c.folder_id, "label": labels.get(c.folder_id, ""),
                    "mode": mode_text.get(c.mode, _("No loot file yet") if not c.exists else _("Unreadable")),
                    "accepted": len(data.get(lootfile.KEY_ACCEPTED, [])) if c.data else None,
                    "skipped": len(data.get(lootfile.KEY_SKIPPED, [])) if c.data else None,
                    "error": c.error, "exists": c.exists})
            if not selected or selected not in {c.folder_id for c in chars}:
                selected = chars[0].folder_id if chars else None
            backups = []
            if selected:
                for b in lootfile.list_backups(self.store.backups, selected):
                    backups.append({"file": b.name, "saved": b.stat().st_mtime})
            active = lib.accepted_entries()[0]
            return {
                "folder": lib.state.characterdata_dir, "default_folder": str(paths.default_characterdata_dir()),
                "format_ok": report.validated, "format_message": report.message, "characters": rows,
                "selected": selected, "backups": backups,
                "export_count": sum(e.exportable for e in active), "blocked": sum(not e.exportable for e in active),
            }

    def set_folder(self, use_default: bool = False) -> dict:
        if use_default:
            folder = paths.default_characterdata_dir()
        else:
            picked = self.dialogs.pick_folder(self.library.state.characterdata_dir)
            if not picked:
                return {"cancelled": True}
            folder = Path(picked)
            if (folder / "characterdata").is_dir():
                folder = folder / "characterdata"
        with self.lock:
            self.library.state.characterdata_dir = str(folder)
            self._save()
        return {"folder": str(folder)}

    def set_label(self, folder_id: str, label: str) -> dict:
        with self.lock:
            labels = self.library.state.character_labels
            if label.strip():
                labels[folder_id] = label.strip()[:60]
            else:
                labels.pop(folder_id, None)
            self._save()
            return {}

    def install_preview(self, folder_id: str, mode: str) -> dict:
        with self.lock:
            lib = self.library
            if not self._format().validated:
                raise UserError(_("The loot file format could not be confirmed, so installing is disabled."))
            folder = self._folder(folder_id)
            if folder.error:
                raise UserError(_("This character's loot file can't be read: {error}").format(error=folder.error))
            ids = sorted(lib.accepted_ids())
            if not ids:
                raise UserError(_("Your Accepted Loot list has no exportable items."))
            plan = lootfile.plan_install(folder.data, ids, mode)
            self._pending_install = (folder, plan)
            label = lib.state.character_labels.get(folder_id)
            blocked = [e.name for e in lib.accepted_entries()[0] if not e.exportable]
            mode_text = {lootfile.MODE_ACCEPTED: _("Accepted Loot"), lootfile.MODE_SKIPPED: _("Skipped Loot"),
                         None: _("no file yet")}
            rows = []
            if plan.creates_file:
                rows.append({"icon": "file-plus", "tone": "warn", "label": _("A new loot file will be created"),
                             "value": lootfile.FILE_NAME})
            if plan.mode_change:
                rows.append({"icon": "swap", "tone": "warn",
                             "label": _("Loot mode changes: {old} → Accepted Loot").format(
                                 old=mode_text.get(plan.old_mode, plan.old_mode)), "value": "listType"})
            rows.append({"icon": "plus-circle", "tone": "ok", "label": _("Items added to the Accepted list"),
                         "value": f"+{len(plan.added)}"})
            rows.append({"icon": "equals", "tone": "muted", "label": _("Already on the list, kept"),
                         "value": str(len(plan.kept))})
            if plan.mode == lootfile.REPLACE:
                rows.append({"icon": "minus-circle", "tone": "bad", "label": _("Items removed from the Accepted list"),
                             "value": f"−{len(plan.removed)}"})
            rows.append({"icon": "lock-simple", "tone": "muted", "label": _("Skipped list left unchanged"),
                         "value": str(len(plan.new_data.get(lootfile.KEY_SKIPPED, [])))})
            total = len(plan.new_data[lootfile.KEY_ACCEPTED])
            return {
                "mode": plan.mode, "folder": folder_id, "label": label,
                "rows": rows, "total": total,
                "removed": [lib.item_name(i) for i in plan.removed],
                "also_skipped": [lib.item_name(i) for i in plan.also_skipped],
                "blocked": blocked,
                "limit": limit_message(total, lib.state.loot_list_limit),
                **self._tibia_flags(),
                "backup_dir": str(lootfile.backup_dir_for(self.store.backups, folder_id)),
            }

    def install_apply(self, folder_id: str, mode: str) -> dict:
        with self.lock:
            pending = self._pending_install
            if not pending or pending[0].folder_id != folder_id or pending[1].mode != mode:
                raise UserError(_("Please preview the installation again."))
            if tibia_status() == RUNNING:
                raise UserError(_("Tibia is running. Close the game yourself, then try again. The client keeps the "
                                  "loot list in memory and can overwrite the file when it exits."))
            folder, plan = pending
            try:
                result = lootfile.install(plan, folder, self.store.backups)
            except (OSError, lootfile.LootFileError) as e:
                raise UserError(str(e)) from None
            self._pending_install = None
            return {"backup": result.backup.name if result.backup else None,
                    "count": len(plan.new_data[lootfile.KEY_ACCEPTED])}

    @staticmethod
    def _tibia_flags() -> dict:
        status = tibia_status()
        return {"tibia_running": status == RUNNING, "tibia_unknown": status == UNKNOWN}

    def _backup_path(self, folder_id: str, file: str) -> Path:
        backup = next((b for b in lootfile.list_backups(self.store.backups, folder_id) if b.name == file), None)
        if backup is None:
            raise UserError(_("That backup no longer exists."))
        return backup

    def restore_preview(self, folder_id: str, file: str) -> dict:
        with self.lock:
            backup = self._backup_path(folder_id, file)
            try:
                data = lootfile.read_file(backup)
            except lootfile.LootFileError as e:
                raise UserError(str(e)) from None
            mode_text = {lootfile.MODE_ACCEPTED: _("Accepted Loot"), lootfile.MODE_SKIPPED: _("Skipped Loot")}
            return {"file": file, "folder": folder_id, "label": self.library.state.character_labels.get(folder_id),
                    "mode": mode_text.get(data[lootfile.KEY_MODE]), "accepted": len(data[lootfile.KEY_ACCEPTED]),
                    "skipped": len(data[lootfile.KEY_SKIPPED]), **self._tibia_flags()}

    def restore_apply(self, folder_id: str, file: str) -> dict:
        with self.lock:
            if tibia_status() == RUNNING:
                raise UserError(_("Tibia is running. Close the game yourself, then try again."))
            backup = self._backup_path(folder_id, file)
            try:
                safety = lootfile.restore_backup(backup, self._folder(folder_id), self.store.backups)
            except (OSError, lootfile.LootFileError) as e:
                raise UserError(str(e)) from None
            return {"safety_backup": safety.name if safety else None}

    def open_folder(self, which: str, folder_id: str | None = None) -> dict:
        if which == "backups":
            target = lootfile.backup_dir_for(self.store.backups, folder_id) if folder_id else self.store.backups
        elif which == "reports":
            target = self.store.root / "reports"
        else:
            target = self.store.root
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(target)
        return {}

    # --- data sources and updates ----------------------------------------------------------------

    def sources(self) -> dict:
        with self.lock:
            lib = self.library
            log = self.store.source_log()
            csrc = lib.catalog.get("source") or {}
            dsrc = lib.delivery.get("source") or {}
            isrc = lib.wiki_index.get("source") or {}
            rows = []
            for sid, where, fresh, fallback in (
                (tibia_client.SOURCE_ID, f"appearances.dat · {csrc.get('client_version') or '?'}",
                 csrc.get("read_at"), csrc.get("read_at")),
                (tibiawiki.SOURCE_ID, _("MediaWiki API · page revised {d}").format(
                    d=(dsrc.get("revision_timestamp") or "?")[:10]), dsrc.get("fetched_at"),
                 dsrc.get("fetched_at") if dsrc.get("bundled") else None),
                (tibiawiki.INDEX_SOURCE_ID, _("{n} pages · only changed pages are downloaded").format(
                    n=len(lib.wiki_index["pages"])), isrc.get("fetched_at"),
                 isrc.get("fetched_at") if isrc.get("bundled") else None),
            ):
                info = SOURCES[sid]
                entry = log.get(sid, {})
                rows.append({"name": info.label, "where": where, "gives": info.provides, "type": KIND_TEXT[info.kind],
                             "official": info.kind.startswith("official"), "fresh": fresh,
                             "last_success": entry.get("last_success") or fallback,
                             "error": entry.get("last_error"),
                             "bundled": sid != tibia_client.SOURCE_ID and fallback is not None})
            rows.append({"name": _("Market values"), "where": _("Per world"),
                         "gives": _("Not configured. No reliable per-world source found yet."),
                         "type": KIND_TEXT["third_party_estimate"], "official": False, "fresh": None,
                         "last_success": None, "error": None, "off": True})
            successes = [r["last_success"] for r in rows if r.get("last_success")]
            return {"rows": rows, "last_update": max(successes) if successes else None,
                    "limit": lib.state.loot_list_limit, "app_data": str(self.store.root)}

    def set_limit(self, value) -> dict:
        with self.lock:
            if value in (None, ""):
                self.library.state.loot_list_limit = None
            else:
                try:
                    n = int(value)
                except (TypeError, ValueError):
                    raise UserError(_("Enter a positive whole number, or leave it empty.")) from None
                if n <= 0:
                    raise UserError(_("Enter a positive whole number, or leave it empty."))
                self.library.state.loot_list_limit = n
            self._save()
            return {"limit": self.library.state.loot_list_limit}

    def start_update(self) -> dict:
        with self.lock:
            if self._update and self._update.get("running"):
                return self.update_status()
            cancel = threading.Event()
            job = {"running": True, "progress": _("Starting…"), "review": None, "error": None, "cancel": cancel}
            self._update = job
            # The check runs for minutes without the lock; give it a stable copy of the user's edits.
            snapshot = copy.copy(self.library)
            snapshot.state = copy.deepcopy(self.library.state)

        def progress(text):
            job["progress"] = text

        def work():
            try:
                http = self.http or PoliteHttpClient(cancel=cancel)
                if self.http is not None:
                    self.http.cancel = cancel
                job["review"] = check_for_updates(snapshot, http, progress=progress)
            except Exception as e:  # reported to the user; caches are untouched
                job["error"] = str(e)
            finally:
                job["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return self.update_status()

    def update_status(self) -> dict:
        job = self._update
        if not job:
            return {"running": False, "review": None}
        out = {"running": job["running"], "progress": job["progress"], "error": job["error"], "review": None}
        review = job.get("review")
        if review is not None:
            area_text = {"delivery": _("Delivery Task"), "catalog": _("Client catalog"),
                         "wiki_index": _("TibiaWiki item page")}
            tabs = {NEW: [], CHANGED: [], REMOVED: []}
            for c in review.changes:
                tabs[c.kind].append({"area": area_text.get(c.area, c.area), "name": c.name, "detail": c.details,
                                     "note": c.user_note})
            state = self.library.state
            out["review"] = {
                "results": [{"label": r.label, "ok": r.ok, "error": r.error} for r in review.results],
                "tabs": tabs, "any_success": review.any_success,
                "removed_delivery": sum(1 for c in review.changes if c.area == "delivery" and c.kind == REMOVED),
                "kept": {"excluded": len(state.delivery_removed) + len(state.accepted_excluded),
                         "added": len(state.delivery_added) + len(state.accepted_extra)},
            }
        return out

    def apply_update(self, keep_removed: bool = False) -> dict:
        job = self._update
        if not job or job["running"] or not job.get("review"):
            raise UserError(_("There is no finished update check to apply."))
        with self.lock:
            apply_update(job["review"], self.library, self.store, keep_removed)
            self.notices = [n for n in self.notices if "Check for updates" not in n]
            self._update = None
        return {}

    def cancel_update(self) -> dict:
        """Stop a running check (its next request or wait raises Cancelled) or discard a finished one."""
        job = self._update
        if job:
            job["cancel"].set()
            if job.get("review"):
                record_failures(job["review"], self.store)
        self._update = None
        return {}

    # --- help and reports ------------------------------------------------------------------------

    def help(self) -> dict:
        config = support.SupportConfig.load(self.store.root)
        return {"faq": [{"q": q, "a": a} for q, a in FAQ],
                "releases": [{"version": v, "date": d, "notes": n} for v, d, n in RELEASE_NOTES],
                "support": {"can_send": config.can_send_reports, "contact": config.contact_link(),
                            "has_contact": config.has_contact}}

    def report_template(self, category: str = "bug", key: str | None = None, operation: str | None = None,
                        error: str | None = None) -> dict:
        with self.lock:
            lib = self.library
            cid = int(key) if key and key.isdigit() else None
            title = key[5:] if key and key.startswith("wiki:") else None
            rows = support.diagnostics(lib, self.store.source_log(), cid, title, operation, error,
                                       self._format().validated)
            item = lib.items_by_id.get(cid) if cid is not None else None
            wiki = lib.wiki_record_for(cid, title) if (cid is not None or title) else None
            name = item["name"] if item else (title or "")
            config = support.SupportConfig.load(self.store.root)
            return {
                "categories": [{"key": k, "label": label, "kind": kind}
                               for k, (label, kind) in support.CATEGORIES.items()],
                "category": category if category in support.CATEGORIES else "bug",
                "title": operation or (f"{name} ({cid})" if cid is not None else name),
                "item_name": name, "item_id": str(cid) if cid is not None else "",
                "source_url": wiki.get("url", "") if wiki and not wiki.get("not_found") else "",
                "diagnostics": support.diagnostics_text(rows),
                "destinations": {"url": bool(config.report_url_template), "email": bool(config.report_email)},
            }

    def report_compose(self, category: str, title: str, values: dict, diagnostics: str | None) -> dict:
        if category not in support.CATEGORIES:
            raise UserError(_("Unknown report category."))
        full_title, body = support.compose(category, title or "", values or {}, diagnostics)
        return {"title": full_title, "body": body}

    def report_links(self, title: str, body: str) -> dict:
        config = support.SupportConfig.load(self.store.root)
        return {"links": [{"label": label, "url": url, "needs_clipboard": short}
                          for label, url, short in config.report_links(title, body)]}

    def report_save(self, title: str, body: str, choose: bool = True) -> dict:
        reports = self.store.root / "reports"
        if not choose:
            return {"path": str(support.save_report(reports, title, body))}
        reports.mkdir(parents=True, exist_ok=True)
        path = self.dialogs.save_file("report.md", ".md", [(_("Markdown text"), "*.md"), (_("Text file"), "*.txt")],
                                      str(reports))
        if not path:
            return {"cancelled": True}
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{body}")
        return {"path": path}

    def open_url(self, url: str) -> dict:
        if not url.startswith(("https://", "http://", "mailto:")):
            raise UserError(_("Only web and e-mail links can be opened."))
        webbrowser.open(url)
        return {}
