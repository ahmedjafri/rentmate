"""E2E eval: agent handles a maintenance issue starting from a tenant SMS.

Unlike ``eval_maintenance_basic.py`` (which pre-seeds a structured Task
with title / goal / steps), this scenario starts with the production
intake path: a tenant sends an SMS, ``route_inbound_to_task`` creates
the Task, and the agent has to figure out the rest from the message
alone — gather details, engage a plumber, surface cost to the owner,
and schedule the repair.

Higher ``max_turns`` than the seeded variant because the agent has no
preset progress steps to follow.

Run: ``poetry run pytest evals/eval_maintenance_intake.py -m eval -v``
"""
from __future__ import annotations

import pytest

from db.lib import route_inbound_to_task
from db.models import Conversation, Message, ParticipantType, Task
from evals.multi_actor_harness import (
    Actor,
    Goal,
    format_transcript,
    run_e2e_scenario,
)
from evals.personas import COOPERATIVE_VENDOR, FRIENDLY_TENANT, HANDS_ON_OWNER


def _agent_reached_owner(db, task: Task) -> bool:
    """True iff the agent sent at least one outreach message in an
    owner conversation linked to this task."""
    msg = (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(
            Conversation.parent_task_id == task.id,
            Conversation.conversation_type == "owner",
            Message.sender_type == ParticipantType.ACCOUNT_USER,
            Message.is_ai.is_(True),
        )
        .first()
    )
    return msg is not None


@pytest.mark.eval
def test_agent_handles_maintenance_intake_from_tenant_sms(
    db, scenario_builder, mock_sms, e2e_autonomous_mode,
):
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    park_plumbing = sb.add_vendor(vendor_type="Plumber", name="Park Plumbing Co")
    rivera_plumbing = sb.add_vendor(vendor_type="Plumber", name="Rivera Plumbing")
    owner = sb.add_owner(first_name="Patricia", last_name="Reyes")
    s = sb.build()
    property_name = s["property"].name
    tenant = s["tenant"]

    # Production intake path: a tenant SMS is the first thing on this task.
    inbound_body = (
        "Hi — there's a leak under our kitchen sink and water is starting "
        "to damage the cabinet floor. Can someone come look at it soon?"
    )
    conv, _msg = route_inbound_to_task(
        db,
        tenant=tenant,
        body=inbound_body,
        channel_type="sms",
        sender_meta={},
    )
    db.commit()

    task = (
        db.query(Task)
        .filter(Task.ai_conversation_id == conv.id)
        .one()
    )

    tenant_actor = Actor(
        name="tenant",
        user=tenant.user,
        persona=FRIENDLY_TENANT,
        conversation_type="tenant",
        property_name=property_name,
    )
    park_actor = Actor(
        name="park_plumbing",
        user=park_plumbing,
        persona=COOPERATIVE_VENDOR,
        conversation_type="vendor",
        property_name=property_name,
    )
    rivera_actor = Actor(
        name="rivera_plumbing",
        user=rivera_plumbing,
        persona=COOPERATIVE_VENDOR,
        conversation_type="vendor",
        property_name=property_name,
    )
    owner_actor = Actor(
        name="owner",
        user=owner,
        persona=HANDS_ON_OWNER,
        conversation_type="owner",
        property_name=property_name,
    )

    result = run_e2e_scenario(
        db,
        task=task,
        actors=[tenant_actor, park_actor, rivera_actor, owner_actor],
        goal=Goal(
            description=(
                "Starting from a tenant SMS about a kitchen sink leak, "
                "the agent gathers details, reaches a plumber, surfaces "
                "the cost to the owner for approval, and schedules the "
                "repair. The owner conversation is reached after a "
                "vendor quote is on the record."
            ),
            predicate=_agent_reached_owner,
        ),
        max_turns=12,
    )

    assert result.success, (
        f"Agent did not complete the intake-driven maintenance flow with "
        f"owner approval. turns={result.turns_taken}, "
        f"terminated_by={result.terminated_by}. "
        f"Reason: {result.judge_reason}\n\n"
        f"Transcript:\n{format_transcript(result.transcript)}"
    )
    assert result.turns_taken <= 12
