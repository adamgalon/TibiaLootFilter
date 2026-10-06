"""Reading, validating and safely writing the client's ``lootBlackWhitelist.json``.

There is no official schema. The structure below was confirmed on client
15.33 by reading existing files and by the key names present in the client
executable. It is never assumed: writes are allowed only after files in the
selected Tibia folder have been read and match it, and unknown keys are
preserved untouched.

    {
        "blacklistTypes": [<client id>, ...],   # "Skipped Loot"
        "listType": "blacklist" | "whitelist",  # "whitelist" = use Accepted Loot
        "whitelistTypes": [<client id>, ...]    # "Accepted Loot"
    }
"""

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .i18n import _

FILE_NAME = "lootBlackWhitelist.json"
KEY_MODE, KEY_ACCEPTED, KEY_SKIPPED = "listType", "whitelistTypes", "blacklistTypes"
MODE_ACCEPTED, MODE_SKIPPED = "whitelist", "blacklist"
REQUIRED_KEYS = (KEY_SKIPPED, KEY_MODE, KEY_ACCEPTED)
MERGE, REPLACE = "merge", "replace"


class LootFileError(Exception):
    pass


def validate(obj) -> list[str]:
    """Return a list of problems; empty means the structure matches the known format."""
    if not isinstance(obj, dict):
        return [_("The file does not contain a JSON object.")]
    problems = []
    for key in REQUIRED_KEYS:
        if key not in obj:
            problems.append(_("Missing key \"{key}\".").format(key=key))
    if KEY_MODE in obj and obj[KEY_MODE] not in (MODE_ACCEPTED, MODE_SKIPPED):
        problems.append(_("Unrecognised list type \"{value}\".").format(value=obj[KEY_MODE]))
    for key in (KEY_ACCEPTED, KEY_SKIPPED):
        value = obj.get(key, [])
        if not isinstance(value, list) or not all(isinstance(x, int) and not isinstance(x, bool) and x > 0
                                                  for x in value):
            problems.append(_("\"{key}\" is not a list of item IDs.").format(key=key))
    return problems


