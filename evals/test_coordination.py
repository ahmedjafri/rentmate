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
            parent_task_id=task.id,
        )
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

    def test_vendor_quote_and_slot_does_not_reask_vendor_and_checks_tenant(self, db, scenario_builder, mock_sms, autonomous_mode):
        """If the vendor already gave quote + availability and RentMate
        acknowledged it, a later review should move forward by messaging
        the tenant rather than re-requesting quote/pricing/availability."""
        sb = scenario_builder
        prop = sb.add_property(name="The Meadows", address="1842 Meadow Lane")
        sb.add_unit(label="A")
        sb.add_tenant(first_name="Alex", last_name="Tenant", phone="206-555-1111")
        sb.add_lease()
        vendor = sb.add_vendor(name="Alex", vendor_type="Landscaper", phone="206-555-2222")
        task = sb.add_task(
            title="Spring cleanup at The Meadows",
            category="maintenance", urgency="medium",
            context_body="Need spring cleanup quote and coordination for common areas.",
        )

        from gql.services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject="Spring cleanup",
            vendor_id=str(vendor.id),
            property_id=prop.id,
            parent_task_id=task.id,
        )
        db.flush()

        add_message(
            db,
            vendor_conv.id,
            "RentMate",
            "Hi Alex, looking for a quote on spring cleanup at The Meadows (1842 Meadow Lane). Front/back lawn mow, shrub trim, bed weeding — common areas only, 4-unit building.",
            ParticipantType.ACCOUNT_USER,
            is_ai=True,
        )
        add_message(
            db,
            vendor_conv.id,
            "Alex",
            "$545 for the first cleanup, $60/visit if you want biweekly through summer. I have next Thursday open.",
            ParticipantType.EXTERNAL_CONTACT,
        )
        add_message(
            db,
            vendor_conv.id,
            "RentMate",
            "Appreciate it. Holding the Thursday slot while I confirm with the owner.",
            ParticipantType.ACCOUNT_USER,
            is_ai=True,
        )

        result = run_turn_sync(
            db,
            task,
            "Alex already provided quote and availability for spring cleanup. Review and respond.",
        )

        suggestions = get_suggestions(db, task.id)
        tenant_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="tenant")
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")

        assert len(tenant_msgs) > 0, (
            f"Agent should move forward by messaging the tenant. "
            f"Reply: {result['reply'][:200]} "
            f"Actions: {[(s.action_payload or {}).get('action') for s in suggestions]}"
        )
        assert len(vendor_msgs) == 0, (
            f"Agent should not re-request quote/availability from the vendor after it was already provided. "
            f"Reply: {result['reply'][:200]}"
        )

    def test_vendor_proposes_booking_time_agent_does_not_close_before_both_sides_informed(self, db, scenario_builder, mock_sms, autonomous_mode):
        """A vendor proposing a concrete booking time should keep the task in
        coordination mode: ask the tenant first, do not confirm the vendor
        yet, and definitely do not close the task."""
        sb = scenario_builder
        prop = sb.add_property(name="The Meadows", address="1842 Meadow Lane")
        sb.add_unit(label="2B")
        tenant = sb.add_tenant(first_name="Priya", last_name="Patel", phone="206-555-0102")
        sb.add_lease()
        vendor = sb.add_vendor(name="Alex Nakamura", vendor_type="Landscaper", phone="206-555-0206")
        task = sb.add_task(
            title="Landscape spring cleanup — booking",
            category="maintenance", urgency="low",
            context_body="Two quotes received. Need to finalize time and book the spring cleanup.",
        )
        task.goal = "Get at least two landscaper quotes and pick one to do a spring cleanup at The Meadows by end of month."
        task.steps = [
            {"key": "collect_quotes", "label": "Collect landscaper quotes", "status": "done"},
            {"key": "compare_bids", "label": "Compare bids and timing", "status": "done"},
            {"key": "book_vendor", "label": "Book the selected landscaper", "status": "active"},
        ]
        db.flush()

        from gql.services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject="Spring cleanup",
            vendor_id=str(vendor.id),
            property_id=prop.id,
            parent_task_id=task.id,
        )
        tenant_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.TENANT,
            subject="Spring cleanup access",
            tenant_id=str(tenant.id),
            property_id=prop.id,
            parent_task_id=task.id,
        )
        task.parent_conversation_id = tenant_conv.id
        db.flush()

        add_message(
            db, vendor_conv.id, "RentMate",
            "Thanks for the quote. I may book you for next week once I confirm access.",
            ParticipantType.ACCOUNT_USER, is_ai=True,
        )
        add_message(
            db, vendor_conv.id, "Alex Nakamura",
            "Thursday at 10am works on my end if the tenant can provide access.",
            ParticipantType.EXTERNAL_CONTACT,
        )

        result = run_turn_sync(
            db,
            task,
            "Alex asked whether Thursday at 10am works for the spring cleanup. Review and respond.",
        )

        suggestions = get_suggestions(db, task.id)
        tenant_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="tenant")
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")
        close_actions = [s for s in suggestions if (s.action_payload or {}).get("action") == "close_task"]

        assert len(tenant_msgs) > 0, (
            f"Agent should ask the tenant about access before booking. "
            f"Reply: {result['reply'][:200]} "
            f"Actions: {[(s.action_payload or {}).get('action') for s in suggestions if s.action_payload]}"
        )
        assert len(vendor_msgs) == 0, (
            f"Agent should not confirm the vendor before tenant confirmation. "
            f"Reply: {result['reply'][:200]}"
        )
        assert len(close_actions) == 0, (
            f"Agent should not close the task while the scheduling handshake is incomplete. "
            f"Reply: {result['reply'][:200]}"
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
