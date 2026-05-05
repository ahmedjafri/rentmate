"""Eval: agent creates suggestions from document data.

When the agent is told about an uploaded document with extracted lease
data, it should engage — surfacing suggestions, creating entities, or at
minimum acknowledging what it found.
"""
import os
from datetime import UTC, datetime

import pytest

from db.enums import TaskStepStatus
from db.models import Document, Property
from evals.conftest import get_suggestions, run_review
from services.task_service import TaskProgressStep

pytestmark = pytest.mark.eval


@pytest.fixture
def scenario_with_document(scenario_builder, db):
    """Scenario with a processed lease document attached to the task."""
    doc = Document(
        id="eval-doc-001",
        org_id=1,
        creator_id=1,
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

    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    sb.add_task(
        title="Review uploaded document",
        category="leasing",
        context_body=(
            f"User uploaded document: test-lease.pdf (ID: {doc.id})\n"
            f"The document has been processed. Extracted data shows a lease at "
            f"1234 Acme Lane for $2795/month. No tenant name was found in the document.\n"
            f"Review the extraction and create appropriate suggestions."
        ),
        goal=(
            "Review the uploaded lease document, surface what was extracted, "
            "and confirm with the manager what records to create before any "
            "entity is committed."
        ),
        steps=[
            TaskProgressStep(
                key="review_extraction",
                label="Review the extracted lease fields for completeness",
                status=TaskStepStatus.ACTIVE,
            ),
            TaskProgressStep(
                key="ask_for_missing_info",
                label="Ask the manager for any missing fields (e.g. tenant name)",
                status=TaskStepStatus.PENDING,
            ),
            TaskProgressStep(
                key="propose_records",
                label="Propose property/tenant/lease records for approval",
                status=TaskStepStatus.PENDING,
            ),
            TaskProgressStep(
                key="confirm_created",
                label="Confirm with the manager the records were created correctly",
                status=TaskStepStatus.PENDING,
            ),
        ],
    )
    return {**sb.build(), "document": doc}


def test_agent_engages_with_document(scenario_with_document, db, mock_sms, autonomous_mode):
    """Agent should take action — suggestions, entities, or evidence of engagement."""
    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")

    task = scenario_with_document["task"]
    task_id = task.id
    run_review(db, task)

    suggestions = get_suggestions(db, task_id)
    created_property = (
        db.query(Property).filter(Property.address_line1.ilike("%1234 Acme Lane%")).first()
    )

    # Production review writes a summary message into the task's AI conversation;
    # accept any of: suggestions queued, property created, or evidence the agent
    # actually engaged with the document.
    assert len(suggestions) > 0 or created_property is not None, (
        f"Agent didn't engage with the document data. "
        f"Suggestions: {[s.action_payload for s in suggestions]}"
    )


def test_agent_does_not_hallucinate_tenant(scenario_with_document, db, mock_sms, autonomous_mode):
    """When the document has no tenant name, the agent should not fabricate one in any drafted message or suggestion."""
    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")

    task = scenario_with_document["task"]
    task_id = task.id
    run_review(db, task)

    suggestions = get_suggestions(db, task_id)
    # Names that have appeared as hallucinations in past extractions.
    bad_names = ("bob", "lisa", "zainab")
    for sg in suggestions:
        payload = sg.action_payload or {}
        haystacks = [
            (payload.get("draft_message") or "").lower(),
            (payload.get("body") or "").lower(),
            (sg.title or "").lower(),
        ]
        for haystack in haystacks:
            for name in bad_names:
                assert name not in haystack, (
                    f"Agent hallucinated tenant name '{name}' in suggestion: {haystack[:300]}"
                )
