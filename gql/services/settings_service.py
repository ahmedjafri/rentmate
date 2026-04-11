"""Service for reading and writing app-level settings from the database."""
import json
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from db.enums import SuggestionOption, TaskCategory
from db.models import AppSetting
from db.models.base import Base
from db.session import SessionLocal, engine

# Ensure app_settings table exists (may not if DB was created before this model)
Base.metadata.create_all(engine, tables=[AppSetting.__table__], checkfirst=True)

ActionPolicyLevel = Literal["strict", "balanced", "aggressive"]

_DEFAULT_ACTION_POLICY: dict[str, ActionPolicyLevel] = {
    "entity_changes": "balanced",
    "outbound_messages": "balanced",
    "suggestion_fallback": "balanced",
}


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


# ── action policy ────────────────────────────────────────────────────────────


def get_action_policy_settings() -> dict[str, ActionPolicyLevel]:
    """Return the action-policy settings dict."""
    stored = get_setting("action_policy") or {}
    return {
        "entity_changes": stored.get("entity_changes", _DEFAULT_ACTION_POLICY["entity_changes"]),
        "outbound_messages": stored.get("outbound_messages", _DEFAULT_ACTION_POLICY["outbound_messages"]),
        "suggestion_fallback": stored.get("suggestion_fallback", _DEFAULT_ACTION_POLICY["suggestion_fallback"]),
    }


def save_action_policy_settings(data: dict[str, ActionPolicyLevel]) -> None:
    """Persist action-policy settings."""
    current = get_action_policy_settings()
    current.update({k: v for k, v in data.items() if v is not None})
    set_setting("action_policy", value=current)


_ENTITY_CONFIDENCE_THRESHOLDS: dict[ActionPolicyLevel, float] = {
    "strict": 0.9,
    "balanced": 0.75,
    "aggressive": 0.6,
}

_MESSAGE_RISK_ALLOWLIST: dict[ActionPolicyLevel, set[str]] = {
    "strict": {"low"},
    "balanced": {"low", "medium"},
    "aggressive": {"low", "medium", "high"},
}

_SUGGESTION_FALLBACK_ALLOWANCE: dict[ActionPolicyLevel, str] = {
    "strict": "prefer_suggestion_when_uncertain",
    "balanced": "use_suggestion_when_blocked_or_ambiguous",
    "aggressive": "use_suggestion_only_when_blocked",
}


def get_entity_change_policy_level() -> ActionPolicyLevel:
    return get_action_policy_settings()["entity_changes"]


def get_outbound_message_policy_level() -> ActionPolicyLevel:
    return get_action_policy_settings()["outbound_messages"]


def get_suggestion_fallback_policy_level() -> ActionPolicyLevel:
    return get_action_policy_settings()["suggestion_fallback"]


def entity_change_confidence_threshold(level: ActionPolicyLevel | None = None) -> float:
    return _ENTITY_CONFIDENCE_THRESHOLDS[level or get_entity_change_policy_level()]


def outbound_message_allows_risk(risk_level: str, level: ActionPolicyLevel | None = None) -> bool:
    normalized = (risk_level or "medium").strip().lower()
    if normalized == "critical":
        return False
    return normalized in _MESSAGE_RISK_ALLOWLIST[level or get_outbound_message_policy_level()]


def should_prefer_suggestion_when_uncertain(level: ActionPolicyLevel | None = None) -> bool:
    return (level or get_suggestion_fallback_policy_level()) == "strict"


def get_action_policy_summary() -> dict[str, str | float]:
    settings = get_action_policy_settings()
    return {
        "entity_changes": settings["entity_changes"],
        "entity_confidence_threshold": entity_change_confidence_threshold(settings["entity_changes"]),
        "outbound_messages": settings["outbound_messages"],
        "suggestion_fallback": settings["suggestion_fallback"],
        "suggestion_fallback_mode": _SUGGESTION_FALLBACK_ALLOWANCE[settings["suggestion_fallback"]],
    }


# Temporary compatibility helpers while the old category-autonomy model is removed.
def get_autonomy_settings() -> dict:
    return {c.value: "suggest" for c in TaskCategory}


def get_autonomy_for_category(category: TaskCategory | str | None) -> str:
    return "suggest"


def get_task_mode_for_category(category: str | None) -> tuple[str, str]:
    return ("waiting_approval", "suggested")


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
    SuggestionOption(key="send", label="Create Task", action="send_and_create_task", variant="default"),
    SuggestionOption(key="reject", label="Reject", action="reject_task", variant="ghost"),
]

_VENDOR_DRAFT_OPTIONS = [
    SuggestionOption(key="send", label="Send Message", action="send_and_create_task", variant="default"),
    SuggestionOption(key="edit", label="Edit Message", action="edit_message", variant="outline"),
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
