"""Current task/vendor integration coverage.

Exercises the supported path:
1. Create a vendor
2. Create a task
3. Assign the vendor to the task
4. Verify vendor conversation wiring and GraphQL visibility
"""

from db.enums import TaskCategory, TaskSource, Urgency
from db.models import Conversation, ConversationParticipant, ConversationType, ParticipantType, Task
from gql.schema import schema
from gql.services.task_service import TaskService
from gql.services.vendor_service import VendorService
from gql.types import CreateTaskInput, CreateVendorInput

FAKE_USER = {"id": 1, "uid": "user-external-123", "email": "admin@test.com"}


def _gql_context(db):
    return {"db_session": db, "user": FAKE_USER}


def _create_vendor(db, name="Acme Plumbing"):
    return VendorService.create_vendor(
        db,
        CreateVendorInput(
            name=name,
            phone="555-0001",
            email="vendor@test.com",
            vendor_type="Plumber",
        ),
    )


def _create_task(db, title="Fix leaky faucet"):
    return TaskService.create_task(
        db,
        CreateTaskInput(
            title=title,
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
            externalConversationId
          }
        }
        """,
        variable_values={"taskId": task.id, "vendorId": vendor.external_id},
        context_value=_gql_context(db),
    )

    assert result.errors is None, result.errors
    payload = result.data["assignVendorToTask"]
    assert payload["uid"] == task.id
    assert payload["externalConversationId"] is not None

    db.expire_all()
    db_task = db.get(Task, task.id)
    ext_convo = db.get(Conversation, db_task.external_conversation_id)
    participants = db.query(ConversationParticipant).filter(
        ConversationParticipant.conversation_id == db_task.external_conversation_id,
    ).all()
    ai_convo = db.get(Conversation, db_task.ai_conversation_id)

    assert ext_convo is not None
    assert ext_convo.conversation_type == ConversationType.VENDOR
    assert len(participants) == 1
    assert participants[0].user_id == vendor.id
    assert participants[0].participant_type == ParticipantType.EXTERNAL_CONTACT
    assert (ai_convo.extra or {})["assigned_vendor_id"] == vendor.id
    assert (ai_convo.extra or {})["assigned_vendor_name"] == vendor.name


def test_vendor_conversation_is_returned_by_conversations_query(db):
    vendor = _create_vendor(db)
    task = _create_task(db)

    assign_result = schema.execute_sync(
        """
        mutation AssignVendor($taskId: Int!, $vendorId: String!) {
          assignVendorToTask(taskId: $taskId, vendorId: $vendorId) {
            externalConversationId
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
    db_task = db.get(Task, task.id)
    public_convo_uid = db.get(Conversation, db_task.external_conversation_id).external_id
    convo_ids = [row["uid"] for row in result.data["conversations"]]
    assert str(db_task.external_conversation_id) not in convo_ids
    assert public_convo_uid in convo_ids


def test_each_task_gets_its_own_vendor_conversation(db):
    vendor = _create_vendor(db)
    first = _create_task(db, title="Job A")
    second = _create_task(db, title="Job B")

    first_result = schema.execute_sync(
        """
        mutation AssignVendor($taskId: Int!, $vendorId: String!) {
          assignVendorToTask(taskId: $taskId, vendorId: $vendorId) {
            externalConversationId
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
            externalConversationId
          }
        }
        """,
        variable_values={"taskId": second.id, "vendorId": vendor.external_id},
        context_value=_gql_context(db),
    )

    assert first_result.errors is None, first_result.errors
    assert second_result.errors is None, second_result.errors
    assert first_result.data["assignVendorToTask"]["externalConversationId"] != second_result.data["assignVendorToTask"]["externalConversationId"]
