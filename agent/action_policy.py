"""Policy-first action gating for tool choice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.settings_service import (
    entity_change_confidence_threshold,
    get_action_policy_settings,
    outbound_message_allows_risk,
    should_prefer_suggestion_when_uncertain,
)

ActionClass = Literal["read", "entity_change", "outbound_message", "suggestion"]


@dataclass
class ActionCandidate:
    action_class: ActionClass
    action_name: str
    confidence: float | None = None
    risk_level: str | None = None


@dataclass
class ActionDecision:
    allowed: bool
    reason: str
    preferred_rank: int


_ACTION_RANK: dict[ActionClass, int] = {
    "read": 0,
    "entity_change": 1,
    "outbound_message": 2,
    "suggestion": 3,
}


def evaluate_action_candidate(candidate: ActionCandidate) -> ActionDecision:
    settings = get_action_policy_settings()

    if candidate.action_class == "read":
        return ActionDecision(True, "read actions are always allowed", _ACTION_RANK["read"])

    if candidate.action_class == "entity_change":
        confidence = candidate.confidence if candidate.confidence is not None else 1.0
        threshold = entity_change_confidence_threshold(settings["entity_changes"])
        allowed = confidence >= threshold
        return ActionDecision(
            allowed=allowed,
            reason=(
                f"confidence {confidence:.2f} meets threshold {threshold:.2f}"
                if allowed else
                f"confidence {confidence:.2f} below threshold {threshold:.2f}"
            ),
            preferred_rank=_ACTION_RANK["entity_change"],
        )

    if candidate.action_class == "outbound_message":
        risk_level = (candidate.risk_level or "medium").lower()
        allowed = outbound_message_allows_risk(risk_level, settings["outbound_messages"])
        return ActionDecision(
            allowed=allowed,
            reason=(
                f"risk level {risk_level} allowed by outbound policy"
                if allowed else
                f"risk level {risk_level} blocked by outbound policy"
            ),
            preferred_rank=_ACTION_RANK["outbound_message"],
        )

    if candidate.action_class == "suggestion":
        return ActionDecision(
            allowed=True,
            reason=(
                "suggestion fallback preferred under uncertainty"
                if should_prefer_suggestion_when_uncertain(settings["suggestion_fallback"])
                else "suggestion fallback allowed only after higher-priority actions are blocked"
            ),
            preferred_rank=_ACTION_RANK["suggestion"],
        )

    return ActionDecision(False, "unknown action class", 99)

