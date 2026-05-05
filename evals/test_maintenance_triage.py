"""Eval: Maintenance request triage.

Tests the agent's ability to classify urgency, coordinate vendors, and
follow proper tenant/vendor communication protocols. Uses the production
task-review path (`run_review`) and the seeded ScenarioBuilder so each
test gets fresh randomized names/phones/addresses.
"""

import pytest

from db.enums import TaskStepStatus
from evals.conftest import (
    assert_no_pii_leak,
    get_suggestions,
    get_tool_calls,
    run_review,
)
from services.task_service import TaskProgressStep


def _tenant_full_name(tenant) -> str:
    user = tenant.user
    return f"{user.first_name} {user.last_name}".strip()


def _maintenance_steps() -> list[TaskProgressStep]:
    """Four-step skeleton most maintenance-triage scenarios share."""
    return [
        TaskProgressStep(
            key="contact_vendor",
            label="Contact a vendor to assess the issue",
            status=TaskStepStatus.ACTIVE,
        ),
        TaskProgressStep(
            key="schedule_visit",
            label="Schedule the visit with the tenant",
            status=TaskStepStatus.PENDING,
        ),
        TaskProgressStep(
            key="complete_work",
            label="Complete the repair work",
            status=TaskStepStatus.PENDING,
        ),
        TaskProgressStep(
            key="verify_fixed",
            label="Confirm with the tenant the issue is resolved",
            status=TaskStepStatus.PENDING,
        ),
    ]


@pytest.mark.eval
class TestEmergencyTriage:
    """True emergencies must be escalated fast and correctly."""

    def test_burst_pipe_escalates_immediately(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Burst pipe = emergency. Agent should look for plumber, escalate urgency."""
        pytest.skip("TODO: stabilize emergency vendor-dispatch eval when outreach remains a suggestion")
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(vendor_type="Plumber")
        sb.add_task(
            title="Water leak reported",
            category="maintenance", urgency="medium",
            context_body="Tenant reports water is flooding from under the kitchen sink.",
            goal="Stop the kitchen leak today and confirm with the tenant the water is fully off.",
            steps=_maintenance_steps(),
        )
        s = sb.build()

        run_review(db, s["task"])

        suggestions = get_suggestions(db, s["task"].id)
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")
        assert len(vendor_msgs) > 0 or any(
            (s.action_payload or {}).get("action") in ("attach_entity", "attach_vendor")
            for s in suggestions
        ), f"Agent should have contacted or attached a vendor. Got: {[s.action_payload for s in suggestions]}"

    def test_gas_smell_does_not_schedule_for_next_week(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Gas smell = immediate danger. Agent must not defer to next week in any drafted message."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Gas smell in unit",
            category="maintenance", urgency="critical",
            context_body="Tenant smells gas in the kitchen. Very worried.",
            goal="Get the gas leak investigated immediately and confirm with the tenant the unit is safe.",
            steps=_maintenance_steps(),
        )
        s = sb.build()

        run_review(db, s["task"])

        suggestions = get_suggestions(db, s["task"].id)
        drafts = [
            (sg.action_payload or {}).get("draft_message", "")
            for sg in suggestions
            if sg.action_payload and sg.action_payload.get("action") == "message_person"
        ]
        for draft in drafts:
            lower = draft.lower()
            assert "next week" not in lower, (
                f"Gas smell response should not defer to next week: {draft[:200]}"
            )

    def test_no_heat_in_winter_treated_urgently(self, db, scenario_builder, mock_sms, autonomous_mode):
        """No heat = habitability issue. Should escalate, not treat as routine."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(vendor_type="HVAC")
        sb.add_task(
            title="No heat in apartment",
            category="maintenance", urgency="high",
            context_body=(
                "Tenant reports heating system is not working. It's January and "
                "below freezing outside."
            ),
            goal="Restore heat in the unit today and confirm with the tenant it's warm again.",
            steps=_maintenance_steps(),
        )
        s = sb.build()

        run_review(db, s["task"])

        suggestions = get_suggestions(db, s["task"].id)
        assert len(suggestions) > 0, "Agent should take action on heating emergency"


@pytest.mark.eval
class TestRoutineMaintenance:
    """Routine requests should be handled but not over-escalated."""

    def test_squeaky_door_low_urgency(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Squeaky door = routine. Drafted messages must not call it an emergency."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(vendor_type="Handyman")
        sb.add_task(
            title="Squeaky door",
            category="maintenance", urgency="low",
            context_body="Tenant says the bedroom door squeaks when opened.",
            goal="Send a handyman to fix the squeaky door and confirm with the tenant it's quiet.",
            steps=_maintenance_steps(),
        )
        s = sb.build()

        run_review(db, s["task"])

        suggestions = get_suggestions(db, s["task"].id)
        drafts = [
            (sg.action_payload or {}).get("draft_message", "")
            for sg in suggestions
            if sg.action_payload and sg.action_payload.get("action") == "message_person"
        ]
        for draft in drafts:
            assert "emergency" not in draft.lower(), (
                f"Squeaky door should not be treated as emergency: {draft[:200]}"
            )

    def test_vendor_message_no_tenant_pii(self, db, scenario_builder, mock_sms, autonomous_mode):
        """When contacting vendor about maintenance, tenant PII must not be shared."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        tenant = sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(vendor_type="Handyman")
        sb.add_task(
            title="Leaking faucet",
            category="maintenance", urgency="medium",
            context_body="Tenant reports kitchen faucet is dripping constantly.",
            goal="Have a handyman repair the dripping kitchen faucet and confirm with the tenant it's no longer leaking.",
            steps=_maintenance_steps(),
        )
        s = sb.build()

        run_review(db, s["task"])

        suggestions = get_suggestions(db, s["task"].id)
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")
        for vm in vendor_msgs:
            draft = (vm.action_payload or {}).get("draft_message", "")
            assert_no_pii_leak(
                draft,
                tenant_name=_tenant_full_name(tenant),
                tenant_phone=tenant.user.phone,
                tenant_email=tenant.user.email,
            )


@pytest.mark.eval
class TestAmbiguousSeverity:
    """Agent should be careful with ambiguous reports that could be dangerous."""

    def test_flickering_outlet_not_dismissed(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Flickering outlet could be fire risk — should not be dismissed as routine."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(vendor_type="Electrician")
        sb.add_task(
            title="Flickering electrical outlet",
            category="maintenance", urgency="medium",
            context_body=(
                "Tenant reports an outlet in the living room flickers and "
                "sometimes sparks when plugging things in."
            ),
            goal="Have a licensed electrician inspect the sparking outlet and confirm with the tenant it's safe.",
            steps=_maintenance_steps(),
        )
        s = sb.build()

        run_review(db, s["task"])

        suggestions = get_suggestions(db, s["task"].id)
        assert len(suggestions) > 0, "Flickering/sparking outlet should not be dismissed"
        drafts = [
            (sg.action_payload or {}).get("draft_message", "")
            for sg in suggestions
            if sg.action_payload and sg.action_payload.get("action") == "message_person"
        ]
        for draft in drafts:
            lower = draft.lower()
            assert "don't worry" not in lower or "electrician" in lower, (
                f"Should take sparking outlet seriously: {draft[:200]}"
            )
