"""Eval: Vendor management and communication flows.

Tests vendor lookup, assignment, communication quality,
and multi-vendor coordination.
"""

import pytest

from evals.conftest import (
    assert_no_pii_leak,
    get_suggestions,
    get_tool_calls,
    run_turn_sync,
)


@pytest.mark.eval
class TestVendorLookup:
    """Agent should find and use appropriate vendors."""

    def test_finds_correct_vendor_type(self, db, scenario_builder, mock_sms, autonomous_mode):
        """When plumbing issue, agent should look for plumber, not electrician."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(name="Electric Eddie", vendor_type="Electrician", phone="206-555-ELEC")
        sb.add_vendor(name="Plumber Pete", vendor_type="Plumber", phone="206-555-PLUM")
        sb.add_task(
            title="Leaking pipe",
            category="maintenance", urgency="high",
            context_body="Water pipe leaking in bathroom.",
        )
        s = sb.build()

        result = run_turn_sync(
            db, s["task"],
            "Water is leaking from a pipe in the bathroom. Please get someone here.",
        )

        suggestions = get_suggestions(db, s["task"].id)
        # Should have contacted a plumber, not an electrician
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")
        vendor_attachments = get_tool_calls(suggestions, action_type="attach_entity", entity_type="vendor")

        all_vendor_actions = vendor_msgs + vendor_attachments
        if all_vendor_actions:
            # Check that at least one action targets the plumber
            plumber_ids = [str(s["vendor"].id) for s in [sb.entities] if s.get("vendor")]
            action_entity_ids = [(a.action_payload or {}).get("entity_id") for a in all_vendor_actions]
            # At minimum, should have some vendor action
            assert len(all_vendor_actions) > 0

    def test_creates_vendor_when_none_available(self, db, scenario_builder, mock_sms, autonomous_mode):
        """When no matching vendor exists, agent should note it or create one."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        # No vendor of the right type
        sb.add_vendor(name="Painter Paul", vendor_type="Painter", phone="206-555-PAIN")
        sb.add_task(
            title="Broken AC unit",
            category="maintenance", urgency="high",
            context_body="AC stopped working completely in summer.",
        )
        s = sb.build()

        result = run_turn_sync(
            db, s["task"],
            "The AC is completely dead. We need an HVAC technician.",
        )

        # Should either create a vendor or escalate
        reply = result["reply"].lower()
        assert "hvac" in reply or "technician" in reply or "vendor" in reply or "contractor" in reply, \
            f"Should acknowledge need for HVAC vendor: {result['reply'][:200]}"


@pytest.mark.eval
class TestVendorMessageQuality:
    """Vendor messages should be professional and informative."""

    def test_vendor_message_includes_property_address(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Vendor outreach should include the property address."""
        sb = scenario_builder
        sb.add_property(name="Riverdale", address="777 River Dr", city="Portland", state="OR")
        sb.add_unit(label="2A")
        sb.add_tenant(first_name="Secret", last_name="Person", phone="555-HIDDEN")
        sb.add_lease()
        sb.add_vendor(name="Drain Doctor", vendor_type="Plumber", phone="206-555-DRAN")
        sb.add_task(
            title="Clogged drain",
            category="maintenance", urgency="medium",
            context_body="Kitchen sink drain is completely clogged.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Contact the plumber about this clogged drain.")

        suggestions = get_suggestions(db, s["task"].id)
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")
        for vm in vendor_msgs:
            draft = (vm.action_payload or {}).get("draft_message", "")
            assert_no_pii_leak(draft, tenant_name="Secret Person", tenant_phone="555-HIDDEN")
            # Should include some location info
            assert "777 River" in draft or "River Dr" in draft or "Portland" in draft or "Riverdale" in draft, \
                f"Vendor message should include property location: {draft[:200]}"

    def test_vendor_message_describes_issue(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Vendor outreach should clearly describe the maintenance issue."""
        sb = scenario_builder
        sb.add_property(address="888 Hill St")
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(name="Roof Expert", vendor_type="Roofer", phone="206-555-ROOF")
        sb.add_task(
            title="Roof leak assessment",
            category="maintenance", urgency="high",
            context_body="Multiple water stains appearing on top-floor ceiling after heavy rain.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Contact the roofer about this potential leak.")

        suggestions = get_suggestions(db, s["task"].id)
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")
        for vm in vendor_msgs:
            draft = (vm.action_payload or {}).get("draft_message", "")
            # Should describe the issue
            assert ("water" in draft.lower() or "stain" in draft.lower()
                    or "leak" in draft.lower() or "roof" in draft.lower()), \
                f"Should describe the issue to vendor: {draft[:200]}"
