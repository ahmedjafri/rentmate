"""Current task/vendor integration coverage.

Exercises the supported path:
1. Create a vendor
2. Create a task
3. Assign the vendor to the task
4. Verify vendor conversation wiring and GraphQL visibility
"""

from datetime import UTC, datetime

from db.enums import SuggestionStatus, TaskCategory, TaskSource, Urgency
from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    ParticipantType,
    Suggestion,
    Task,
    Tenant,
    User,
)
from gql.schema import schema
from gql.types import CreateTaskInput, CreateVendorInput
from services.number_allocator import NumberAllocator
from services.task_service import TaskService
from services.task_suggestions import MessagePersonSuggestionExecutor
from services.vendor_service import VendorService

FAKE_USER = {"id": 1, "uid": "user-external-123", "email": "admin@test.com"}


def _gql_context(db):
    return {"db_session": db, "user": FAKE_USER}


def _create_vendor(db, name="Acme Plumbing", email="vendor@test.com"):
    return VendorService.create_vendor(
        db,
        CreateVendorInput(
            name=name,
            phone="555-0001",
            email=email,
            vendor_type="Plumber",
        ),
    )


def _create_task(db, title="Fix leaky faucet"):
    return TaskService.create_task(
        db,
        CreateTaskInput(
            title=title,
            goal="Get the plumbing repair quoted and scheduled.",
            source=TaskSource.MANUAL,
            category=TaskCategory.MAINTENANCE,
            urgency=Urgency.MEDIUM,
        ),
    )


def test_assign_vendor_wires_vendor_conversation_and_metadata(db):
    vendor = _create_vendor(db)
    task = _create_task(db)

    result = schema.execute_sync(
        """
        mutation AssignVendor($taskId: Int!, $vendorId: String!) {
          assignVendorToTask(taskId: $taskId, vendorId: $vendorId) {
            uid
            externalConversationIds
          }
        }
        """,
        variable_values={"taskId": task.id, "vendorId": vendor.external_id},
        context_value=_gql_context(db),
    )

    assert result.errors is None, result.errors
    payload = result.data["assignVendorToTask"]
    assert payload["uid"] == task.id
    assert payload["externalConversationIds"]

    db.expire_all()
    db_task = db.get(Task, (task.org_id, task.id))
    assert db_task.external_conversations
    ext_convo = db_task.external_conversations[0]
    participants = db.query(ConversationParticipant).filter(
        ConversationParticipant.conversation_id == ext_convo.id,
    ).all()
    ai_convo = db.get(Conversation, db_task.ai_conversation_id)

    assert ext_convo is not None
    assert ext_convo.conversation_type == ConversationType.VENDOR
    # ``get_or_create_external_conversation`` now also adds the manager
    # as a participant (so they get unread receipts on inbound vendor
    # messages), so the conversation has 2 rows: the vendor + the
    # manager. Filter to the vendor participant for the assertions
    # below.
    vendor_participants = [
        p for p in participants if p.participant_type == ParticipantType.EXTERNAL_CONTACT
    ]
    assert len(vendor_participants) == 1
    assert vendor_participants[0].user_id == vendor.id
    assert (ai_convo.extra or {})["assigned_vendor_id"] == vendor.id
    assert (ai_convo.extra or {})["assigned_vendor_name"] == vendor.name


def test_vendor_conversation_is_returned_by_conversations_query(db):
    vendor = _create_vendor(db)
    task = _create_task(db)

    assign_result = schema.execute_sync(
        """
        mutation AssignVendor($taskId: Int!, $vendorId: String!) {
          assignVendorToTask(taskId: $taskId, vendorId: $vendorId) {
            externalConversationIds
          }
        }
        """,
        variable_values={"taskId": task.id, "vendorId": vendor.external_id},
        context_value=_gql_context(db),
    )
    assert assign_result.errors is None, assign_result.errors

    result = schema.execute_sync(
        """
        query {
          conversations(conversationType: VENDOR) {
            uid
            conversationType
            title
          }
        }
        """,
        context_value=_gql_context(db),
    )

    assert result.errors is None, result.errors
    db_task = db.get(Task, (task.org_id, task.id))
    ext_convo = db_task.external_conversations[0]
    public_convo_uid = ext_convo.external_id
    convo_ids = [row["uid"] for row in result.data["conversations"]]
    assert str(ext_convo.id) not in convo_ids
    assert public_convo_uid in convo_ids


def test_each_task_gets_its_own_vendor_conversation(db):
    vendor = _create_vendor(db)
    first = _create_task(db, title="Job A")
    second = _create_task(db, title="Job B")

    first_result = schema.execute_sync(
        """
        mutation AssignVendor($taskId: Int!, $vendorId: String!) {
          assignVendorToTask(taskId: $taskId, vendorId: $vendorId) {
            externalConversationIds
          }
        }
        """,
        variable_values={"taskId": first.id, "vendorId": vendor.external_id},
        context_value=_gql_context(db),
    )
    second_result = schema.execute_sync(
        """
        mutation AssignVendor($taskId: Int!, $vendorId: String!) {
          assignVendorToTask(taskId: $taskId, vendorId: $vendorId) {
            externalConversationIds
          }
        }
        """,
        variable_values={"taskId": second.id, "vendorId": vendor.external_id},
        context_value=_gql_context(db),
    )

    assert first_result.errors is None, first_result.errors
    assert second_result.errors is None, second_result.errors
    assert first_result.data["assignVendorToTask"]["externalConversationIds"] != second_result.data["assignVendorToTask"]["externalConversationIds"]


