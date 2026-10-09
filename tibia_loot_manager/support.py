"""Problem reports: categories, privacy-safe diagnostics, and configurable destinations.

Nothing is ever sent automatically. A report is composed locally, shown to the
user for review and editing, and then either opened in the user's browser or
mail program (if the app owner configured a destination), copied, or saved.

The destination is read from ``data/support.json`` (set by whoever distributes
the app) and may be overridden by ``support.json`` in the app data folder. Both
are empty by default: no address, website or repository is assumed.
"""

import os
import platform
import re
import urllib.parse
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path

from . import __version__, paths
from .i18n import _
from .library import ID_STATUS_DETAIL, MAPPING_STATE, STATE_TEXT, Library
from .storage import read_json

CONFIG_FILE = "support.json"
MAX_URL_LENGTH = 7000  # browsers and GitHub accept roughly 8k characters in a URL
MAX_MAILTO_LENGTH = 1800  # some mail programs truncate longer mailto: links

ITEM, BUG, SUGGESTION, OTHER = "item", "bug", "suggestion", "other"

CATEGORIES = {
    "item_id": (_("Incorrect item ID or item name"), ITEM),
    "delivery": (_("Incorrect Delivery Task membership or quantity range"), ITEM),
    "value": (_("Incorrect NPC price or market value"), ITEM),
    "drop": (_("Incorrect monster drop or source link"), ITEM),
    "install": (_("Import/export or installation problem"), BUG),
    "bug": (_("General bug"), BUG),
    "feature": (_("Feature suggestion"), SUGGESTION),
    "other": (_("Other"), OTHER),
}


# --- destination -----------------------------------------------------------------------

@dataclass
class SupportConfig:
    contact_url: str = ""  # a support or contact web page
    contact_email: str = ""  # an address for general contact
    report_url_template: str = ""  # e.g. a new-issue URL containing {title} and {body}
    report_email: str = ""  # an address that receives reports

    @classmethod
    def load(cls, app_root: Path | None = None) -> "SupportConfig":
        merged: dict = {}
        for path in (paths.bundled_data_dir() / CONFIG_FILE, Path(app_root or paths.app_data_dir()) / CONFIG_FILE):
            data = read_json(path, default={}) or {}
            merged.update({k: v.strip() for k, v in data.items() if isinstance(v, str) and v.strip()})
        known = {f.name for f in fields(cls)}
        config = cls(**{k: v for k, v in merged.items() if k in known})
        config._drop_invalid()
        return config

    def _drop_invalid(self) -> None:
        for name in ("contact_url", "report_url_template"):
            if getattr(self, name) and urllib.parse.urlsplit(getattr(self, name)).scheme not in ("http", "https"):
                setattr(self, name, "")
        if self.report_url_template and "{body}" not in self.report_url_template:
            self.report_url_template = ""
        for name in ("contact_email", "report_email"):
            if getattr(self, name) and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", getattr(self, name)):
                setattr(self, name, "")

    @property
    def can_send_reports(self) -> bool:
        return bool(self.report_url_template or self.report_email)

    @property
    def has_contact(self) -> bool:
        return bool(self.contact_url or self.contact_email)

    def report_links(self, title: str, body: str) -> list[tuple[str, str, bool]]:
        """[(label, url, body_was_left_out)]. If a link would be too long, the body is left
        out and the caller must copy the report to the clipboard for the user to paste."""
        links = []
        q = urllib.parse.quote
        if self.report_url_template:
            url = self.report_url_template.replace("{title}", q(title)).replace("{body}", q(body))
            short = len(url) > MAX_URL_LENGTH
            if short:
                url = self.report_url_template.replace("{title}", q(title)).replace(
                    "{body}", q(_("(The report was copied to your clipboard. Please paste it here.)")))
            links.append((_("Open report page in browser"), url, short))
        if self.report_email:
            url = f"mailto:{self.report_email}?subject={q(title)}&body={q(body)}"
            short = len(url) > MAX_MAILTO_LENGTH
            if short:
                url = f"mailto:{self.report_email}?subject={q(title)}&body=" + q(
                    _("(The report was copied to your clipboard. Please paste it here.)"))
            links.append((_("Open in email program"), url, short))
        return links

    def contact_link(self) -> str | None:
        if self.contact_url:
            return self.contact_url
        if self.contact_email:
            return f"mailto:{self.contact_email}"
        return None


# --- privacy -----------------------------------------------------------------------------

def redact(text: str, labels: list[str] | tuple = ()) -> str:
    """Remove personal details from diagnostic text: user folders, character folder
    numbers, character labels and e-mail addresses."""
    if not text:
        return text
    replacements = []
    for env, placeholder in (("LOCALAPPDATA", "%LOCALAPPDATA%"), ("APPDATA", "%APPDATA%"),
                             ("USERPROFILE", "%USERPROFILE%")):
        value = os.environ.get(env)
        if value:
            replacements.append((value, placeholder))
    replacements.append((str(Path.home()), "%USERPROFILE%"))
    for value, placeholder in replacements:
        for variant in {value, value.replace("\\", "/")}:
            text = re.sub(re.escape(variant), lambda _m, p=placeholder: p, text, flags=re.IGNORECASE)
    text = re.sub(r"((?:characterdata|backups)[\\/])\d+", r"\1<character folder>", text, flags=re.IGNORECASE)
    text = re.sub(r"[^@\s<>\"']+@[^@\s<>\"']+\.\w+", "<email>", text)
    for label in labels:
        if label and len(label) >= 2:
            text = re.sub(r"(?<!\w)" + re.escape(label) + r"(?!\w)", "<character label>", text, flags=re.IGNORECASE)
    return text


