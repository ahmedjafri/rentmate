"""Eval: agent handles batch operations across multiple properties.

When the user requests an action across multiple properties (e.g. "gutter cleaning
on all Washington properties"), the agent should create tasks/suggestions for ALL
matching properties — not just one, and not ask for confirmation on each.
"""
import os

import pytest

pytestmark = pytest.mark.eval


@pytest.fixture
def multi_property_scenario(scenario_builder, db):
    """Build a scenario with multiple WA properties and a vendor."""
    builder = scenario_builder

    # Three WA properties
    p1 = builder.add_property(name="Acme Lane House", address="1234 Acme Lane", city="Blaine", state="WA")
    builder.add_unit(label="Main", prop=p1)

    import uuid

    from db.models import Property, Unit
    p2 = Property(
        id=str(uuid.uuid4()),
        org_id=1,
        creator_id=1,
        name="Cedar Heights",
        address_line1="567 Cedar St",
        city="Bothell",
        state="WA",
        postal_code="98012",
    )
    db.add(p2)
    db.flush()
    u2 = Unit(id=str(uuid.uuid4()), org_id=1, creator_id=1, property_id=p2.id, label="Main")
    db.add(u2)
    db.flush()

    p3 = Property(
        id=str(uuid.uuid4()),
        org_id=1,
        creator_id=1,
        name="Pine Valley",
        address_line1="890 Pine Ave",
        city="Seattle",
        state="WA",
        postal_code="98101",
    )
    db.add(p3)
    db.flush()
    u3 = Unit(id=str(uuid.uuid4()), org_id=1, creator_id=1, property_id=p3.id, label="Main")
    db.add(u3)
    db.flush()

    # One non-WA property (should NOT be included)
    p4 = Property(
        id=str(uuid.uuid4()),
        org_id=1,
        creator_id=1,
        name="Oregon Place",
        address_line1="100 Oak Rd",
        city="Portland",
        state="OR",
        postal_code="97201",
    )
    db.add(p4)
    db.flush()

    builder.add_vendor(name="Gutter Pro Services", vendor_type="General Contractor")

    # Create a task for the agent to work in
    builder.entities["property"] = p1
    builder.entities["unit"] = builder.entities.get("unit")
    builder.add_tenant(first_name="Test", last_name="Tenant")
    builder.add_lease()
    builder.add_task(
        title="Seasonal maintenance review",
        category="maintenance",
        context_body=(
            "The property manager wants to schedule seasonal maintenance. "
            "Properties in the system:\n"
            f"  - {p1.name}: {p1.address_line1}, {p1.city}, WA\n"
            f"  - {p2.name}: {p2.address_line1}, {p2.city}, WA\n"
            f"  - {p3.name}: {p3.address_line1}, {p3.city}, WA\n"
            f"  - {p4.name}: {p4.address_line1}, {p4.city}, OR\n"
        ),
    )
    return {
        **builder.build(),
        "wa_properties": [p1, p2, p3],
        "or_property": p4,
    }


def test_agent_creates_tasks_for_all_matching_properties(multi_property_scenario, db):
    """When asked to do gutter cleaning on WA properties, the agent should
    create tasks/suggestions for ALL WA properties, not just one."""
    from evals.conftest import run_turn_sync

    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")

    task = multi_property_scenario["task"]
    wa_props = multi_property_scenario["wa_properties"]

    result = run_turn_sync(
        db, task,
        "We need to do gutter cleaning on all Washington properties. "
        "Contact Gutter Pro Services for quotes on each."
    )

    reply = result["reply"].lower()
    pending = result["pending_suggestions"]

    # The agent should have created multiple tasks/suggestions
    # At minimum, it should mention multiple properties in the reply
    wa_names = [p.name.lower() for p in wa_props]
    wa_addresses = [p.address_line1.lower() for p in wa_props]

    mentions = sum(
        1 for p in wa_props
        if any(term in reply for term in [p.name.lower(), p.address_line1.lower()[:15]])
    )

    # Should mention at least 2 of the 3 WA properties
    assert mentions >= 2 or len(pending) >= 2, (
        f"Agent should act on multiple WA properties but only mentioned {mentions} "
        f"and created {len(pending)} suggestions. Reply: {reply[:400]}"
    )

    # Should NOT include Oregon property
    assert "oregon" not in reply and "portland" not in reply and "oak rd" not in reply, (
        f"Agent included non-WA property in WA-only request: {reply[:400]}"
    )


def test_agent_does_not_ask_for_each_property(multi_property_scenario, db):
    """The agent should NOT ask 'do you have any other properties?' when
    the user's request clearly covers multiple properties."""
    from evals.conftest import run_turn_sync

    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")

    task = multi_property_scenario["task"]
    result = run_turn_sync(
        db, task,
        "Schedule gutter cleaning for all our Washington state properties."
    )

    reply = result["reply"].lower()

    # Should NOT ask if there are more properties
    bad_phrases = [
        "any other",
        "do you have",
        "which properties",
        "which ones",
        "can you specify",
        "please list",
        "let me know which",
    ]
    found_bad = [p for p in bad_phrases if p in reply]
    assert not found_bad, (
        f"Agent asked for clarification instead of acting on all WA properties. "
        f"Found: {found_bad}. Reply: {reply[:400]}"
    )
