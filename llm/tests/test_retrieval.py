from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from db.enums import TaskCategory, TaskMode, TaskStatus, Urgency
from db.models import Lease, Property, Task, Tenant, Unit, User
from llm.retrieval import RankedContextItem, RetrievalRequest, _llm_rerank, retrieve_context


def test_person_query_ranks_tenant_above_task_shell(db):
    tenant_user = User(
        id=2,
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Bob",
        last_name="Ferguson",
        active=True,
    )
    db.add(tenant_user)
    db.flush()

    property_row = Property(
        id="prop-bob",
        org_id=1,
        creator_id=1,
        address_line1="1234 Acme Lane",
        property_type="single_family",
        source="manual",
    )
    unit_row = Unit(
        id="unit-bob",
        org_id=1,
        creator_id=1,
        property_id=property_row.id,
        label="Main",
    )
    tenant = Tenant(
        org_id=1,
        creator_id=1,
        user_id=tenant_user.id,
        context="Primary tenant for the Acme Lane lease.",
    )
    task = Task(
        org_id=1,
        creator_id=1,
        title="Create property and tenant from lease",
        category=TaskCategory.LEASING,
        task_status=TaskStatus.ACTIVE,
        task_mode=TaskMode.MANUAL,
        urgency=Urgency.MEDIUM,
    )
    db.add_all([property_row, unit_row, tenant, task])
    db.flush()

    db.add(Lease(
        id="lease-bob",
        org_id=1,
        creator_id=1,
        tenant_id=tenant.id,
        unit_id=unit_row.id,
        property_id=property_row.id,
        start_date=date(2020, 8, 15),
        end_date=date(2021, 8, 15),
        rent_amount=2795,
        payment_status="current",
    ))
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="dev",
        intent="answer_question",
        query="Can we evict Bob F",
        limit=5,
    ))

    assert bundle.items
    assert bundle.items[0].source_type == "tenant"
    assert bundle.items[0].title == "Bob Ferguson"
    assert "person-centric entity overlap" in bundle.items[0].reasons


def test_llm_rerank_reorders_shortlist():
    ranked = [
        RankedContextItem(
            memory_item_id="task-1",
            source_type="task",
            source_id="1",
            entity_type="task",
            entity_id="1",
            title="Create property and tenant from lease",
            content="Task shell",
            metadata={},
            heuristic_score=1.2,
            vector_score=0.1,
            final_score=1.2,
            reasons=["triage intent prior"],
        ),
        RankedContextItem(
            memory_item_id="tenant-1",
            source_type="tenant",
            source_id="tenant-1",
            entity_type="tenant",
            entity_id="tenant-1",
            title="Bob Ferguson",
            content="Tenant: Bob Ferguson",
            metadata={},
            heuristic_score=0.2,
            vector_score=0.0,
            final_score=0.2,
            reasons=["token overlap: bob"],
        ),
    ]

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ordered_indices":[1,0],"reason":"named tenant is directly relevant"}'))]
            )

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    with patch("llm.retrieval._resolve_rerank_client_config", return_value=("fake-mini", "key", "http://example.test")), \
         patch("llm.retrieval.OpenAI", FakeOpenAI):
        reranked = _llm_rerank(
            RetrievalRequest(surface="dev", intent="answer_question", query="Can we evict Bob F", limit=5),
            ranked,
        )

    assert reranked[0].source_type == "tenant"
    assert "llm rerank" in reranked[0].reasons
