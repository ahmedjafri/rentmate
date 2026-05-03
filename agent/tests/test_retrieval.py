from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from agent.retrieval import RankedContextItem, RetrievalRequest, _llm_rerank, retrieve_context
from db.enums import TaskCategory, TaskMode, TaskStatus, Urgency
from db.models import (
    Conversation,
    ConversationType,
    Document,
    DocumentTag,
    Lease,
    Message,
    MessageType,
    ParticipantType,
    Property,
    Task,
    Tenant,
    Unit,
    User,
)
from services.number_allocator import NumberAllocator


def _add_property(db, property_id: str = "prop-acme") -> Property:
    property_row = Property(
        id=property_id,
        org_id=1,
        creator_id=1,
        address_line1="1234 Acme Lane",
        property_type="single_family",
        source="manual",
    )
    db.add(property_row)
    db.flush()
    return property_row


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
    db.add_all([property_row, unit_row, tenant])
    db.flush()
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Create property and tenant from lease",
        category=TaskCategory.LEASING,
        task_status=TaskStatus.ACTIVE,
        task_mode=TaskMode.MANUAL,
        urgency=Urgency.MEDIUM,
    )
    db.add(task)
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

    with patch("agent.retrieval._resolve_rerank_client_config", return_value=("fake-mini", "key", "http://example.test")), \
         patch("agent.retrieval.OpenAI", FakeOpenAI):
        reranked = _llm_rerank(
            RetrievalRequest(surface="dev", intent="answer_question", query="Can we evict Bob F", limit=5),
            ranked,
        )

    assert reranked[0].source_type == "tenant"
    assert "llm rerank" in reranked[0].reasons


def test_conversation_memory_uses_notes_not_raw_chat(db):
    _add_property(db)
    conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Chat with RentMate",
        conversation_type=ConversationType.USER_AI,
        property_id="prop-acme",
    )
    db.add(conv)
    db.flush()

    db.add_all([
        Message(
            org_id=1,
            conversation_id=conv.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            sender_name="You",
            body="I don't want you to create a suggestion for doing this",
            message_type=MessageType.MESSAGE,
            is_ai=False,
        ),
        Message(
            org_id=1,
            conversation_id=conv.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            sender_name="RentMate",
            body="I've created the suggestion for drafting the notice.",
            message_type=MessageType.ACTION,
            is_ai=True,
            meta={
                "action_card": {
                    "kind": "suggestion",
                    "title": "Draft 14-Day Pay or Vacate Notice for Bob Ferguson",
                    "summary": "Formal notice draft request tied to February and March 2026 unpaid rent.",
                }
            },
        ),
    ])
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="chat",
        intent="account_overview",
        query="create a draft",
        property_id="prop-acme",
        limit=10,
    ))

    conversation_items = [item for item in bundle.items if item.source_type == "conversation_note"]
    assert conversation_items
    top = conversation_items[0]
    assert "User preference:" in top.content
    assert "AI note:" in top.content
    assert "You:" not in top.content
    assert "RentMate:" not in top.content
    assert "I've created the suggestion for drafting the notice." not in top.content


def test_explicit_anti_suggestion_query_downranks_suggestion_and_task_notes(db):
    _add_property(db)
    suggestion_conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Draft 14-Day Pay or Vacate Notice for Bob Ferguson",
        conversation_type=ConversationType.SUGGESTION_AI,
        property_id="prop-acme",
    )
    user_conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Chat with RentMate",
        conversation_type=ConversationType.USER_AI,
        property_id="prop-acme",
    )
    db.add_all([suggestion_conv, user_conv])
    db.flush()

    db.add(Message(
        org_id=1,
        conversation_id=suggestion_conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        sender_name="RentMate",
        body="Draft 14-Day Pay or Vacate Notice for Bob Ferguson",
        message_type=MessageType.ACTION,
        is_ai=True,
        meta={
            "action_card": {
                "kind": "suggestion",
                "title": "Draft 14-Day Pay or Vacate Notice for Bob Ferguson",
                "summary": "Suggestion path for a formal notice.",
            }
        },
    ))
    db.add(Message(
        org_id=1,
        conversation_id=user_conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        sender_name="You",
        body="dont create a suggestion, create the draft",
        message_type=MessageType.MESSAGE,
        is_ai=False,
    ))
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="chat",
        intent="account_overview",
        query="dont create a suggestion, create the draft",
        property_id="prop-acme",
        limit=10,
    ))

    conversation_items = [item for item in bundle.items if item.source_type == "conversation_note"]
    assert conversation_items
    assert conversation_items[0].metadata["conversation_type"] == "user_ai"


