"""Eval: agent creates suggestions from document data.

Tests that when the agent is told about an uploaded document with extracted
lease data, it uses create_suggestion (or create_property/create_tenant)
rather than doing nothing.
"""
import os

import pytest

pytestmark = pytest.mark.eval


@pytest.fixture
def scenario_with_document(scenario_builder, db):
    """Build a scenario with a processed document."""
    from datetime import UTC, datetime

    from db.models import Document

    # Create the document in DB (simulating a processed upload)
    doc = Document(
        id="eval-doc-001",
        filename="test-lease.pdf",
        content_type="application/pdf",
        storage_path="documents/eval-doc-001/test-lease.pdf",
        document_type="lease",
        status="done",
        sha256_checksum="eval-test",
        raw_text="Monthly rental agreement for 1234 Acme Lane, rent $2795/mo",
        extracted_data={
            "leases": [{
                "property_address": "1234 Acme Lane ABC WA 12345",
                "unit_label": "N/A",
                "tenant_first_name": None,
                "tenant_last_name": None,
                "lease_start_date": "2020-08-15",
                "lease_end_date": "2021-08-15",
                "monthly_rent": 2795,
                "property_type": "single_family",
                "property_context": "Furnishings: microwave, dishwasher, washer, dryer",
                "lease_context": "Security deposit $3000, late fee $40 after 4th day",
            }]
        },
        context="Test lease for eval purposes",
        created_at=datetime.now(UTC),
        processed_at=datetime.now(UTC),
    )
    db.add(doc)
    db.flush()

    # Build a task context for the agent to work in
    builder = scenario_builder
    builder.add_property(address="999 Other St")
    builder.add_unit(label="Main")
    builder.add_tenant(first_name="Existing", last_name="Tenant")
    builder.add_lease()
    builder.add_task(
        title="Review uploaded document",
        category="leasing",
        context_body=(
            f"User uploaded document: test-lease.pdf (ID: {doc.id})\n"
            f"The document has been processed. Extracted data shows a lease at "
            f"1234 Acme Lane for $2795/month. No tenant name was found in the document.\n"
            f"Review the extraction and create appropriate suggestions."
        ),
    )
    return {**builder.build(), "document": doc}


def test_agent_creates_action_from_document(scenario_with_document, db):
    """When told about a processed document, the agent should take action —
    either creating suggestions, creating entities directly, or saving context."""
    from evals.conftest import run_turn_sync

    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")

    task = scenario_with_document["task"]
    result = run_turn_sync(
        db, task,
        "I just uploaded a lease document (test-lease.pdf, ID: eval-doc-001). "
        "Please review it and suggest what records to create."
    )

    reply = result["reply"]
    pending = result["pending_suggestions"]

    # The agent should either:
    # 1. Create suggestions (via create_suggestion → pending_suggestions)
    # 2. Use tools that show up in progress (create_property, create_tenant)
    # 3. At minimum, acknowledge the document and explain what it found
    #
    # We check that the reply mentions the property or lease details
    reply_lower = reply.lower()
    mentions_property = "1234" in reply_lower or "acme" in reply_lower
    mentions_lease = "2795" in reply_lower or "rent" in reply_lower
    has_suggestions = len(pending) > 0

    assert mentions_property or mentions_lease or has_suggestions, (
        f"Agent didn't engage with the document data. Reply: {reply[:300]}"
    )


def test_agent_does_not_hallucinate_tenant(scenario_with_document, db):
    """When the document has no tenant name, the agent should NOT fabricate one."""
    from evals.conftest import run_turn_sync

    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")

    task = scenario_with_document["task"]
    result = run_turn_sync(
        db, task,
        "What tenant information is in the uploaded document eval-doc-001?"
    )

    reply = result["reply"].lower()
    # The extracted data has null tenant fields — agent should not fabricate
    assert "bob" not in reply, f"Agent hallucinated tenant name 'Bob': {reply[:300]}"
    assert "lisa" not in reply, f"Agent hallucinated landlord as tenant 'Lisa': {reply[:300]}"
    # Should mention that tenant info is missing/not found
    missing_indicators = ["no tenant", "not specified", "not found", "missing", "blank", "null", "unknown", "not available", "not included"]
    mentions_missing = any(ind in reply for ind in missing_indicators)
    assert mentions_missing, (
        f"Agent should note tenant info is missing, but said: {reply[:300]}"
    )
