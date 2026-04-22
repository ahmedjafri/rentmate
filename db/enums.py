"""Shared enums and domain types for Task and Suggestion models."""

import enum
from dataclasses import dataclass


class TaskCategory(str, enum.Enum):
    RENT = "rent"
    MAINTENANCE = "maintenance"
    LEASING = "leasing"
    COMPLIANCE = "compliance"
    OTHER = "other"


class Urgency(int, enum.Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


def parse_urgency(value: "Urgency | str | int | None") -> "Urgency | None":
    if value is None or value == "":
        return None
    if isinstance(value, Urgency):
        return value
    if isinstance(value, int):
        return Urgency(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.isdigit():
            return Urgency(int(normalized))
        return Urgency[normalized.upper()]
    raise ValueError(f"Unsupported urgency value: {value!r}")


class TaskStatus(int, enum.Enum):
    SUGGESTED = 1
    ACTIVE = 2
    PAUSED = 3
    RESOLVED = 4
    DISMISSED = 5


class TaskMode(int, enum.Enum):
    MANUAL = 1
    WAITING_APPROVAL = 2
    AUTONOMOUS = 3


def parse_task_mode(value: "TaskMode | str | int | None") -> "TaskMode | None":
    if value is None or value == "":
        return None
    if isinstance(value, TaskMode):
        return value
    if isinstance(value, int):
        return TaskMode(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.isdigit():
            return TaskMode(int(normalized))
        return TaskMode[normalized.upper()]
    raise ValueError(f"Unsupported task mode value: {value!r}")


class TaskPriority(int, enum.Enum):
    ROUTINE = 1
    URGENT = 2


class ChannelType(int, enum.Enum):
    SMS = 1
    EMAIL = 2


class TaskSource(str, enum.Enum):
    MANUAL = "manual"
    AI_SUGGESTION = "ai_suggestion"
    AUTOMATION = "automation"
    AGENT = "agent"
    DOCUMENT = "document"
    TENANT_REPORT = "tenant_report"
    DEV_SIM = "dev_sim"


class SuggestionSourceEnum(str, enum.Enum):
    AUTOMATION = "automation"
    AGENT = "agent"


class SuggestionStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


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
    action: str      # value passed to act_on_suggestion (e.g. "send_and_create_task")
    variant: str     # UI style: "default", "outline", "ghost"