def test_retrieval_skips_transient_tool_failure_action_card_notes(db):
    _add_property(db)
    user_conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Chat with RentMate",
        conversation_type=ConversationType.USER_AI,
        property_id="prop-acme",
    )
    db.add(user_conv)
    db.flush()

    db.add(Message(
        org_id=1,
        conversation_id=user_conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        sender_name="RentMate",
        body="Create 14-Day Pay or Vacate Notice for Bob Ferguson",
        message_type=MessageType.ACTION,
        is_ai=True,
        meta={
            "action_card": {
                "kind": "suggestion",
                "title": "Create 14-Day Pay or Vacate Notice for Bob Ferguson",
                "summary": (
                    "**Technical Issue:** The PDF rendering system is currently unavailable, "
                    "preventing automated document creation."
                ),
            }
        },
    ))
    db.add(Message(
        org_id=1,
        conversation_id=user_conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        sender_name="You",
        body="Create a brand new 14-day notice document",
        message_type=MessageType.MESSAGE,
        is_ai=False,
    ))
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="chat",
        intent="account_overview",
        query="Create a brand new 14-day notice document",
        property_id="prop-acme",
        limit=10,
    ))

    conversation_items = [item for item in bundle.items if item.source_type == "conversation_note"]
    assert conversation_items
    top = conversation_items[0]
    assert "Technical Issue" not in top.content
    assert "currently unavailable" not in top.content


def test_compliance_query_prefers_active_lease_over_expired_lease(db):
    tenant_user = User(
        id=22,
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
        id="prop-compliance-lease",
        org_id=1,
        creator_id=1,
        address_line1="1234 Acme Lane",
        property_type="single_family",
        source="manual",
    )
    unit_row = Unit(
        id="unit-compliance-lease",
        org_id=1,
        creator_id=1,
        property_id=property_row.id,
        label="Main",
    )
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    db.add_all([property_row, unit_row, tenant])
    db.flush()

    db.add_all([
        Lease(
            id="lease-expired",
            org_id=1,
            creator_id=1,
            tenant_id=tenant.id,
            unit_id=unit_row.id,
            property_id=property_row.id,
            start_date=date(2023, 1, 1),
            end_date=date(2024, 1, 1),
            rent_amount=2400,
            payment_status="current",
        ),
        Lease(
            id="lease-active",
            org_id=1,
            creator_id=1,
            tenant_id=tenant.id,
            unit_id=unit_row.id,
            property_id=property_row.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            rent_amount=2795,
            payment_status="current",
        ),
    ])
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="chat",
        intent="answer_question",
        query="Who should appear as the landlord on the compliance notice for Bob Ferguson?",
        property_id=property_row.id,
        unit_id=unit_row.id,
        limit=10,
    ))

    lease_items = [item for item in bundle.items if item.source_type == "lease"]
    assert len(lease_items) >= 2
    assert lease_items[0].source_id == "lease-active"
    assert lease_items[0].metadata["is_active"] is True
    expired = next(item for item in lease_items if item.source_id == "lease-expired")
    assert expired.metadata["is_expired"] is True
    assert "expired lease blocked for compliance-sensitive facts" in expired.reasons


