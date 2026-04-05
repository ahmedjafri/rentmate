"""
Integration test: automation simulation → task creation → vendor conversation
→ vendor chat → task resolution.

Validates the full lifecycle that a property manager and vendor go through
when an automation suggests a task with a vendor assignment.
"""
import pytest
import os
import uuid
from datetime import datetime
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from main import app
from db.models import (
    Base, Conversation, ConversationParticipant, ConversationType,
    ExternalContact, Message, MessageType, ParticipantType, Task,
)
from gql.schema import schema
from gql.services.vendor_service import VendorService
from gql.types import CreateVendorInput
from handlers.deps import get_db

FAKE_USER = {"id": "test-user-id", "email": "admin@test.com"}


def _gql_context(db):
    return {"db_session": db, "user": FAKE_USER}


def _make_token():
    import jwt
    from backends.local_auth import DEFAULT_USER_ID
    return jwt.encode(
        {"sub": DEFAULT_USER_ID, "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {_make_token()}"}


class TestTaskVendorFlow:
    """Full lifecycle: simulate → create task → vendor conversation → chat → resolve."""

    def _create_vendor(self, db, name="Acme Plumbing"):
        vendor = VendorService.create_vendor(
            db, CreateVendorInput(name=name, email="vendor@test.com", vendor_type="Plumber", contact_method="email"),
        )
        # Capture scalar fields now — the ORM object may be detached later
        return {"id": str(vendor.id), "name": vendor.name}

    def _create_task_via_endpoint(self, client, db, vendor, subject="Fix leaky faucet",
                                   autonomy="autonomous"):
        """POST /automations/simulate/create-task with a vendor assignment."""
        from gql.services.settings_service import _AUTONOMY_MODES, _DEFAULT_MODE
        mode = _AUTONOMY_MODES.get(autonomy, _DEFAULT_MODE)
        auto_cfg = {"checks": {"test_plumbing": {"preferred_vendor_id": vendor["id"]}}}
        with patch("handlers.automations.SessionLocal") as mock_sl, \
             patch("handlers.automations._load_automation_config", return_value=auto_cfg), \
             patch("gql.services.settings_service.get_autonomy_for_category", return_value=autonomy), \
             patch("gql.services.settings_service.get_task_mode_for_category", return_value=mode), \
             patch("llm.vendor_outreach.generate_vendor_outreach", return_value="Hi, are you available for this job?"):
            mock_sl.session_factory.return_value = db
            response = client.post(
                "/automations/simulate/create-task",
                json={
                    "subject": subject,
                    "body": "Kitchen faucet is dripping.",
                    "category": "plumbing",
                    "urgency": "medium",
                    "property_id": None,
                    "unit_id": None,
                    "automation_key": "test_plumbing",
                },
                headers=AUTH,
            )
        return response

    # -- 1. Task creation with vendor assignment --------------------------

    def test_create_task_with_vendor_returns_ok(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        response = self._create_task_via_endpoint(client, db, vendor)
        assert response.status_code == 200, response.json()
        data = response.json()
        assert data["ok"] is True

    def test_task_has_external_conversation(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_endpoint(client, db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        assert task is not None
        assert task.ai_conversation_id is not None, "AI conversation should be set"
        assert task.external_conversation_id is not None, "External conversation should be set"

    def test_external_conversation_is_vendor_type(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_endpoint(client, db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        ext_convo = db.get(Conversation, task.external_conversation_id)
        assert ext_convo is not None
        assert ext_convo.conversation_type == ConversationType.VENDOR

    def test_vendor_is_participant_on_external_conversation(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_endpoint(client, db, vendor)

        task = db.query(Task).filter(Task.title == "Fix leaky faucet").first()
        participants = db.query(ConversationParticipant).filter(
            ConversationParticipant.conversation_id == task.external_conversation_id,
        ).all()
        assert len(participants) == 1
        assert participants[0].external_contact_id == vendor["id"]
        assert participants[0].participant_type == ParticipantType.EXTERNAL_CONTACT

    def test_vendor_metadata_stored_in_ai_conversation(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_endpoint(client, db, vendor)

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
        self._create_task_via_endpoint(client, db, vendor)

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
        self._create_task_via_endpoint(client, db, vendor)

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
        with patch("handlers.automations.SessionLocal") as mock_sl:
            mock_sl.session_factory.return_value = db
            response = client.post(
                "/automations/simulate/create-task",
                json={
                    "subject": "Gutter cleaning",
                    "body": "Gutters need cleaning",
                    "category": "maintenance",
                    "urgency": "low",
                    "property_id": None,
                    "unit_id": None,
                },
                headers=AUTH,
            )
        assert response.status_code == 200
        task = db.query(Task).filter(Task.title == "Gutter cleaning").first()
        assert task is not None
        assert task.external_conversation_id is None

    # -- 5. Task resolution -----------------------------------------------

    def test_resolve_task_via_graphql(self, db):
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_endpoint(client, db, vendor)

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

    # -- 6. Reuses existing vendor conversation ---------------------------

    def test_second_task_reuses_vendor_conversation(self, db):
        """Two tasks for the same vendor should share the external conversation."""
        client = TestClient(app)
        vendor = self._create_vendor(db)
        self._create_task_via_endpoint(client, db, vendor, subject="Job A")
        self._create_task_via_endpoint(client, db, vendor, subject="Job B")

        task_a = db.query(Task).filter(Task.title == "Job A").first()
        task_b = db.query(Task).filter(Task.title == "Job B").first()
        assert task_a.external_conversation_id == task_b.external_conversation_id, (
            "Both tasks should reuse the same vendor conversation"
        )
