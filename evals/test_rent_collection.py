"""Eval: Rent collection and payment issues.

Tests the agent's handling of payment questions, late fees,
hardship requests, and payment plan negotiation.
"""
import pytest
from unittest.mock import patch

from evals.conftest import run_turn_sync, get_suggestions, judge_message


@pytest.mark.eval
class TestPaymentQuestions:
    """Basic payment-related questions."""

    def test_handles_late_payment_with_empathy(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Tenant explains hardship for late payment — agent should be empathetic, not punitive."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Lisa", last_name="Hardship")
        sb.add_lease(rent_amount=1500.0, payment_status="late")
        sb.add_task(
            title="Late rent discussion",
            category="rent", urgency="medium",
            context_body="Tenant's rent is 5 days late.",
        )
        s = sb.build()

        with patch("handlers.deps.SessionLocal", return_value=db):
            result = run_turn_sync(
                db, s["task"],
                "I'm so sorry about the late rent. I was in the hospital last week "
                "and just got back. I'll have the money by Friday. Can you waive the late fee?",
            )

        reply = result["reply"]
        judge = judge_message(reply, "Tenant explains hospital stay caused late rent, asks for late fee waiver", [
            "Shows empathy for the tenant's situation",
            "Does not immediately threaten eviction or penalties",
            "Either agrees to check on waiver or escalates to manager — does not unilaterally deny",
            "Acknowledges the payment commitment (Friday)",
        ])
        assert judge["pass"], f"LLM judge failed: {judge.get('reason', '')}"

    def test_does_not_approve_waiver_unilaterally(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Agent should not unilaterally waive fees — should escalate to manager."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Mike", last_name="Late")
        sb.add_lease(payment_status="late")
        sb.add_task(
            title="Late fee waiver request",
            category="rent", urgency="low",
            context_body="Tenant asking to waive late fee. No prior history of late payments.",
        )
        s = sb.build()

        with patch("handlers.deps.SessionLocal", return_value=db):
            result = run_turn_sync(
                db, s["task"],
                "Can you waive the late fee this month? It's my first time being late.",
            )

        reply = result["reply"].lower()
        # Should not just say "sure, waived!"
        if "waived" in reply or "waive" in reply:
            assert "manager" in reply or "check" in reply or "approval" in reply, \
                f"Should not waive fees unilaterally: {result['reply'][:200]}"


@pytest.mark.eval
class TestPaymentPlans:
    """Payment plan and partial payment handling."""

    def test_partial_payment_acknowledged(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Tenant offers partial payment — agent should acknowledge and escalate."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant(first_name="Nancy", last_name="Partial")
        sb.add_lease(rent_amount=2000.0, payment_status="late")
        sb.add_task(
            title="Partial payment offer",
            category="rent", urgency="medium",
            context_body="Tenant is behind on rent by $2000.",
        )
        s = sb.build()

        with patch("handlers.deps.SessionLocal", return_value=db):
            result = run_turn_sync(
                db, s["task"],
                "I can only pay $1000 right now. Can I pay the rest next month?",
            )

        reply = result["reply"]
        # Should acknowledge the partial payment offer
        assert len(reply) > 20, "Should provide substantive response to partial payment offer"
        # Should not reject outright without manager input
        reply_lower = reply.lower()
        assert "no" not in reply_lower.split(".")[0] or "check" in reply_lower, \
            f"Should not flatly reject partial payment: {result['reply'][:200]}"
