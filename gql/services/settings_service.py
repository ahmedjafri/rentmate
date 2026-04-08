"""Service for reading and writing app-level settings from the database."""
import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

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


# ── LLM config ──────────────────────────────────────────────────────────────


_LLM_KEY = "llm"


def get_llm_settings() -> dict:
    """Return LLM settings: {api_key, model, base_url}.

    Prioritizes database settings over environment variables
    (LLM_API_KEY, LLM_MODEL, LLM_BASE_URL).
    """
    import os
    db_settings = get_setting(_LLM_KEY) or {}

    return {
        "api_key":  db_settings.get("api_key")  or os.environ.get("LLM_API_KEY", ""),
        "model":    db_settings.get("model")    or os.environ.get("LLM_MODEL", "openai/gpt-4o-mini"),
        "base_url": db_settings.get("base_url") or os.environ.get("LLM_BASE_URL", ""),
    }


def save_llm_settings(*, api_key: str | None = None, model: str | None = None, base_url: str | None = None) -> None:
    """Persist LLM settings to the database."""
    current = get_llm_settings()
    if api_key is not None:
        current["api_key"] = api_key
    if model is not None:
        current["model"] = model
    if base_url is not None:
        current["base_url"] = base_url
    set_setting(_LLM_KEY, value=current)


def load_llm_into_env() -> None:
    """Populate os.environ from DB-stored LLM settings (startup helper)."""
    import os
    settings = get_llm_settings()
    _ENV_MAP = {"api_key": "LLM_API_KEY", "model": "LLM_MODEL", "base_url": "LLM_BASE_URL"}
    for db_key, env_key in _ENV_MAP.items():
        val = settings.get(db_key)
        if val and not os.environ.get(env_key):
            os.environ[env_key] = val


# ── agent integrations ──────────────────────────────────────────────────────


_AGENT_INT_KEY = "agent_integrations"


def get_agent_integrations() -> dict:
    """Return agent integration settings: {web_search_enabled, brave_api_key}."""
    return get_setting(_AGENT_INT_KEY) or {}


def save_agent_integrations(*, brave_api_key: str | None = None, web_search_enabled: bool | None = None) -> None:
    """Persist agent integration settings."""
    current = get_agent_integrations()
    if brave_api_key is not None:
        current["brave_api_key"] = brave_api_key
    if web_search_enabled is not None:
        current["web_search_enabled"] = web_search_enabled
    set_setting(_AGENT_INT_KEY, value=current)


def load_agent_integrations_into_env() -> None:
    """Populate os.environ from DB-stored agent integration settings."""
    import os
    settings = get_agent_integrations()
    brave_key = settings.get("brave_api_key")
    if brave_key and not os.environ.get("BRAVE_API_KEY"):
        os.environ["BRAVE_API_KEY"] = brave_key


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


# ── onboarding ──────────────────────────────────────────────────────────────


_ONBOARDING_KEY = "onboarding"

_INITIAL_STEPS = {
    "configure_llm": "pending",
    "add_property": "pending",
    "upload_document": "pending",
    "tell_concerns": "pending",
}


def get_onboarding_state(db: Session) -> dict | None:
    """Read onboarding state for the current account. Returns None if not set."""
    row = db.query(AppSetting).filter_by(key=_ONBOARDING_KEY).first()
    if row and row.value:
        return json.loads(row.value)
    return None


def _upsert_onboarding(db: Session, *, state: dict) -> dict:
    """Write onboarding state (upsert)."""
    now = datetime.now(UTC)
    row = db.query(AppSetting).filter_by(key=_ONBOARDING_KEY).first()
    if row:
        row.value = json.dumps(state)
        row.updated_at = now
    else:
        db.add(AppSetting(key=_ONBOARDING_KEY, value=json.dumps(state), updated_at=now))
    db.flush()
    return state


def is_llm_configured() -> bool:
    """Return True if an LLM API key is set (env or DB)."""
    import os
    if os.environ.get("LLM_API_KEY"):
        return True
    llm = get_llm_settings()
    return bool(llm.get("api_key"))


def init_onboarding(db: Session) -> dict:
    """Create a fresh onboarding state and persist it."""
    steps = dict(_INITIAL_STEPS)
    if is_llm_configured():
        steps["configure_llm"] = "done"
    state = {
        "status": "active",
        "started_at": datetime.now(UTC).isoformat(),
        "dismissed_at": None,
        "path_picked": None,
        "steps": steps,
    }
    return _upsert_onboarding(db, state=state)


def update_onboarding_step(db: Session, *, step: str, status: str = "done") -> dict:
    """Mark an onboarding step as done (or another status). Auto-completes if all done."""
    state = get_onboarding_state(db) or init_onboarding(db)
    if step in state["steps"]:
        state["steps"][step] = status
    if all(v == "done" for v in state["steps"].values()):
        state["status"] = "completed"
    return _upsert_onboarding(db, state=state)


def set_onboarding_path(db: Session, *, path: str) -> dict:
    """Record which onboarding path the user chose."""
    state = get_onboarding_state(db) or init_onboarding(db)
    if not state.get("path_picked"):
        state["path_picked"] = path
    return _upsert_onboarding(db, state=state)


def dismiss_onboarding(db: Session) -> dict:
    """Mark onboarding as dismissed."""
    state = get_onboarding_state(db) or init_onboarding(db)
    state["status"] = "dismissed"
    state["dismissed_at"] = datetime.now(UTC).isoformat()
    return _upsert_onboarding(db, state=state)


# ── suggestion options ───────────────────────────────────────────────────


def build_suggestion_options(
    autonomy: str,
    has_vendor_draft: bool = False,
) -> list[SuggestionOption]:
    """Return the action options for a suggested task based on context."""
    if has_vendor_draft and autonomy == "suggest":
        return list(_VENDOR_DRAFT_OPTIONS)
    return list(_DEFAULT_OPTIONS)
