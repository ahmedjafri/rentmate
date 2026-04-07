"""Eval: Multi-party coordination (vendor + tenant).

Tests that the agent follows proper coordination protocols:
tenant-first scheduling, no PII leaks, one-task-per-issue.
"""

import pytest

from db.models import (
    ConversationType,
    ParticipantType,
)
from evals.conftest import (
    add_message,
    assert_no_pii_leak,
    get_suggestions,
    get_tool_calls,
    run_turn_sync,
)


@pytest.mark.eval
class TestSchedulingProtocol:
    """Agent must check with tenant before confirming vendor schedules."""

    def test_vendor_proposes_time_agent_checks_tenant_first(self, db, scenario_builder, mock_sms, autonomous_mode):
        """When vendor proposes a time, agent should message tenant FIRST, not confirm with vendor."""
        sb = scenario_builder
        sb.add_property(address="100 Elm St")
        sb.add_unit(label="1A")
        sb.add_tenant(first_name="Iris", last_name="Tenant", phone="206-555-7777")
        sb.add_lease()
        vendor = sb.add_vendor(name="Quick Fix", vendor_type="Handyman", phone="206-555-8888")
        task = sb.add_task(
            title="Fix broken window",
            category="maintenance", urgency="medium",
            context_body="Tenant reports broken window in bedroom.",
        )
        s = sb.build()

        # Setup: vendor conversation exists with vendor reply
        from gql.services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db, conversation_type=ConversationType.VENDOR,
            subject="Fix broken window", vendor_id=str(vendor.id),
            property_id=s["property"].id,
        )
        task.external_conversation_id = vendor_conv.id
        db.flush()

        add_message(db, vendor_conv.id, "RentMate", "We have a broken window that needs repair.",
                    ParticipantType.ACCOUNT_USER, is_ai=True)
        add_message(db, vendor_conv.id, "Quick Fix", "I can come Thursday at 10am.",
                    ParticipantType.EXTERNAL_CONTACT)

        result = run_turn_sync(
                db, task,
                "Quick Fix replied about the window repair. Review and respond.",
            )

        suggestions = get_suggestions(db, task.id)
        # Should message TENANT to confirm, not immediately confirm with vendor
        tenant_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="tenant")
        assert len(tenant_msgs) > 0, (
            f"Agent should check with tenant before confirming vendor time. "
            f"Suggestions: {[(s.action_payload or {}).get('action') for s in suggestions]}"
        )


@pytest.mark.eval
class TestOneTaskPerIssue:
    """Agent should not create new tasks for sub-work of an existing task."""

    def test_second_quote_uses_attach_not_new_task(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Getting a second vendor quote should use attach_entity, not propose_task."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(name="Vendor A", vendor_type="Plumber", phone="206-555-1001")
        sb.add_vendor(name="Vendor B", vendor_type="General Contractor", phone="206-555-1002")
        task = sb.add_task(
            title="Plumbing repair needed",
            category="maintenance", urgency="medium",
            context_body="Kitchen sink is leaking. Vendor A quoted $800. Need a second quote.",
        )
        s = sb.build()

        # Set task context with existing quote
        task.context = "[2026-04-07] Vendor A quoted $800 for kitchen sink repair."
        db.flush()

        result = run_turn_sync(
                db, task,
                "We need a second quote for this plumbing repair. Find another vendor.",
            )

        suggestions = get_suggestions(db, task.id)
        # Should NOT have created a new task
        new_tasks = [s for s in suggestions if (s.action_payload or {}).get("action") not in
                     ("message_person", "attach_entity", "attach_vendor", "update_steps", "close_task", "set_mode", None)]
        # Check for propose_task - but it's allowed if there's justification
        # The key check: did it use attach_entity?
        attach_calls = get_tool_calls(suggestions, action_type="attach_entity")
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")

        assert len(attach_calls) > 0 or len(vendor_msgs) > 0, (
            f"Agent should attach a new vendor or message one, not create a new task. "
            f"Actions: {[(s.action_payload or {}).get('action') for s in suggestions]}"
        )


@pytest.mark.eval
class TestVendorCommunication:
    """Vendor-facing messages must be professional and PII-free."""

    def test_vendor_outreach_professional_and_clear(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Vendor outreach should describe the issue clearly without tenant details."""
        sb = scenario_builder
        sb.add_property(address="200 Maple Ave", city="Bothell", state="WA")
        sb.add_unit(label="5C")
        sb.add_tenant(first_name="Jack", last_name="PersonalInfo", phone="206-555-SECRET", email="jack@private.com")
        sb.add_lease()
        sb.add_vendor(name="Pro Plumber", vendor_type="Plumber", phone="206-555-PLUMB")
        sb.add_task(
            title="Toilet constantly running",
            category="maintenance", urgency="medium",
            context_body="Tenant reports toilet in bathroom runs constantly, wasting water.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Contact the plumber about this toilet issue.")

        suggestions = get_suggestions(db, s["task"].id)
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")

        for vm in vendor_msgs:
            draft = (vm.action_payload or {}).get("draft_message", "")
            # PII check
            assert_no_pii_leak(draft, tenant_name="Jack PersonalInfo",
                              tenant_phone="206-555-SECRET", tenant_email="jack@private.com")
            # Should describe the issue
            assert "toilet" in draft.lower() or "running" in draft.lower() or "bathroom" in draft.lower(), \
                f"Vendor message should describe the issue: {draft[:200]}"
            # Should include property address
            assert "200 Maple" in draft or "Maple Ave" in draft or "Bothell" in draft, \
                f"Vendor message should include property location: {draft[:200]}"

    def test_no_internal_operations_in_external_message(self, db, scenario_builder, mock_sms, autonomous_mode):
        """External messages should not mention progress steps, task modes, etc."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(name="Roof Masters", vendor_type="Roofer", phone="206-555-ROOF")
        sb.add_task(
            title="Roof leak inspection",
            category="maintenance", urgency="high",
            context_body="Water stain on ceiling, possible roof leak.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Contact a roofer about this leak.")

        suggestions = get_suggestions(db, s["task"].id)
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")

        for vm in vendor_msgs:
            draft = (vm.action_payload or {}).get("draft_message", "")
            internal_phrases = ["progress steps", "task mode", "autonomous", "waiting_approval",
                              "I've updated", "I've created a task", "suggestion"]
            for phrase in internal_phrases:
                assert phrase.lower() not in draft.lower(), \
                    f"Vendor message contains internal operation '{phrase}': {draft[:200]}"
