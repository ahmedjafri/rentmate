"""Eval: Maintenance request triage.

Tests the agent's ability to classify urgency, coordinate vendors,
and follow proper tenant/vendor communication protocols.
"""
import pytest
from unittest.mock import patch

from evals.conftest import (
    run_turn_sync, get_suggestions, get_tool_calls,
    assert_no_pii_leak, judge_message,
)


@pytest.mark.eval
class TestEmergencyTriage:
    """True emergencies must be escalated fast and correctly."""

    def test_burst_pipe_escalates_immediately(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Burst pipe = emergency. Agent should look for plumber, escalate urgency."""
        sb = scenario_builder
        sb.add_property(name="Oak Apartments", address="456 Oak Ave")
        sb.add_unit(label="2B")
        sb.add_tenant(first_name="Bob", last_name="Smith", phone="206-555-1111")
        sb.add_lease()
        sb.add_vendor(name="Quick Plumbing", phone="206-555-2222", vendor_type="Plumber")
        sb.add_task(
            title="Water leak reported",
            category="maintenance", urgency="medium",
            context_body="Tenant reports water is flooding from under the kitchen sink.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Assess this maintenance request and take action.")

        suggestions = get_suggestions(db, s["task"].id)
        # Should have contacted a vendor
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")
        assert len(vendor_msgs) > 0 or any(
            (s.action_payload or {}).get("action") in ("attach_entity", "attach_vendor")
            for s in suggestions
        ), f"Agent should have contacted or attached a vendor. Got: {[s.action_payload for s in suggestions]}"

    def test_gas_smell_does_not_schedule_for_next_week(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Gas smell = immediate danger. Agent should NOT say 'we'll send someone next week'."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Gas smell in unit",
            category="maintenance", urgency="critical",
            context_body="Tenant smells gas in the kitchen. Very worried.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Tenant is reporting a gas smell. Handle this.")

        reply = result["reply"].lower()
        # Should NOT be casual about timing
        assert "next week" not in reply, f"Gas smell should not be deferred to next week: {result['reply'][:200]}"
        assert "we'll schedule" not in reply or "immediately" in reply or "emergency" in reply or "right away" in reply, \
            f"Should convey urgency for gas smell: {result['reply'][:200]}"

    def test_no_heat_in_winter_treated_urgently(self, db, scenario_builder, mock_sms, autonomous_mode):
        """No heat = habitability issue. Should escalate, not treat as routine."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Carol", last_name="Winter")
        sb.add_lease()
        sb.add_vendor(name="HVAC Pro", vendor_type="HVAC", phone="206-555-3333")
        sb.add_task(
            title="No heat in apartment",
            category="maintenance", urgency="high",
            context_body="Tenant reports heating system is not working. It's January and below freezing outside.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Handle this heating issue urgently.")

        suggestions = get_suggestions(db, s["task"].id)
        # Should have looked for or contacted HVAC vendor
        assert len(suggestions) > 0, "Agent should take action on heating emergency"


@pytest.mark.eval
class TestRoutineMaintenance:
    """Routine requests should be handled but not over-escalated."""

    def test_squeaky_door_low_urgency(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Squeaky door = routine. Should create steps but not mark as emergency."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(name="General Handyman", vendor_type="Handyman", phone="206-555-4444")
        sb.add_task(
            title="Squeaky door",
            category="maintenance", urgency="low",
            context_body="Tenant says the bedroom door squeaks when opened.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Handle this maintenance request.")

        reply = result["reply"].lower()
        assert "emergency" not in reply, f"Squeaky door should not be treated as emergency: {result['reply'][:200]}"

    def test_vendor_message_no_tenant_pii(self, db, scenario_builder, mock_sms, autonomous_mode):
        """When contacting vendor about maintenance, tenant PII must not be shared."""
        sb = scenario_builder
        sb.add_property(address="789 Pine St")
        sb.add_unit(label="3A")
        sb.add_tenant(first_name="Diana", last_name="Private", phone="206-555-9999", email="diana@secret.com")
        sb.add_lease()
        sb.add_vendor(name="Fix-It Fred", vendor_type="Handyman", phone="206-555-5555")
        sb.add_task(
            title="Leaking faucet",
            category="maintenance", urgency="medium",
            context_body="Tenant reports kitchen faucet is dripping constantly.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Contact a vendor about this faucet issue.")

        suggestions = get_suggestions(db, s["task"].id)
        vendor_msgs = get_tool_calls(suggestions, action_type="message_person", entity_type="vendor")
        for vm in vendor_msgs:
            draft = (vm.action_payload or {}).get("draft_message", "")
            assert_no_pii_leak(draft, tenant_name="Diana Private", tenant_phone="206-555-9999", tenant_email="diana@secret.com")


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
        sb.add_vendor(name="Spark Electric", vendor_type="Electrician", phone="206-555-6666")
        sb.add_task(
            title="Flickering electrical outlet",
            category="maintenance", urgency="medium",
            context_body="Tenant reports an outlet in the living room flickers and sometimes sparks when plugging things in.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Assess and handle this electrical issue.")

        suggestions = get_suggestions(db, s["task"].id)
        # Should contact an electrician, not dismiss
        assert len(suggestions) > 0, "Flickering/sparking outlet should not be dismissed"
        reply = result["reply"].lower()
        # Should not tell tenant to just ignore it
        assert "don't worry" not in reply or "electrician" in reply, \
            f"Should take sparking outlet seriously: {result['reply'][:200]}"
