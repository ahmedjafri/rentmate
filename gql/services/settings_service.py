"""Service for reading and writing app-level settings from the database."""
import json
from datetime import UTC, datetime

from db.enums import SuggestionOption, TaskCategory
from db.models import AppSetting
from db.models.base import Base
from db.session import SessionLocal, engine

# Ensure app_settings table exists (may not if DB was created before this model)
Base.metadata.create_all(engine, tables=[AppSetting.__table__], checkfirst=True)

_DEFAULT_AUTONOMY = {c.value: "suggest" for c in TaskCategory}


# ── generic get/set ──────────────────────────────────────────────────────────


def get_setting(key: str) -> dict | None:
    """Read a setting by key. Returns parsed JSON or None."""
    db = SessionLocal.session_factory()
    try:
        row = db.query(AppSetting).filter_by(key=key).first()
        if row and row.value:
            return json.loads(row.value)
        return None
    finally:
        db.close()


def set_setting(key: str, *, value: dict) -> None:
    """Write a setting by key (upsert)."""
    db = SessionLocal.session_factory()
    try:
        row = db.query(AppSetting).filter_by(key=key).first()
        now = datetime.now(UTC)
        if row:
            row.value = json.dumps(value)
            row.updated_at = now
        else:
            db.add(AppSetting(key=key, value=json.dumps(value), updated_at=now))
        db.commit()
    finally:
        db.close()


def load_app_settings() -> dict:
    """Read all settings as a merged dict."""
    db = SessionLocal.session_factory()
    try:
        rows = db.query(AppSetting).all()
        result = {}
        for row in rows:
            if row.value:
                try:
                    result[row.key] = json.loads(row.value)
                except Exception:
                    result[row.key] = row.value
        return result
    finally:
        db.close()


def save_app_settings(data: dict) -> None:
    """Write multiple settings at once (merge with existing)."""
    for key, value in data.items():
        set_setting(key, value=value)


# ── autonomy ─────────────────────────────────────────────────────────────────


def get_autonomy_settings() -> dict:
    """Return the autonomy settings dict (category → level)."""
    return get_setting("autonomy") or dict(_DEFAULT_AUTONOMY)


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


# ── integrations ─────────────────────────────────────────────────────────────


def get_integrations() -> dict:
    """Return all integration settings."""
    return get_setting("integrations") or {}


def save_integrations(data: dict) -> None:
    """Save integration settings."""
    set_setting("integrations", value=data)


# ── suggestion options ───────────────────────────────────────────────────────


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