# --- diagnostics -------------------------------------------------------------------------

def diagnostics(library: Library, source_log: dict, client_id: int | None = None, title: str | None = None,
                operation: str | None = None, error: str | None = None,
                format_validated: bool | None = None) -> list[tuple[str, str]]:
    """Facts useful for a report. Never includes loot files, character data or account details."""
    csrc = library.catalog.get("source") or {}
    dsrc = library.delivery.get("source") or {}
    isrc = library.wiki_index.get("source") or {}
    rows = [
        (_("App version"), __version__),
        (_("Operating system"), platform.platform(terse=True)),
        (_("Tibia client version"), csrc.get("client_version") or _("not detected")),
        (_("Item catalog read"), csrc.get("read_at") or "—"),
        (_("Delivery Task data"), _("{url}, page revised {rev}, fetched {fetched}").format(
            url=dsrc.get("url") or "—", rev=dsrc.get("revision_timestamp") or "—",
            fetched=dsrc.get("fetched_at") or "—")),
        (_("TibiaWiki item index fetched"), isrc.get("fetched_at") or "—"),
    ]
    for source_id, entry in sorted(source_log.items()):
        rows.append((_("Last update: {source}").format(source=source_id),
                     _("success {ok}, attempt {attempt}").format(
                         ok=entry.get("last_success") or "—", attempt=entry.get("last_attempt") or "—")
                     + (f" ({entry['last_error']})" if entry.get("last_error") else "")))
    if library.reference_issues:
        rows.append((_("Reference check"), " ".join(library.reference_issues)))
    if format_validated is not None:
        rows.append((_("Loot file format confirmed"), _("yes") if format_validated else _("no")))

    if client_id is not None or title:
        item = library.items_by_id.get(client_id) if client_id is not None else None
        rows.append((_("Item name"), item["name"] if item else (title or "—")))
        rows.append((_("Tibia item ID"), str(client_id) if client_id is not None else _("unknown")))
        if client_id is not None:
            code = library.client_id_status(client_id)
            rows.append((_("ID mapping"), f"{STATE_TEXT[MAPPING_STATE[code]]}: {ID_STATUS_DETAIL[code]}"))
            if library.conflicting_pages(client_id):
                rows.append((_("Conflicting wiki pages"), ", ".join(library.conflicting_pages(client_id))))
            if library.same_name_ids(client_id):
                rows.append((_("Other IDs with this name"), ", ".join(map(str, library.same_name_ids(client_id)))))
        wiki = library.wiki_record_for(client_id, title)
        if wiki and not wiki.get("not_found"):
            rows.append((_("TibiaWiki page"), f"{wiki.get('title')} — {wiki.get('url')}"
                         + (f" (page ID {wiki['pageid']})" if wiki.get("pageid") else "")))
            rows.append((_("TibiaWiki item IDs"), ", ".join(map(str, wiki.get("itemids", []))) or "—"))
        dtitle = title or (library.delivery_title_for(client_id) if client_id is not None else None)
        if dtitle and dtitle in library.delivery["items"]:
            d = library.delivery["items"][dtitle]
            rows.append((_("Delivery Task record"), _("{cat}, {lo}–{hi}, source {url}").format(
                cat=d.get("task_category"), lo=d.get("min_qty"), hi=d.get("max_qty"), url=dsrc.get("url"))))
    if operation:
        rows.append((_("Failing operation"), operation))
    if error:
        rows.append((_("Error message"), error))
    labels = list(library.state.character_labels.values())
    return [(k, redact(str(v), labels)) for k, v in rows]


def diagnostics_text(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in rows)


# --- composing ---------------------------------------------------------------------------

ITEM_FIELDS = (("item_name", _("Item name")), ("item_id", _("Tibia item ID")),
               ("expected", _("Expected or corrected value")), ("source_url", _("Source URL")))
BUG_FIELDS = (("steps", _("Steps to reproduce")), ("expected_behavior", _("Expected behavior")),
              ("actual_behavior", _("Actual behavior")))


def compose(category: str, title: str, values: dict[str, str], diagnostics_block: str | None) -> tuple[str, str]:
    """Build the report title and Markdown body from the form."""
    label, kind = CATEGORIES[category]
    lines = [f"**{_('Category')}:** {label}", ""]
    if kind == ITEM:
        for key, name in ITEM_FIELDS:
            lines.append(f"**{name}:** {values.get(key, '').strip() or '—'}")
        lines.append("")
    elif kind == BUG:
        for key, name in BUG_FIELDS:
            lines += [f"### {name}", values.get(key, "").strip() or "—", ""]
    lines += [f"### {_('Description')}", values.get("description", "").strip() or "—", ""]
    if diagnostics_block and diagnostics_block.strip():
        lines += [f"### {_('Diagnostic information')}", diagnostics_block.strip(), ""]
    full_title = f"[{label}] {title.strip()}" if title.strip() else f"[{label}]"
    return full_title, "\n".join(lines).rstrip() + "\n"


def save_report(directory: Path, title: str, body: str) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text(f"# {title}\n\n{body}", encoding="utf-8")
    return path
