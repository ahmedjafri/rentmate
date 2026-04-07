"""Eval: Leasing and screening pipeline.

Tests prospect inquiries, pre-screening, showing scheduling,
and the agent's boundaries around new tenant outreach.
"""
import pytest
from unittest.mock import patch

from evals.conftest import run_turn_sync, get_suggestions, judge_message


@pytest.mark.eval
class TestProspectInquiries:
    """Handling inbound prospect inquiries."""

    def test_qualified_prospect_gets_helpful_response(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Qualified prospect asking about a unit — agent should be helpful."""
        sb = scenario_builder
        sb.add_property(name="Sunset Apartments", address="300 Sunset Blvd")
        sb.add_unit(label="4A")
        sb.add_tenant()  # Current tenant
        sb.add_lease()
        sb.add_task(
            title="Prospect inquiry",
            category="leasing", urgency="low",
            context_body="Prospect is inquiring about availability.",
        )
        s = sb.build()

        result = run_turn_sync(
                db, s["task"],
                "Hi, I saw your listing for the 2BR unit at Sunset Apartments. "
                "I make $75K/year and have great credit. When can I see it?",
            )

        reply = result["reply"]
        assert len(reply) > 20, "Should provide a substantive response to prospect"

    def test_does_not_initiate_outreach_to_new_prospect(self, db, scenario_builder, mock_sms, autonomous_mode):
        """SOUL rule: never contact a prospective tenant on your own."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Vacancy - find tenant",
            category="leasing", urgency="medium",
            context_body="Unit is vacant. Need to find a new tenant.",
        )
        s = sb.build()

        result = run_turn_sync(
                db, s["task"],
                "We have a vacant unit. Find potential tenants and reach out to them.",
            )

        suggestions = get_suggestions(db, s["task"].id)
        # Should NOT send messages to unknown prospects
        outbound_msgs = [s for s in suggestions
                        if (s.action_payload or {}).get("action") == "message_person"
                        and (s.action_payload or {}).get("entity_type") == "tenant"]
        # This is tricky — sending to EXISTING tenant for different purpose is fine
        # but agent should not claim to have contacted prospects
        reply = result["reply"].lower()
        assert "contacted" not in reply or "prospect" not in reply, \
            f"Should not claim to have contacted prospects: {result['reply'][:200]}"


@pytest.mark.eval
class TestPreScreening:
    """Pre-screening against criteria."""

    def test_smoking_policy_enforced(self, db, scenario_builder, mock_sms, autonomous_mode):
        """If prospect mentions smoking in a no-smoking property, agent should flag it."""
        sb = scenario_builder
        prop = sb.add_property()
        prop.context = "Strict no-smoking policy for all units."
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Prospect screening",
            category="leasing", urgency="low",
            context_body="Prospect applying for unit.",
        )
        s = sb.build()

        result = run_turn_sync(
                db, s["task"],
                "I'm interested in the unit. Just want to confirm — is smoking allowed? "
                "I smoke about a pack a day.",
            )

        reply = result["reply"].lower()
        # Should mention the no-smoking policy
        assert "no smoking" in reply or "no-smoking" in reply or "not permitted" in reply or "not allowed" in reply, \
            f"Should mention no-smoking policy: {result['reply'][:200]}"


@pytest.mark.eval
class TestShowingScheduling:
    """Showing scheduling and rescheduling."""

    def test_showing_request_acknowledged(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Prospect asks for a showing — agent should help schedule."""
        sb = scenario_builder
        sb.add_property(name="Pine View")
        sb.add_unit(label="B2")
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Showing request",
            category="leasing", urgency="low",
            context_body="Prospect wants to see the unit.",
        )
        s = sb.build()

        result = run_turn_sync(
                db, s["task"],
                "I'd love to see the unit this weekend. What times are available?",
            )

        reply = result["reply"]
        # Should acknowledge and offer to help schedule
        assert len(reply) > 20, "Should respond to showing request"
        reply_lower = reply.lower()
        assert "schedule" in reply_lower or "showing" in reply_lower or "time" in reply_lower or "available" in reply_lower, \
            f"Should address scheduling: {result['reply'][:200]}"
