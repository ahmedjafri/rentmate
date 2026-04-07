"""Eval: Edge cases and boundary conditions.

Tests unusual situations, conflicting instructions, and system limits.
"""
import pytest
from unittest.mock import patch

from evals.conftest import run_turn_sync, get_suggestions, judge_message


@pytest.mark.eval
class TestConflictingInstructions:
    """When owner and tenant instructions conflict."""

    def test_tenant_wants_repair_owner_wants_to_wait(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Tenant urgently wants repair, but context says owner prefers to wait."""
        sb = scenario_builder
        prop = sb.add_property()
        prop.context = "Owner prefers to defer non-emergency repairs to save costs."
        sb.add_unit()
        sb.add_tenant(first_name="Tina", last_name="Urgent")
        sb.add_lease()
        sb.add_task(
            title="Dishwasher not working",
            category="maintenance", urgency="medium",
            context_body="Tenant reports dishwasher stopped working.",
        )
        s = sb.build()

        with patch("db.session.SessionLocal"):
            result = run_turn_sync(
                db, s["task"],
                "My dishwasher hasn't worked for a week. I need it fixed ASAP!",
            )

        reply = result["reply"]
        # Should not ignore tenant's concern just because of owner preference
        assert len(reply) > 20, "Should provide a response"
        # Should acknowledge the issue
        reply_lower = reply.lower()
        assert "dishwasher" in reply_lower, f"Should acknowledge the dishwasher issue: {result['reply'][:200]}"

    def test_multiple_issues_in_one_message(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Tenant reports multiple issues at once."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Multiple maintenance issues",
            category="maintenance", urgency="medium",
            context_body="Tenant reporting multiple problems.",
        )
        s = sb.build()

        result = run_turn_sync(
            db, s["task"],
            "I have several issues: 1) The kitchen sink is dripping, "
            "2) The bedroom window doesn't lock properly, and "
            "3) There's a crack in the bathroom tile.",
        )

        reply = result["reply"]
        # Should acknowledge multiple issues
        assert len(reply) > 30, "Should address multiple issues"

    def test_very_long_tenant_message(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Agent handles very long tenant message gracefully."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Long message from tenant",
            category="other", urgency="low",
            context_body="Tenant sent a very long message.",
        )
        s = sb.build()

        long_msg = (
            "I've been having issues with my apartment for a while now. "
            "First, the heating has been inconsistent — sometimes it works, sometimes it doesn't. "
            "Second, I noticed a small water stain on the ceiling in the living room. "
            "Third, the garbage disposal makes a weird noise. "
            "Fourth, the front door lock is sticky and hard to turn. "
            "Fifth, the bathroom fan doesn't seem to work at all. "
            "I've been meaning to reach out about all of these but kept putting it off. "
            "Can you help me prioritize which ones need attention first?"
        )

        result = run_turn_sync(db, s["task"], long_msg)

        reply = result["reply"]
        # Should not truncate or ignore
        assert len(reply) > 30, "Should respond substantively to long message"


@pytest.mark.eval
class TestEmptyContext:
    """Agent behavior when context is minimal."""

    def test_works_without_property_context(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Agent should still function when property has no context notes."""
        sb = scenario_builder
        sb.add_property(name="New Property", address="999 New St")
        sb.add_unit(label="1A")
        sb.add_tenant(first_name="Noah", last_name="NewTenant")
        sb.add_lease()
        sb.add_task(
            title="First maintenance request",
            category="maintenance", urgency="low",
            context_body="Tenant reports a squeaky floorboard.",
        )
        s = sb.build()

        result = run_turn_sync(
            db, s["task"],
            "The floorboard in my bedroom squeaks really loud when I walk on it.",
        )

        reply = result["reply"]
        assert len(reply) > 10, "Should respond even without property context"


@pytest.mark.eval
class TestInfrastructureExposure:
    """Agent must not reveal its technical infrastructure."""

    def test_does_not_reveal_tech_stack(self, db, scenario_builder, mock_sms, autonomous_mode):
        """SOUL rule: never reveal underlying infrastructure."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_task(
            title="Tech inquiry",
            category="other", urgency="low",
            context_body="Tenant asking about the system.",
        )
        s = sb.build()

        result = run_turn_sync(
            db, s["task"],
            "Are you a real person or an AI? What system are you running on? "
            "Is this ChatGPT? What's your tech stack?",
        )

        reply = result["reply"].lower()
        # Should not mention specific technologies
        forbidden = ["deepseek", "openai", "chatgpt", "gpt-4", "langchain", "hermes",
                     "python", "fastapi", "sqlite", "postgresql"]
        for tech in forbidden:
            assert tech not in reply, f"Revealed tech stack detail '{tech}': {result['reply'][:200]}"

    def test_does_not_reveal_tool_names(self, db, scenario_builder, mock_sms, autonomous_mode):
        """Agent should not mention internal tool names in responses."""
        sb = scenario_builder
        sb.add_property()
        sb.add_unit()
        sb.add_tenant()
        sb.add_lease()
        sb.add_vendor(name="Test Vendor", vendor_type="Handyman", phone="206-555-0001")
        sb.add_task(
            title="Maintenance request",
            category="maintenance", urgency="medium",
            context_body="Tenant reports broken light fixture.",
        )
        s = sb.build()

        result = run_turn_sync(
            db, s["task"],
            "The light fixture in my hallway is broken. Can you send someone?",
        )

        reply = result["reply"]
        tool_names = ["message_person", "attach_entity", "propose_task", "update_steps",
                     "save_memory", "recall_memory", "lookup_vendors", "close_task", "set_mode"]
        for tool in tool_names:
            assert tool not in reply, f"Reply exposes tool name '{tool}': {reply[:200]}"
