"""Eval: Tenant communication and repetitive Q&A.

Tests the agent's ability to answer common tenant questions accurately,
escalate when info is missing, and maintain professional tone.
"""

import pytest

from evals.conftest import (
    run_turn_sync,
)


@pytest.mark.eval
class TestLeaseQuestions:
    """Common lease-related questions tenants ask."""

    def test_rent_due_date_answered_from_context(self, db, scenario_builder, mock_sms, autonomous_mode):
        """When lease data is available, agent should answer rent questions directly."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Eve", last_name="Asker")
        sb.add_lease(rent_amount=2100.0, payment_status="current")
        sb.add_task(
            title="Rent question from tenant",
            category="rent", urgency="low",
            context_body="Tenant asks: When is rent due and how much is it?",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Tenant asks: When is rent due and how much is it?")

        reply = result["reply"].lower()
        # Should mention rent amount from lease data
        assert "2,100" in reply or "2100" in reply, \
            f"Should state the rent amount from lease data: {result['reply'][:200]}"

    def test_missing_info_escalates_not_guesses(self, db, scenario_builder, mock_sms, autonomous_mode):
        """When agent doesn't have specific info, it should escalate, not guess."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Frank", last_name="Curious")
        sb.add_lease()
        sb.add_task(
            title="Security deposit question",
            category="rent", urgency="low",
            context_body="Tenant asks: What are the specific rules for getting my security deposit back?",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Tenant asks: What are the specific rules for getting my security deposit back? What deductions are typical?")

        reply = result["reply"].lower()
        # Should NOT tell tenant to check their lease
        assert "check your lease" not in reply, \
            f"Agent should not redirect tenant to check their own lease: {result['reply'][:200]}"
        # Should offer to follow up
        assert "check with" in reply or "follow up" in reply or "get back to you" in reply or "property manager" in reply, \
            f"Should offer to check with manager: {result['reply'][:200]}"

    def test_does_not_redirect_tenant_to_self_serve(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Agent should never tell tenant to 'look it up yourself'."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Late fee policy question",
            category="rent", urgency="low",
            context_body="Tenant asks: How much is the late fee if I pay rent 3 days late?",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "How much is the late fee if I pay rent 3 days late?")

        reply = result["reply"].lower()
        bad_phrases = ["check your lease", "refer to your", "look at your", "review your documents"]
        for phrase in bad_phrases:
            assert phrase not in reply, \
                f"Agent redirected tenant to self-serve ('{phrase}'): {result['reply'][:200]}"


@pytest.mark.eval
class TestCommonQuestions:
    """Repetitive questions about amenities, policies, etc."""

    def test_pet_policy_question(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Pet policy question — agent should answer if known or escalate."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Grace", last_name="PetLover")
        sb.add_lease()
        sb.add_task(
            title="Pet policy inquiry",
            category="leasing", urgency="low",
            context_body="Tenant asks: Can I get a dog? What's the pet policy?",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "Can I get a dog? What's the pet policy here?")

        # Should provide some response (even if escalating)
        assert len(result["reply"]) > 20, "Should provide a substantive response"
        # Should not make up a policy
        reply = result["reply"].lower()
        if "allow" in reply or "permit" in reply or "no pets" in reply:
            # If stating a policy, it should be hedged or sourced
            pass  # Accept — agent may have context
        else:
            # Should escalate
            assert "check" in reply or "manager" in reply or "get back" in reply, \
                f"Should either state policy or escalate: {result['reply'][:200]}"

    def test_subletting_request_handled_carefully(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Subletting requests are policy-sensitive — should not approve unilaterally."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Henry", last_name="Traveler")
        sb.add_lease()
        sb.add_task(
            title="Subletting request",
            category="leasing", urgency="low",
            context_body="Tenant wants to sublet their apartment for 2 months while traveling.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "I want to sublet my apartment for 2 months while I'm traveling. Is that okay?")

        reply = result["reply"].lower()
        # Should NOT just say "yes, go ahead"
        assert "yes" not in reply.split(".")[0] or "policy" in reply or "manager" in reply or "approval" in reply, \
            f"Should not approve subletting without manager input: {result['reply'][:200]}"


@pytest.mark.eval
class TestTenantTone:
    """Agent should be professional but warm, not robotic or dismissive."""

    def test_no_filler_phrases(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Agent should not use filler like 'Great question!' or 'I'd be happy to help!'."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="General inquiry",
            category="other", urgency="low",
            context_body="Tenant asks about trash pickup day.",
        )
        s = sb.build()

        result = run_turn_sync(db, s["task"], "What day is trash pickup?")

        reply = result["reply"]
        filler = ["Great question", "I'd be happy to help", "Thank you for reaching out",
                  "That's a great question", "I appreciate you asking"]
        for f in filler:
            assert f not in reply, f"Reply contains filler phrase '{f}': {reply[:200]}"
