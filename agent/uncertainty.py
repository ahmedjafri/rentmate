"""Confidence-based gating for commit-style agent actions.

Every gated tool requires a ``confidence`` score (0.0–1.0) on each call.
Each tool maps to one of the three action-policy fields
(``entity_changes``, ``outbound_messages``, ``task_suggestion_creation``);
that field's level decides the minimum confidence the call must clear.
Calls below the threshold are intercepted in ``agent.dispatch`` and the
agent is told to ``ask_manager`` first.

Adding a new gated tool is one line in ``_TOOL_POLICY_MAP`` plus a
``confidence`` parameter on the tool's schema — no other plumbing.
"""
from __future__ import annotations

from db.enums import ActionPolicyLevel
from services.settings_service import get_action_policy_settings

# Same scale across all three policies — confidence is a domain-neutral
# measure of "how sure the agent is", so the dial values shouldn't depend
# on which family of action is being gated.
_CONFIDENCE_THRESHOLDS: dict[ActionPolicyLevel, float] = {
    ActionPolicyLevel.AGGRESSIVE: 0.3,
    ActionPolicyLevel.BALANCED: 0.6,
    ActionPolicyLevel.STRICT: 0.8,
}

# tool name → action-policy field that controls its confidence threshold.
_TOOL_POLICY_MAP: dict[str, str] = {
    # Task & suggestion creation
    "create_suggestion": "task_suggestion_creation",
    "propose_task": "task_suggestion_creation",
    # Outbound communication
    "message_person": "outbound_messages",
    # Entity creation / mutation
    "create_property": "entity_changes",
    "create_tenant": "entity_changes",
    "create_lease": "entity_changes",
    "update_lease": "entity_changes",
    "create_vendor": "entity_changes",
}

GATED_TOOLS: frozenset[str] = frozenset(_TOOL_POLICY_MAP)

# Treat a missing confidence as middling so the agent is nudged to
# declare it explicitly under STRICT (0.8 threshold blocks 0.5) but
# AGGRESSIVE (0.3) still lets work through if the agent forgets.
_DEFAULT_CONFIDENCE = 0.5


def confidence_threshold_for(policy_field: str) -> float:
    """Minimum confidence required by the given policy field's current level."""
    settings = get_action_policy_settings()
    level = settings.get(policy_field, ActionPolicyLevel.BALANCED)
    return _CONFIDENCE_THRESHOLDS[level]


def _coerce_confidence(value: object) -> float:
    if value is None:
        return _DEFAULT_CONFIDENCE
    try:
        score = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_CONFIDENCE
    if score != score:  # NaN
        return _DEFAULT_CONFIDENCE
    return max(0.0, min(1.0, score))


def check_action_confidence(*, tool_name: str, args: dict) -> str | None:
    """Return an error message when the call should be blocked, else None.

    Tools outside ``_TOOL_POLICY_MAP`` always pass. For gated tools,
    missing confidence is treated as ``_DEFAULT_CONFIDENCE`` so the agent
    can't bypass the gate by simply omitting the argument.
    """
    policy_field = _TOOL_POLICY_MAP.get(tool_name)
    if policy_field is None:
        return None
    score = _coerce_confidence(args.get("confidence"))
    threshold = confidence_threshold_for(policy_field)
    if score >= threshold:
        return None
    return (
        f"Action {tool_name!r} blocked by {policy_field} policy: confidence "
        f"{score:.2f} is below the current threshold {threshold:.2f}. "
        "Call `ask_manager` to resolve what's unclear (e.g. ambiguous goal, "
        "missing entity, unclear next step), then retry with a higher "
        "confidence once the gap is closed."
    )
