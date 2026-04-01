"""Service for reading autonomy and other app-level settings.

Keeps the handler layer from reaching into settings internals directly.
"""
from db.enums import TaskCategory, SuggestionOption

_AUTONOMY_MODES: dict[str, tuple[str, str]] = {
    "manual":     ("manual",           "suggested"),
    "suggest":    ("waiting_approval", "suggested"),
    "autonomous": ("autonomous",       "active"),
}
_DEFAULT_MODE = _AUTONOMY_MODES["suggest"]


def get_autonomy_for_category(category: TaskCategory | str | None) -> str:
    """Return the autonomy level ('manual', 'suggest', or 'autonomous') for a category."""
    from handlers.settings import get_autonomy_settings
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
