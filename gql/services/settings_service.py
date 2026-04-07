"""Service for reading autonomy and other app-level settings."""
import json
import os
from pathlib import Path

from db.enums import SuggestionOption, TaskCategory

_DATA_DIR = Path(os.environ.get("RENTMATE_DATA_DIR", str(Path(__file__).parent.parent.parent / "data")))
_SETTINGS_FILE = _DATA_DIR / "settings.json"
_DEFAULT_AUTONOMY = {c.value: "suggest" for c in TaskCategory}


def load_app_settings() -> dict:
    """Read the app settings JSON file."""
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {}


def get_autonomy_settings() -> dict:
    """Return the autonomy settings dict (category → level)."""
    return load_app_settings().get("autonomy", _DEFAULT_AUTONOMY)

_AUTONOMY_MODES: dict[str, tuple[str, str]] = {
    "manual":     ("manual",           "suggested"),
    "suggest":    ("waiting_approval", "suggested"),
    "autonomous": ("autonomous",       "active"),
}
_DEFAULT_MODE = _AUTONOMY_MODES["suggest"]


def get_autonomy_for_category(category: TaskCategory | str | None) -> str:
    """Return the autonomy level ('manual', 'suggest', or 'autonomous') for a category."""
    settings = get_autonomy_settings()
    return settings.get(category or "", "suggest")


def get_task_mode_for_category(category: str | None) -> tuple[str, str]:
    """Return (task_mode, task_status) for a category based on its autonomy level."""
    level = get_autonomy_for_category(category)
    return _AUTONOMY_MODES.get(level, _DEFAULT_MODE)


_DEFAULT_OPTIONS = [
    SuggestionOption(key="accept", label="Accept", action="accept_task", variant="default"),
    SuggestionOption(key="reject", label="Reject", action="reject_task", variant="ghost"),
]

_VENDOR_DRAFT_OPTIONS = [
    SuggestionOption(key="send", label="Send Message", action="approve_draft", variant="default"),
    SuggestionOption(key="edit", label="Edit Message", action="edit_draft", variant="outline"),
    SuggestionOption(key="skip", label="Do not send", action="reject_task", variant="ghost"),
]


def build_suggestion_options(
    autonomy: str,
    has_vendor_draft: bool = False,
) -> list[SuggestionOption]:
    """Return the action options for a suggested task based on context."""
    if has_vendor_draft and autonomy == "suggest":
        return list(_VENDOR_DRAFT_OPTIONS)
    return list(_DEFAULT_OPTIONS)
