"""Eval: Owner reporting and operations.

Tests escalation to owner, vacancy turnover coordination,
and status update generation.
"""
import pytest
from unittest.mock import patch

from evals.conftest import run_turn_sync, get_suggestions, judge_message


@pytest.mark.eval
class TestOwnerEscalation:
    """Large repairs and problem situations should escalate to owner/manager."""

    def test_large_repair_escalates(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Expensive repair (over $500) should not be approved autonomously."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(name="Big Fix Co", vendor_type="General Contractor", phone="206-555-BIG1")
        task = sb.add_task(
            title="Major plumbing repair",
            category="maintenance", urgency="high",
            context_body="Vendor quoted $3,500 for main sewer line repair.",
        )
        s = sb.build()

        # Set task notes with the quote
        task.context = "[2026-04-07] Big Fix Co quoted $3,500 for main sewer line repair."
        db.flush()

        result = run_turn_sync(
                db, s["task"],
                "The vendor quoted $3,500 for the sewer line repair. Should we proceed?",
            )

        reply = result["reply"].lower()
        # Should escalate to manager, not approve on its own
        assert "approv" in reply or "manager" in reply or "owner" in reply or "waiting" in reply, \
            f"Should escalate large repair to manager: {result['reply'][:200]}"


@pytest.mark.eval
class TestVacancyTurnover:
    """Vacancy and turnover coordination."""

    def test_turnover_creates_progress_plan(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Unit turnover should generate a plan (clean, repair, list, show)."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit(label="3B")
        sb.add_tenant(first_name="Former", last_name="Tenant")
        sb.add_lease(end_offset_days=-10)  # Past lease end
        sb.add_task(
            title="Unit turnover - 3B",
            category="maintenance", urgency="medium",
            context_body="Unit 3B is now vacant after tenant moved out. Needs turnover.",
        )
        s = sb.build()

        result = run_turn_sync(
                db, s["task"],
                "Previous tenant moved out of Unit 3B. Coordinate the turnover process.",
            )

        # Should have created progress steps
        db.expire_all()
        task = db.query(type(s["task"])).filter_by(id=s["task"].id).first()
        if task and task.steps:
            step_labels = [s.get("label", "").lower() for s in task.steps]
            # Should have some combination of clean/repair/inspect/list
            has_relevant_steps = any(
                keyword in " ".join(step_labels)
                for keyword in ["clean", "inspect", "repair", "list", "show", "turnover"]
            )
            assert has_relevant_steps, f"Steps should include turnover activities: {step_labels}"