def _make_message_person_suggestion(
    db,
    *,
    task: Task,
    entity_type: str,
    entity_id: str,
    draft: str,
) -> Suggestion:
    """Create a pending message_person suggestion for a task."""
    sid = NumberAllocator.allocate_next(db, entity_type="suggestion", org_id=1)
    sugg = Suggestion(
        id=sid,
        org_id=1,
        creator_id=1,
        title=f"Message {entity_type}",
        status=SuggestionStatus.PENDING,
        task_id=task.id,
        action_payload={
            "action": "message_person",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "draft_message": draft,
        },
    )
    db.add(sugg)
    db.flush()
    return sugg


def test_multiple_vendors_on_same_task_get_distinct_conversations(db):
    """One task coordinating quotes from N vendors should produce N conversations."""
    task = _create_task(db, title="Water damage — fetch 3 quotes")

    # Three independent vendor contacts.
    vendors = [
        _create_vendor(db, name="Acme Plumbing", email="acme@test.com"),
        _create_vendor(db, name="Blue Ridge Plumbing", email="blueridge@test.com"),
        _create_vendor(db, name="City Wide Plumbers", email="citywide@test.com"),
    ]

    # Reaching out to each vendor is driven by a message_person suggestion.
    for idx, vendor in enumerate(vendors):
        sugg = _make_message_person_suggestion(
            db,
            task=task,
            entity_type="vendor",
            entity_id=vendor.external_id,
            draft=f"Quote request #{idx + 1}",
        )
        MessagePersonSuggestionExecutor(db).execute(sugg.id, "message_person_send")
        db.commit()

    db.expire_all()
    db_task = db.get(Task, (task.org_id, task.id))
    vendor_convos = [
        c for c in db_task.external_conversations
        if c.conversation_type == ConversationType.VENDOR
    ]

    assert len(vendor_convos) == 3, (
        f"Expected 3 distinct vendor conversations, got {len(vendor_convos)}"
    )
    convo_ids = {c.id for c in vendor_convos}
    assert len(convo_ids) == 3, "Vendor conversations collapsed onto the same id"

    # Each conversation should have exactly one external vendor participant,
    # and the three participants should be the three distinct vendors.
    participant_user_ids: set[int] = set()
    for convo in vendor_convos:
        parts = db.query(ConversationParticipant).filter_by(
            conversation_id=convo.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
        ).all()
        assert len(parts) == 1
        participant_user_ids.add(parts[0].user_id)
    assert participant_user_ids == {v.id for v in vendors}


def test_tenant_outreach_creates_new_task_scoped_conversation(db):
    """A prior unrelated tenant chat must NOT be reused when the agent reaches
    out to that tenant in a new task — the task gets its own thread."""
    # Tenant with their own pre-existing chat (no parent_task_id).
    tenant_user = User(
        id=99,
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Pat",
        last_name="Renter",
        active=True,
    )
    db.add(tenant_user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    db.add(tenant)
    db.flush()

    prior_convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="General chat — unrelated",
        conversation_type=ConversationType.TENANT,
        is_group=False,
        is_archived=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(prior_convo)
    db.flush()
    db.add(ConversationParticipant(
        org_id=1,
        creator_id=1,
        conversation_id=prior_convo.id,
        user_id=tenant_user.id,
        participant_type=ParticipantType.TENANT,
        is_active=True,
    ))
    db.flush()
    assert prior_convo.parent_task_id is None

    # New task for something unrelated to the prior chat. Reach out to the
    # same tenant through the message_person suggestion flow.
    task = _create_task(db, title="Schedule HVAC service")
    sugg = _make_message_person_suggestion(
        db,
        task=task,
        entity_type="tenant",
        entity_id=tenant.external_id,
        draft="We'll need access Thursday morning for HVAC.",
    )
    MessagePersonSuggestionExecutor(db).execute(sugg.id, "message_person_send")
    db.commit()
    db.expire_all()

    # The prior conversation must be untouched.
    prior_refetched = db.get(Conversation, prior_convo.id)
    assert prior_refetched.parent_task_id is None

    # The task owns a fresh TENANT conversation with this tenant.
    db_task = db.get(Task, (task.org_id, task.id))
    tenant_convos = [
        c for c in db_task.external_conversations
        if c.conversation_type == ConversationType.TENANT
    ]
    assert len(tenant_convos) == 1
    assert tenant_convos[0].id != prior_convo.id
    assert tenant_convos[0].parent_task_id == task.id

    parts = db.query(ConversationParticipant).filter_by(
        conversation_id=tenant_convos[0].id,
        participant_type=ParticipantType.TENANT,
    ).all()
    assert [p.user_id for p in parts] == [tenant_user.id]
