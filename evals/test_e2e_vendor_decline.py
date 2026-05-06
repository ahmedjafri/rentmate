"""E2E eval: agent re-routes after a vendor declines.

The first vendor (REFUSING_VENDOR) politely turns down the work. The
agent should pick up the decline signal and reach out to a second
available vendor — visible as a fresh outreach message in a different
vendor conversation linked to the same task.

Run: ``poetry run pytest evals/test_e2e_vendor_decline.py -m eval -v``
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
from evals.personas import COOPERATIVE_VENDOR, REFUSING_VENDOR
from services.task_service import TaskProgressStep


def _agent_reached_second_vendor(db, task: Task) -> bool:
    """True iff the agent sent outreach to two distinct vendor conversations."""
    rows = (
        db.query(Conversation.id)
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(
            Conversation.parent_task_id == task.id,
            Conversation.conversation_type == "vendor",
            Message.sender_type == ParticipantType.ACCOUNT_USER,
            Message.is_ai.is_(True),
        )
        .distinct()
        .all()
    )
    return len({cid for (cid,) in rows}) >= 2


@pytest.mark.eval
def test_agent_reroutes_after_vendor_declines(
    db, scenario_builder, mock_sms, e2e_autonomous_mode,
):
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    refusing_vendor = sb.add_vendor(vendor_type="Plumber", name="Rivera Plumbing")
    cooperative_vendor = sb.add_vendor(
        vendor_type="Plumber", name="Park Plumbing Co",
    )
    sb.add_task(
        title="Kitchen sink leak under cabinet",
        category="maintenance",
        urgency="medium",
        context_body=(
            "Tenant reports water pooling under the kitchen sink. Needs a "
            "plumber to assess and repair. Two plumbers are on file: "
            "Rivera Plumbing and Park Plumbing Co."
        ),
        goal=(
            "Get a plumber to assess and repair the leak. If the first "
            "plumber declines, reach out to a second one promptly."
        ),
        steps=[
            TaskProgressStep(
                key="contact_vendor",
                label="Reach out to a plumber",
                status=TaskStepStatus.ACTIVE,
            ),
            TaskProgressStep(
                key="schedule_visit",
                label="Schedule a visit",
                status=TaskStepStatus.PENDING,
            ),
            TaskProgressStep(
                key="complete_repair",
                label="Complete the repair",
                status=TaskStepStatus.PENDING,
            ),
        ],
    )
    s = sb.build()
    property_name = s["property"].name

    refusing_actor = Actor(
        name="rivera_plumbing",
        user=refusing_vendor,
        persona=REFUSING_VENDOR,
        conversation_type="vendor",
        property_name=property_name,
    )
    cooperative_actor = Actor(
        name="park_plumbing",
        user=cooperative_vendor,
        persona=COOPERATIVE_VENDOR,
        conversation_type="vendor",
        property_name=property_name,
    )

    result = run_e2e_scenario(
        db,
        task=s["task"],
        actors=[refusing_actor, cooperative_actor],
        goal=Goal(
            description=(
                "After the first plumber declines the work, the agent "
                "reaches out to the second plumber within the same task."
            ),
            predicate=_agent_reached_second_vendor,
        ),
        max_turns=6,
    )

    assert result.success, (
        f"Agent did not re-route after the first vendor declined. "
        f"turns={result.turns_taken}, terminated_by={result.terminated_by}. "
        f"Reason: {result.judge_reason}\n\n"
        f"Transcript:\n{format_transcript(result.transcript)}"
    )
    assert result.turns_taken <= 6
