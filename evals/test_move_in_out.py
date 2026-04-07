"""Eval: Move-in and move-out scenarios.

Tests handling of move-out notices, security deposit questions,
and lease termination.
"""
import pytest
from unittest.mock import patch

from evals.conftest import run_turn_sync, get_suggestions, judge_message


@pytest.mark.eval
class TestMoveOut:
    """Move-out related scenarios."""

    def test_proper_moveout_notice(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Tenant gives proper 30-day notice — agent should acknowledge and outline next steps."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Oscar", last_name="Leaving")
        sb.add_lease(end_offset_days=45)
        sb.add_task(
            title="Move-out notice",
            category="leasing", urgency="medium",
            context_body="Tenant is giving notice to move out.",
        )
        s = sb.build()

        result = run_turn_sync(
                db, s["task"],
                "I'm writing to let you know I'll be moving out at the end of next month. "
                "Please consider this my 30-day notice.",
            )

        reply = result["reply"]
        judge = judge_message(reply, "Tenant gives proper 30-day move-out notice", [
            "Acknowledges receipt of the notice",
            "Mentions or references key next steps (inspection, key return, cleaning, deposit)",
            "Professional and not argumentative",
            "Does not try to talk tenant out of leaving",
        ])
        assert judge["pass"], f"LLM judge failed: {judge.get('reason', '')}"

    def test_early_lease_break_escalates(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Mid-lease break should be handled carefully — escalate to manager."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Pete", last_name="EarlyExit")
        sb.add_lease(end_offset_days=200)  # Still 200 days left
        sb.add_task(
            title="Early lease termination",
            category="leasing", urgency="medium",
            context_body="Tenant wants to break lease early.",
        )
        s = sb.build()

        result = run_turn_sync(
                db, s["task"],
                "I need to break my lease. I got a job transfer and need to move "
                "to another state in 3 weeks. What are my options?",
            )

        reply = result["reply"].lower()
        # Should not just say "sure, leave whenever"
        assert "anytime" not in reply, f"Should not trivialize lease break: {result['reply'][:200]}"
        # Should mention this is something to discuss with manager or review lease terms
        assert "manager" in reply or "lease" in reply or "discuss" in reply or "options" in reply, \
            f"Should discuss options for early lease break: {result['reply'][:200]}"

    def test_security_deposit_question_after_moveout(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Tenant asks about deposit return — should explain process, not guess amounts."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Quinn", last_name="Deposit")
        sb.add_lease(end_offset_days=-5)  # Already past lease end
        sb.add_task(
            title="Security deposit inquiry",
            category="rent", urgency="low",
            context_body="Tenant moved out 5 days ago, asking about security deposit return.",
        )
        s = sb.build()

        result = run_turn_sync(
                db, s["task"],
                "When will I get my security deposit back? How much will I get?",
            )

        reply = result["reply"].lower()
        # Should not make up specific dollar amounts
        assert "$" not in reply or "deduction" in reply or "inspection" in reply, \
            f"Should not guess deposit amount: {result['reply'][:200]}"
