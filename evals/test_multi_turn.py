"""Eval: Multi-turn conversation flows.

Tests scenarios that require multiple agent turns with simulated
external replies in between.
"""

import pytest

from db.enums import TaskStepStatus
from db.models import (
    ConversationType,
    ParticipantType,
)
from evals.conftest import (
    add_message,
    get_suggestions,
    run_turn_sync,
)
from services.task_service import TaskProgressStep, dump_task_steps


@pytest.mark.eval
class TestVendorNegotiation:
    """Multi-turn vendor negotiation flow."""

    def test_vendor_quote_too_high_agent_negotiates(self, db, scenario_builder, mock_sms, autonomous_mode):
        """When vendor quote seems high, agent should handle diplomatically."""
        sb = scenario_builder
        sb.add_property(address="500 River Rd")
        sb.add_unit()
        sb.add_tenant(first_name="Tom", last_name="Owner")
        sb.add_lease()
        vendor = sb.add_vendor(name="Premium Plumbing", vendor_type="Plumber", phone="206-555-PREM")
        task = sb.add_task(
            title="Water heater replacement",
            category="maintenance", urgency="high",
            context_body="Water heater is leaking and needs replacement.",
        )
        s = sb.build()

        # Setup vendor conversation with quote
        from services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db, conversation_type=ConversationType.VENDOR,
            subject="Water heater", vendor_id=str(vendor.id),
            property_id=s["property"].id,
            parent_task_id=task.id,
        )
        db.flush()

        add_message(db, vendor_conv.id, "RentMate",
                    "Water heater is leaking at 500 River Rd. Can you assess and quote?",
                    ParticipantType.ACCOUNT_USER, is_ai=True)
        add_message(db, vendor_conv.id, "Premium Plumbing",
                    "Looked at it. Need full replacement. Quote: $4,200 installed.",
                    ParticipantType.EXTERNAL_CONTACT)

        result = run_turn_sync(
                db, task,
                "Premium Plumbing quoted $4,200 for water heater replacement. Review and respond.",
            )

        reply = result["reply"].lower()
        suggestions = get_suggestions(db, task.id)
        suggestion_text = " ".join(
            ((s.title or "") + " " + (s.body or "")).lower()
            for s in suggestions
        )

        # Should get a second quote and escalate the expensive quote to manager review.
        assert "second quote" in reply or "second quote" in suggestion_text or "review $4,200" in suggestion_text, \
            f"Should pursue comparison or review on expensive quote: {result['reply'][:200]}"
        assert "approv" in reply or "manager" in reply or "review" in suggestion_text or "$4,200" in suggestion_text, \
            f"Should escalate expensive quote for manager review: {result['reply'][:200]}"


@pytest.mark.eval
class TestRepeatReports:
    """Handling duplicate/repeat maintenance reports."""

    def test_repeat_report_acknowledged_not_duplicated(self, db, scenario_builder, mock_sms, autonomous_mode):
        """If tenant reports the same issue again, agent should acknowledge previous report."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Uma", last_name="Repeat")
        sb.add_lease()
        sb.add_vendor(name="Plumber Joe", vendor_type="Plumber", phone="206-555-JOE1")
        task = sb.add_task(
            title="Sink leak - unit A",
            category="maintenance", urgency="medium",
            context_body="Tenant reports kitchen sink leak.",
        )
        s = sb.build()

        # Add previous context
        task.context = "[2026-04-05] Tenant first reported sink leak. Vendor contacted."
        task.steps = dump_task_steps([
            TaskProgressStep(key="vendor_assess", label="Vendor assessment", status=TaskStepStatus.ACTIVE),
            TaskProgressStep(key="repair", label="Complete repair", status=TaskStepStatus.PENDING),
        ])
        db.flush()

        result = run_turn_sync(
                db, task,
                "I reported the sink leak 2 days ago and nobody has come yet! "
                "The water damage is getting worse.",
            )

        reply = result["reply"]
        # Should acknowledge they already reported it
        assert len(reply) > 30, "Should provide substantive response to repeat report"
        # Should not say "I'll create a new task"
        reply_lower = reply.lower()
        assert "new task" not in reply_lower, \
            f"Should not create new task for repeat report: {result['reply'][:200]}"


@pytest.mark.eval
class TestTenantFollowUp:
    """Tenant follow-up on existing tasks."""

    def test_status_update_when_asked(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Tenant asks for status update — agent should provide one based on task state."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Vera", last_name="WantsUpdate")
        sb.add_lease()
        sb.add_vendor(name="AC Pro", vendor_type="HVAC", phone="206-555-AC01")
        task = sb.add_task(
            title="AC repair - Unit A",
            category="maintenance", urgency="medium",
            context_body="AC not cooling properly.",
        )
        s = sb.build()

        task.context = "[2026-04-05] AC Pro assigned. Scheduled for Monday."
        task.steps = dump_task_steps([
            TaskProgressStep(key="schedule", label="Schedule HVAC tech", status=TaskStepStatus.DONE, note="Monday appointment"),
            TaskProgressStep(key="repair", label="Complete AC repair", status=TaskStepStatus.PENDING),
        ])
        db.flush()

        result = run_turn_sync(
                db, task,
                "Any update on my AC repair? It's been a few days.",
            )

        reply = result["reply"].lower()
        # Should reference the scheduled appointment
        assert "monday" in reply or "scheduled" in reply or "appointment" in reply, \
            f"Should reference existing appointment: {result['reply'][:200]}"
