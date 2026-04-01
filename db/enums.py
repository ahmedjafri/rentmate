"""Shared enums and domain types for Task and Suggestion models."""

import enum
from dataclasses import dataclass


class TaskCategory(str, enum.Enum):
    RENT = "rent"
    MAINTENANCE = "maintenance"
    LEASING = "leasing"
    COMPLIANCE = "compliance"
    OTHER = "other"


class Urgency(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskSource(str, enum.Enum):
    MANUAL = "manual"
    AI_SUGGESTION = "ai_suggestion"
    AUTOMATION = "automation"
    AGENT = "agent"
    DOCUMENT = "document"
    TENANT_REPORT = "tenant_report"
    DEV_SIM = "dev_sim"


# ─── Suggestion source (union) ───────────────────────────────────────────────

@dataclass(frozen=True)
class AutomationSource:
    """Suggestion created by an automation rule."""
    automation_key: str


@dataclass(frozen=True)
class AgentSource:
    """Suggestion created by the AI agent."""
    pass


SuggestionSource = AutomationSource | AgentSource


# ─── Suggestion options ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class SuggestionOption:
    """A single action button rendered in the suggestion UI."""
    key: str
    label: str
    action: str      # value passed to act_on_suggestion (e.g. "accept_task")
    variant: str     # UI style: "default", "outline", "ghost"
