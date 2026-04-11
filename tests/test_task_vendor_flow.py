"""
Integration test: automation simulation → task creation → vendor conversation
→ vendor chat → task resolution.

Validates the full lifecycle that a property manager and vendor go through
when an automation suggests a task with a vendor assignment.
"""
import os

from fastapi.testclient import TestClient

from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Task,
)
from gql.schema import schema
from gql.services.vendor_service import VendorService
from gql.types import CreateVendorInput
from main import app

FAKE_USER = {"id": "test-user-id", "email": "admin@test.com"}


def _gql_context(db):
    return {"db_session": db, "user": FAKE_USER}


def _make_token():
    import jwt

    DEFAULT_USER_ID = "1"  # test-only JWT sub claim
    return jwt.encode(
        {"sub": DEFAULT_USER_ID, "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {_make_token()}"}


import pytest


@pytest.mark.skip(reason="Needs rewrite for new task creation — old automation endpoints removed")
class TestTaskVendorFlow:
    """Full lifecycle: simulate → create task → vendor conversation → chat → resolve."""

    def _create_vendor(self, db, name="Acme Plumbing"):
        vendor = VendorService.create_vendor(
            db, CreateVendorInput(name=name, phone="555-0001", email="vendor@test.com", vendor_type="Plumber"),
        )
        # Capture scalar fields now — the ORM object may be detached later
        return {"id": str(vendor.id), "name": vendor.name}

    def _create_task_via_service(self, db, vendor, subject="Fix leaky faucet",
                                  autonomy="autonomous"):
        """Create a task with vendor assignment directly via TaskService."""
        from gql.types import CreateTaskInput
        task_input = CreateTaskInput(
            title=subject,
            source="manual",
            category="maintenance",
            urgency="medium",
        )
        task = TaskService.create_task(db, task_input, vendor_id=vendor["id"])
        return task

    # -- 1. Task creation with vendor assignment --------------------------

    def test_create_task_with_vendor_returns_ok(self, db):
        vendor = self._create_vendor(db)
        task = self._create_task_via_service(db, vendor)
        assert task is not None
        assert task.id is not None
        assert task.title == "Fix leaky faucet"

    def test_task_has_external_conversation(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_service(db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        assert task is not None
        assert task.ai_conversation_id is not None, "AI conversation should be set"
        assert task.external_conversation_id is not None, "External conversation should be set"

    def test_external_conversation_is_vendor_type(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_service(db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        ext_convo = db.get(Conversation, task.external_conversation_id)
        assert ext_convo is not None
        assert ext_convo.conversation_type == ConversationType.VENDOR

    def test_vendor_is_participant_on_external_conversation(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_service(db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        participants = db.query(ConversationParticipant).filter(
            ConversationParticipant.conversation_id == task.external_conversation_id,
        ).all()
        assert len(participants) == 1
        assert participants[0].user_id == int(vendor["id"])
        assert participants[0].participant_type == ParticipantType.EXTERNAL_CONTACT

    def test_vendor_metadata_stored_in_ai_conversation(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_service(db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        ai_convo = db.get(Conversation, task.ai_conversation_id)
        extra = ai_convo.extra or {}
        assert extra.get("assigned_vendor_id") == vendor["id"]
        assert extra.get("assigned_vendor_name") == vendor["name"]

    # -- 2. Vendor conversation shows up in chats page --------------------

    def test_vendor_conversation_appears_in_conversations_query(self, db):
        """The vendor conversation must be returned by the conversations(type=vendor) query."""
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_service(db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()

        result = schema.execute_sync(
            """
            query {
              conversations(conversationType: "vendor") {
                uid
                conversationType
                title
              }
            }
            """,
            context_value=_gql_context(db),
        )
        assert result.errors is None, result.errors
        convos = result.data["conversations"]
        convo_ids = [c["uid"] for c in convos]
        assert task.external_conversation_id in convo_ids, (
            f"Vendor conversation {task.external_conversation_id} not found in chats query. "
            f"Got: {convo_ids}"
        )

    def test_context_message_in_ai_conversation(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_service(db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        ai_msgs = db.query(Message).filter(
            Message.conversation_id == task.ai_conversation_id,
            Message.message_type == MessageType.CONTEXT,
        ).all()
        assert len(ai_msgs) == 1
        assert "faucet" in ai_msgs[0].body.lower()

    # -- 4. Task without vendor has no external conversation --------------

    def test_task_without_vendor_has_no_external_conversation(self, db):
        client = TestClient(app)
        from gql.types import CreateTaskInput
        task_input = CreateTaskInput(
            title="Gutter cleaning",
            description="Gutters need cleaning",
            category="maintenance",
            urgency="low",
        )
        TaskService.create_task(db, task_input)
        task = db.query(Task).filter(Task.title == "Gutter cleaning").first()
        assert task is not None
        assert task.external_conversation_id is None

    # -- 5. Task resolution -----------------------------------------------

    def test_resolve_task_via_graphql(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_service(db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        result = schema.execute_sync(
            """
            mutation($uid: String!, $status: String!) {
              updateTaskStatus(uid: $uid, status: $status) {
                uid
                taskStatus
              }
            }
            """,
            variable_values={"uid": str(task.id), "status": "resolved"},
            context_value=_gql_context(db),
        )
        assert result.errors is None, result.errors
        assert result.data["updateTaskStatus"]["taskStatus"] == "resolved"

        db.refresh(task)
        assert task.task_status == "resolved"
        assert task.resolved_at is not None

    # -- 6. Each task gets its own vendor conversation --------------------

    def test_second_task_gets_fresh_vendor_conversation(self, db):
        """Two tasks for the same vendor should get separate conversations."""
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_service(db, vendor, subject="Job A")
        self._create_task_via_service(db, vendor, subject="Job B")

        task_a = db.query(Task).filter(Task.title == "Job A").first()
        task_b = db.query(Task).filter(Task.title == "Job B").first()
        assert task_a.external_conversation_id != task_b.external_conversation_id, (
            "Each task should get its own vendor conversation"
        )
