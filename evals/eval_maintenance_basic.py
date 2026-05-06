"""E2E eval: agent runs a basic maintenance task end-to-end.

A tenant reports a kitchen sink leak. The agent should gather details
from the tenant, reach out to a plumber, surface the cost to the owner
for approval, and schedule the repair. This exercises all three
stakeholder types (tenant, vendor, owner) in one task.

Run: ``poetry run pytest evals/eval_maintenance_basic.py -m eval -v``
"""
from __future__ import annotations

import pytest

from db.enums import TaskStepStatus
from db.models import Conversation, Message, ParticipantType, Task
from evals.multi_actor_harness import (
    Actor,
    Goal,
    format_transcript,
    run_e2e_scenario,
)
from evals.personas import COOPERATIVE_VENDOR, FRIENDLY_TENANT, HANDS_ON_OWNER
from services.task_service import TaskProgressStep


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
def test_agent_handles_basic_maintenance_with_owner_approval(
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
    sb.add_task(
        title="Kitchen sink leak — water damage starting",
        category="maintenance",
        urgency="high",
        context_body=(
            "Tenant reports a leak under the kitchen sink."
        ),
        goal=(
            "Gather details from the tenant, get a quote from a plumber, "
            "surface the cost to the owner for approval, and schedule "
            "the repair once approved."
        ),
        steps=[
            TaskProgressStep(
                key="contact_tenant",
                label="Get details from tenant",
                status=TaskStepStatus.ACTIVE,
            ),
            TaskProgressStep(
                key="engage_vendor",
                label="Get a plumber quote",
                status=TaskStepStatus.PENDING,
            ),
            TaskProgressStep(
                key="get_owner_approval",
                label="Get owner approval on cost",
                status=TaskStepStatus.PENDING,
            ),
            TaskProgressStep(
                key="schedule_repair",
                label="Schedule the repair",
                status=TaskStepStatus.PENDING,
            ),
            TaskProgressStep(
                key="confirm_complete",
                label="Confirm repair complete",
                status=TaskStepStatus.PENDING,
            ),
        ],
    )
    s = sb.build()
    property_name = s["property"].name

    tenant_actor = Actor(
        name="tenant",
        user=s["tenant"].user,
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
        task=s["task"],
        actors=[tenant_actor, park_actor, rivera_actor, owner_actor],
        goal=Goal(
            description=(
                "Agent gathers details from the tenant, reaches out to a "
                "plumber, surfaces the cost to the owner for approval, "
                "and schedules the repair. The owner conversation is "
                "reached after a vendor quote is on the record."
            ),
            predicate=_agent_reached_owner,
        ),
        max_turns=10,
    )

    assert result.success, (
        f"Agent did not complete the maintenance flow with owner "
        f"approval. turns={result.turns_taken}, "
        f"terminated_by={result.terminated_by}. "
        f"Reason: {result.judge_reason}\n\n"
        f"Transcript:\n{format_transcript(result.transcript)}"
    )
    assert result.turns_taken <= 10