def test_compliance_query_prefers_current_property_context_over_old_lease_document(db):
    property_row = Property(
        id="prop-current-manager",
        org_id=1,
        creator_id=1,
        address_line1="1234 Acme Lane",
        property_type="single_family",
        source="manual",
        context="Current property manager: Dave at NewCo Property Management.",
    )
    db.add(property_row)
    db.flush()

    old_doc = Document(
        id="doc-old-lease",
        org_id=1,
        creator_id=1,
        filename="old-lease.pdf",
        document_type="lease",
        status="done",
        context="Landlord/Manager contact information: Old Landlord at Legacy Management.",
    )
    db.add(old_doc)
    db.flush()
    db.add(DocumentTag(
        org_id=1,
        document_id=old_doc.id,
        tag_type="property",
        property_id=property_row.id,
    ))
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="chat",
        intent="answer_question",
        query="Who should appear as the current manager on the compliance notice?",
        property_id=property_row.id,
        limit=10,
    ))

    property_item = next(item for item in bundle.items if item.source_type == "property")
    document_item = next(item for item in bundle.items if item.source_type == "document")
    assert property_item.final_score > document_item.final_score
    assert "current operational source preferred for identity/contact facts" in property_item.reasons
    assert "lease document treated as low-confidence for compliance-sensitive facts" in document_item.reasons


# ── NLP query-entity pre-pass ────────────────────────────────────────────────


def _seed_tenant_with_active_lease(
    db,
    *,
    first: str,
    last: str,
    property_id: str,
    address: str,
    property_name: str | None = None,
    unit_label: str = "1B",
):
    user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name=first,
        last_name=last,
        active=True,
    )
    db.add(user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=user.id)
    db.add(tenant)
    db.flush()
    prop = Property(
        id=property_id,
        org_id=1,
        creator_id=1,
        address_line1=address,
        name=property_name,
        property_type="multi_family",
        source="manual",
    )
    unit = Unit(
        id=f"unit-{property_id}-{unit_label}",
        org_id=1,
        creator_id=1,
        property_id=property_id,
        label=unit_label,
    )
    db.add_all([prop, unit])
    db.flush()
    db.add(Lease(
        id=f"lease-{tenant.id}",
        org_id=1,
        creator_id=1,
        tenant_id=tenant.id,
        unit_id=unit.id,
        property_id=property_id,
        start_date=date(2024, 1, 1),
        end_date=date(2099, 12, 31),
        rent_amount=1500,
        payment_status="current",
    ))
    db.flush()
    return tenant, unit, prop


def test_query_extracts_tenant_and_boosts_their_items_over_token_collisions(db):
    """Query 'gutter cleaning for priyas house' should rank Priya's
    note above Tyler's note even though both share 'gutter cleaning'
    tokens — because Priya is a real tenant and the heuristic now
    auto-anchors the request on her tenant_id.
    """
    priya, priya_unit, priya_prop = _seed_tenant_with_active_lease(
        db,
        first="Priya",
        last="Patel",
        property_id="prop-meadows",
        address="500 Meadow Way",
        property_name="The Meadows",
    )
    tyler, tyler_unit, tyler_prop = _seed_tenant_with_active_lease(
        db,
        first="Tyler",
        last="Brooks",
        property_id="prop-harbor",
        address="200 Harbor Way",
        property_name="Harbor View",
        unit_label="Studio A",
    )
    # A note tied to Tyler's unit (mentions "gutter cleaning" generically).
    tyler_conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Repaired bathroom fan — Studio A",
        conversation_type=ConversationType.SUGGESTION_AI,
        unit_id=tyler_unit.id,
        property_id=tyler_prop.id,
    )
    db.add(tyler_conv)
    db.flush()
    db.add(Message(
        org_id=1,
        conversation_id=tyler_conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        sender_name="RentMate",
        body="The agent wants to send a message to Tyler Brooks. Draft message: schedule gutter cleaning visit.",
        message_type=MessageType.MESSAGE,
        is_ai=True,
    ))
    db.flush()
    # A note tied to Priya's unit.
    priya_conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Schedule gutter cleaning for The Meadows (Unit 1B)",
        conversation_type=ConversationType.SUGGESTION_AI,
        unit_id=priya_unit.id,
        property_id=priya_prop.id,
    )
    db.add(priya_conv)
    db.flush()
    db.add(Message(
        org_id=1,
        conversation_id=priya_conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        sender_name="RentMate",
        body="Schedule professional gutter cleaning for The Meadows property; tenant Priya Patel in Unit 1B.",
        message_type=MessageType.MESSAGE,
        is_ai=True,
    ))
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="dev",
        intent="answer_question",
        query="We need to schedule a gutter cleaning for priyas house",
        limit=10,
    ))

    notes = [item for item in bundle.items if item.source_type == "conversation_note"]
    assert notes, "expected at least one conversation note in the result"
    top = notes[0]
    # Priya's note must outrank Tyler's despite shared token overlap.
    assert "Meadows" in (top.title or "") or "Priya" in top.content
    assert any("query-extracted tenant" in r or "query-extracted property" in r for r in top.reasons), top.reasons