def read_file(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as e:
        raise LootFileError(_("Could not read {path}: {error}").format(path=path, error=e)) from None
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise LootFileError(_("{name} is not valid JSON: {error}").format(name=Path(path).name, error=e)) from None
    problems = validate(obj)
    if problems:
        raise LootFileError(" ".join(problems))
    return obj


def serialize(obj: dict) -> str:
    """Match the client's own output style (sorted keys, 4-space indent)."""
    return json.dumps(obj, indent=4, sort_keys=True) + "\n"


# --- discovery and format detection ----------------------------------------------

@dataclass
class CharacterFolder:
    folder_id: str
    path: Path
    file_path: Path
    exists: bool
    data: dict | None = None
    error: str | None = None

    @property
    def mode(self) -> str | None:
        return self.data.get(KEY_MODE) if self.data else None


@dataclass
class FormatReport:
    validated: bool
    message: str
    evidence: list[Path] = field(default_factory=list)
    extra_keys: set[str] = field(default_factory=set)


def list_characters(characterdata_dir: Path) -> list[CharacterFolder]:
    root = Path(characterdata_dir)
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        file_path = child / FILE_NAME
        folder = CharacterFolder(child.name, child, file_path, file_path.is_file())
        # Only folders that look like character data folders are offered
        if not folder.exists and not re.fullmatch(r"\d+", child.name):
            continue
        if folder.exists:
            try:
                folder.data = read_file(file_path)
            except LootFileError as e:
                folder.error = str(e)
        out.append(folder)
    return out


def detect_format(characters: list[CharacterFolder]) -> FormatReport:
    valid = [c for c in characters if c.exists and c.data is not None]
    invalid = [c for c in characters if c.exists and c.error]
    if not valid:
        if invalid:
            return FormatReport(False, _("Existing loot files do not match the known format, so installing "
                                         "is disabled. Manual copy is still available."))
        return FormatReport(False, _("No existing loot file was found to confirm the format. Open the "
                                     "Cyclopedia's loot settings in Tibia once with any character, close Tibia, "
                                     "then refresh. Manual copy is still available."))
    extra = set()
    for c in valid:
        extra |= set(c.data) - set(REQUIRED_KEYS)
    msg = _("Format confirmed from {n} existing file(s) in this Tibia folder.").format(n=len(valid))
    if invalid:
        msg += " " + _("{n} file(s) could not be read and will not be written to.").format(n=len(invalid))
    if extra:
        msg += " " + _("Unknown fields ({keys}) will be preserved.").format(keys=", ".join(sorted(extra)))
    return FormatReport(True, msg, [c.file_path for c in valid], extra)


# --- planning -------------------------------------------------------------------

@dataclass
class InstallPlan:
    mode: str  # MERGE or REPLACE
    new_data: dict
    old_mode: str | None
    added: list[int]
    removed: list[int]
    kept: list[int]
    also_other: list[int]  # IDs on both the installed list and the character's other list
    creates_file: bool
    target: str = MODE_ACCEPTED  # which list is installed: MODE_ACCEPTED or MODE_SKIPPED

    @property
    def mode_change(self) -> bool:
        return self.old_mode != self.target

    @property
    def list_key(self) -> str:
        return KEY_ACCEPTED if self.target == MODE_ACCEPTED else KEY_SKIPPED

    @property
    def other_key(self) -> str:
        return KEY_SKIPPED if self.target == MODE_ACCEPTED else KEY_ACCEPTED


def plan_install(existing: dict | None, ids: list[int], mode: str, target: str = MODE_ACCEPTED) -> InstallPlan:
    """Compute the file to write: the target list (Accepted or Skipped) and ``listType``, which switches the
    character to that list. The other list and any unknown fields are carried over unchanged."""
    if mode not in (MERGE, REPLACE):
        raise ValueError(mode)
    if target not in (MODE_ACCEPTED, MODE_SKIPPED):
        raise ValueError(target)
    key = KEY_ACCEPTED if target == MODE_ACCEPTED else KEY_SKIPPED
    other = KEY_SKIPPED if target == MODE_ACCEPTED else KEY_ACCEPTED
    base = dict(existing) if existing else {KEY_SKIPPED: [], KEY_MODE: MODE_SKIPPED, KEY_ACCEPTED: []}
    old_ids = list(base.get(key, []))
    new_set = set(ids)
    if mode == MERGE:
        result = old_ids + sorted(new_set - set(old_ids))
    else:
        result = sorted(new_set)
    data = {**base, KEY_MODE: target, key: result}
    old_set = set(old_ids)
    return InstallPlan(
        mode=mode,
        new_data=data,
        old_mode=existing.get(KEY_MODE) if existing else None,
        added=sorted(set(result) - old_set),
        removed=sorted(old_set - set(result)),
        kept=sorted(old_set & set(result)),
        also_other=sorted(set(result) & set(base.get(other, []))),
        creates_file=existing is None,
        target=target,
    )


def accepted_only_file(accepted_ids: list[int]) -> dict:
    """A standalone game-format file for export (no existing settings to merge with)."""
    return {KEY_SKIPPED: [], KEY_MODE: MODE_ACCEPTED, KEY_ACCEPTED: sorted(set(accepted_ids))}


# --- backups, install, restore ---------------------------------------------------------

def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_dir_for(backups_root: Path, folder_id: str) -> Path:
    return Path(backups_root) / folder_id


def create_backup(file_path: Path, backups_root: Path, folder_id: str) -> Path | None:
    """Copy the current file into the app's backup folder. Returns None if there was no file."""
    if not Path(file_path).is_file():
        return None
    target_dir = backup_dir_for(backups_root, folder_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"lootBlackWhitelist-{_stamp()}.json"
    n = 1
    while target.exists():
        target = target_dir / f"lootBlackWhitelist-{_stamp()}-{n}.json"
        n += 1
    shutil.copy2(file_path, target)
    return target


def list_backups(backups_root: Path, folder_id: str) -> list[Path]:
    d = backup_dir_for(backups_root, folder_id)
    return sorted(d.glob("lootBlackWhitelist-*.json"), reverse=True) if d.is_dir() else []


def _replace_atomically(file_path: Path, content: bytes) -> None:
    """Replace ``file_path`` in one step: the new content goes to a temporary file in the same
    folder (so the same drive), is flushed to disk, then swapped in with os.replace. An
    interruption leaves either the old file or the new one, never a partial file. The
    temporary file never outlives this call."""
    file_path = Path(file_path)
    fd, tmp = tempfile.mkstemp(prefix=f".{file_path.name}.", suffix=".tmp", dir=file_path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, file_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _write_verified(file_path: Path, data: dict) -> None:
    """Write atomically, then read back and compare."""
    _replace_atomically(file_path, serialize(data).encode("utf-8"))
    if read_file(file_path) != data:
        raise LootFileError(_("The written file did not read back identically."))


def _put_back(backup: Path, file_path: Path) -> None:
    """Restore a backup's exact bytes over ``file_path``, atomically."""
    _replace_atomically(file_path, Path(backup).read_bytes())


@dataclass
class InstallResult:
    backup: Path | None
    file_path: Path


def install(plan: InstallPlan, folder: CharacterFolder, backups_root: Path) -> InstallResult:
    """Back up, write, and verify. On a failed verification the backup is put back."""
    problems = validate(plan.new_data)
    if problems:
        raise LootFileError(" ".join(problems))
    # Re-read right before writing: the file must be unchanged since the preview.
    current = read_file(folder.file_path) if folder.file_path.is_file() else None
    if current != (folder.data if folder.exists else None):
        raise LootFileError(_("The loot file changed since the preview was made. Refresh and try again."))
    backup = create_backup(folder.file_path, backups_root, folder.folder_id)
    try:
        _write_verified(folder.file_path, plan.new_data)
    except Exception as e:
        if backup:
            _put_back(backup, folder.file_path)
            raise LootFileError(_("Install failed and the previous file was restored: {error}").format(error=e)) from e
        if plan.creates_file:
            try:
                folder.file_path.unlink()
            except FileNotFoundError:
                pass
            raise LootFileError(_("Install failed and the new file was removed: {error}").format(error=e)) from e
        raise
    return InstallResult(backup, folder.file_path)


def restore_backup(backup: Path, folder: CharacterFolder, backups_root: Path) -> Path | None:
    """Restore a backup over the character's loot file, backing up the current file first.
    If the restore can't be verified, the current file is put back."""
    data = read_file(backup)
    safety = create_backup(folder.file_path, backups_root, folder.folder_id)
    try:
        _write_verified(folder.file_path, data)
    except Exception as e:
        if safety:
            _put_back(safety, folder.file_path)
            raise LootFileError(_("Restore failed and your current file was kept: {error}").format(error=e)) from e
        raise
    return safety
