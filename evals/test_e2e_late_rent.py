"""E2E eval: agent collects a late-rent commitment from an evasive tenant.

The tenant persona dodges concrete dates ("soon", "as soon as I can").
The agent should keep gently pinning down a real commitment without
escalating to lease termination.

Run: ``poetry run pytest evals/test_e2e_late_rent.py -m eval -v``
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from db.enums import TaskStepStatus
from db.models import Conversation, Message, MessageType, ParticipantType, Task
from evals.multi_actor_harness import (
    Actor,
    Goal,
    format_transcript,
    run_e2e_scenario,
)
from evals.personas import EVASIVE_TENANT
from services.task_service import TaskProgressStep


_DATE_COMMITMENT_PATTERNS = [
    r"\bby (mon|tue|wed|thu|fri|sat|sun)\w*\b",
    r"\bnext (mon|tue|wed|thu|fri|sat|sun)\w*\b",
    r"\bon (mon|tue|wed|thu|fri|sat|sun)\w*\b",
    r"\bby (the )?\d{1,2}(st|nd|rd|th)?\b",
    r"\bon (the )?\d{1,2}(st|nd|rd|th)?\b",
    r"\b\d{1,2}/\d{1,2}\b",
    r"\btomorrow\b",
    r"\btonight\b",
    r"\bthis (afternoon|evening|weekend|week)\b",
    r"\bnext week\b",
    r"\bin \d+ days?\b",
]


def _tenant_committed_to_concrete_date(db, task: Task) -> bool:
    """Heuristic: did the tenant mention a date-bearing phrase in their
    most-recent reply on the tenant conversation?"""
    msg = (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(
            Conversation.parent_task_id == task.id,
            Conversation.conversation_type == "tenant",
            Message.sender_type == ParticipantType.TENANT,
            Message.message_type == MessageType.MESSAGE,
        )
        .order_by(Message.sent_at.desc())
        .first()
    )
    if msg is None or not (msg.body or "").strip():
        return False
    body = msg.body.lower()
    return any(re.search(p, body) for p in _DATE_COMMITMENT_PATTERNS)


@pytest.mark.eval
def test_late_rent_evasive_tenant_eventually_commits(
    db, scenario_builder, mock_sms, e2e_autonomous_mode,
):
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease(payment_status="late")
    rent_due_label = (datetime.now(UTC) - timedelta(days=14)).strftime("%b %-d")
    sb.add_task(
        title="Tenant 14 days late on rent",
        category="payment",
        urgency="high",
        context_body=(
            f"Rent for this lease was due on {rent_due_label} and remains "
            "unpaid 14 days later. Tenant has not yet acknowledged the "
            "balance. Reach out to find out when they can pay."
        ),
        goal=(
            "Get a concrete payment date or partial-payment commitment from "
            "the tenant. Stay empathetic; do not threaten lease termination."
        ),
        steps=[
            TaskProgressStep(
                key="contact_tenant",
                label="Contact tenant about late rent",
                status=TaskStepStatus.ACTIVE,
            ),
            TaskProgressStep(
                key="get_commitment",
                label="Extract a concrete payment date",
                status=TaskStepStatus.PENDING,
            ),
        ],
    )
    s = sb.build()

    tenant_actor = Actor(
        name="tenant",
        user=s["tenant"].user,
        persona=EVASIVE_TENANT,
        conversation_type="tenant",
        property_name=s["property"].name,
    )

    result = run_e2e_scenario(
        db,
        task=s["task"],
        actors=[tenant_actor],
        goal=Goal(
            description=(
                "Agent extracts a concrete payment date or partial-payment "
                "commitment from the tenant without threatening lease "
                "termination."
            ),
            predicate=_tenant_committed_to_concrete_date,
        ),
        max_turns=6,
    )

    assert result.success, (
        f"Goal not met after {result.turns_taken} turns "
        f"(terminated_by={result.terminated_by}). "
        f"Reason: {result.judge_reason}\n\n"
        f"Transcript:\n{format_transcript(result.transcript)}"
    )
    assert result.turns_taken <= 6