def test_overlap_stopwords_no_longer_inflate_score(db):
    """'a', 'for', 'to', 'schedule' must not show up as overlap reasons."""
    _add_property(db)
    conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Generic note",
        conversation_type=ConversationType.SUGGESTION_AI,
        property_id="prop-acme",
    )
    db.add(conv)
    db.flush()
    db.add(Message(
        org_id=1,
        conversation_id=conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        sender_name="RentMate",
        body="We schedule things for tenants and we send messages to vendors.",
        message_type=MessageType.MESSAGE,
        is_ai=True,
    ))
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="dev",
        intent="answer_question",
        query="schedule a follow-up to send for the property",
        property_id="prop-acme",
        limit=5,
    ))

    for item in bundle.items:
        for reason in item.reasons:
            if reason.startswith("token overlap:"):
                listed = {t.strip() for t in reason.split(":", 1)[1].split(",")}
                noise = {"a", "for", "to", "schedule", "the", "and"}
                hits = listed & noise
                assert not hits, f"stopword leaked into overlap reason: {reason!r}"


def test_anchorless_query_returns_empty_bundle(db):
    """Cross-property/cross-tenant token overlap is misleading. If nothing
    in the query maps to a real entity AND the caller passed no
    explicit anchor, return an empty bundle so the agent gets no
    context rather than wrong context.
    """
    _add_property(db)
    conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Generic gutter note",
        conversation_type=ConversationType.SUGGESTION_AI,
        property_id="prop-acme",
    )
    db.add(conv)
    db.flush()
    db.add(Message(
        org_id=1,
        conversation_id=conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        sender_name="RentMate",
        body="Schedule gutter cleaning at the property.",
        message_type=MessageType.MESSAGE,
        is_ai=True,
    ))
    db.commit()

    bundle = retrieve_context(db, RetrievalRequest(
        surface="dev",
        intent="answer_question",
        # no entity reference — neither a tenant name nor a property name
        query="we need to schedule a gutter cleaning",
        limit=10,
    ))

    assert bundle.items == []


def test_explicit_request_entity_skips_extraction(db):
    """When the caller already passes tenant_id, we don't run NLP extraction."""
    priya, _, _ = _seed_tenant_with_active_lease(
        db,
        first="Priya",
        last="Patel",
        property_id="prop-meadows-x",
        address="500 Meadow Way",
        property_name="The Meadows X",
    )
    db.commit()

    with patch("agent.query_entities.extract_query_entities") as mock_extract:
        retrieve_context(db, RetrievalRequest(
            surface="dev",
            intent="answer_question",
            query="schedule a gutter cleaning for priyas house",
            tenant_id=str(priya.external_id),
            limit=5,
        ))
    assert not mock_extract.called
