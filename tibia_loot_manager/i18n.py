"""Localization hook.

All user-facing strings go through ``_()``. English is the source language; a
translation can be added later by dropping a compiled gettext catalog into
``locale/<lang>/LC_MESSAGES/tibia_loot_manager.mo``.
"""

import gettext
from pathlib import Path

_LOCALE_DIR = Path(__file__).parent / "locale"
_translation = gettext.translation("tibia_loot_manager", localedir=_LOCALE_DIR, fallback=True)

_ = _translation.gettext
ngettext = _translation.ngettext
