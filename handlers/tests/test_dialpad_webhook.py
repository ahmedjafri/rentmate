import unittest
import unittest.mock
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from agent.action_policy import ActionDecision
from agent.client import AgentResponse
from db.models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageType,
    Tenant,
    User,
)
from db.utils import normalize_phone
from handlers.chat import is_in_whitelist
from handlers.deps import get_db
from main import app

MOCK_AGENT_REPLY = "This is a mock response."
MOCK_AGENT_ID = "mock-agent-id"


@pytest.mark.usefixtures("db")
class TestQuoWebhook(unittest.TestCase):

    def setUp(self):
        """Set up the test client for the FastAPI app."""
        self.client = TestClient(app)
        # --------------------------------------------------
        # Seed DB so sms_router can resolve the tenant
        # --------------------------------------------------
        self.from_number = "5550001234"   # tenant
        self.to_number = "5559876543"     # admin

        tenant_user = User(
            org_id=1,
            creator_id=1,
            user_type="tenant",
            first_name="Test",
            last_name="Tenant",
            email=None,
            phone=normalize_phone(self.from_number),
            active=True,
        )
        self.db.add(tenant_user)
        self.db.flush()
        self.tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
        self.db.add(self.tenant)
        self.db.flush()

    @patch('handlers.chat.NotificationService.notify', new_callable=AsyncMock)
    @patch('agent.client.call_agent', new_callable=AsyncMock)
    @patch('handlers.chat.agent_registry.ensure_agent', return_value=MOCK_AGENT_ID)
    def test_handle_new_message_with_mocked_agent(
        self, mock_ensure, mock_chat, mock_notify
    ):
        mock_chat.return_value = AgentResponse(reply=MOCK_AGENT_REPLY, side_effects=[])
        app.dependency_overrides[get_db] = lambda: self.db

        # Mock the sms_router to resolve our tenant
        mock_sms_router = Mock()
        mock_sms_router.resolve.return_value = ("default-account", self.tenant, "inbound", "tenant")

        payload = {
            "type": "message.received",
            "data": {
                "from": self.from_number,
                "to": [self.to_number],
                "body": "Hello, how can I help?",
            },
        }

        with patch('integrations.wire.sms_router', mock_sms_router), \
             patch('handlers.chat.PHONE_WHITELIST', [self.from_number]):
            response = self.client.post("/quo-webhook", json=payload)

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        # Agent was called with the right session key and messages
        mock_chat.assert_called_once()
        call_session = mock_chat.call_args.kwargs.get("session_key", mock_chat.call_args.args[1] if len(mock_chat.call_args.args) > 1 else None)
        call_messages = mock_chat.call_args.kwargs.get("messages", mock_chat.call_args.args[2] if len(mock_chat.call_args.args) > 2 else None)
        self.assertIn("sms:", call_session)
        self.assertEqual(call_messages[-1]["role"], "user")
        self.assertEqual(call_messages[-1]["content"], "Hello, how can I help?")

        # Notification was dispatched for the tenant reply
        mock_notify.assert_called_once()

        # DB assertions: tenant conversation + message persisted
        conv = (
            self.db.query(Conversation)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .filter(ConversationParticipant.user_id == self.tenant.user_id)
            .one()
        )
        self.assertFalse(conv.is_archived)

        msg = (
            self.db.query(Message)
            .filter(
                Message.conversation_id == conv.id,
                Message.sender_id == conv.participants[0].id,
            )
            .one()
        )
        self.assertEqual(msg.body, payload["data"]["body"])
        self.assertEqual(msg.meta.get("source"), "quo")
        self.assertEqual(msg.meta.get("direction"), "inbound")

        app.dependency_overrides = {}

    @patch('handlers.chat.NotificationService.notify', new_callable=AsyncMock)
    @patch('agent.client.call_agent', new_callable=AsyncMock)
    @patch('handlers.chat.agent_registry.ensure_agent', return_value=MOCK_AGENT_ID)
    def test_handle_new_message_creates_suggestion_when_policy_blocks_send(
        self, mock_ensure, mock_chat, mock_notify
    ):
        mock_chat.return_value = AgentResponse(reply="Draft response for review.", side_effects=[])
        app.dependency_overrides[get_db] = lambda: self.db

        mock_sms_router = Mock()
        mock_sms_router.resolve.return_value = ("default-account", self.tenant, "inbound", "tenant")

        payload = {
            "type": "message.received",
            "data": {
                "from": self.from_number,
                "to": [self.to_number],
                "body": "Can you waive my late fee?",
            },
        }

        with patch('integrations.wire.sms_router', mock_sms_router), \
             patch('handlers.chat.PHONE_WHITELIST', [self.from_number]), \
             patch('agent.action_policy.evaluate_action_candidate', return_value=ActionDecision(False, "blocked", 2)):
            response = self.client.post("/quo-webhook", json=payload)

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_notify.assert_not_called()

        conv = (
            self.db.query(Conversation)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .filter(ConversationParticipant.user_id == self.tenant.user_id)
            .one()
        )
        ai_msgs = (
            self.db.query(Message)
            .filter(Message.conversation_id == conv.id, Message.is_ai.is_(True))
            .order_by(Message.id.asc())
            .all()
        )
        assert len(ai_msgs) == 1
        assert ai_msgs[0].message_type == MessageType.SUGGESTION
        assert ai_msgs[0].meta["draft_reply"] == "Draft response for review."
        assert ai_msgs[0].meta["related_task_ids"]["suggestion_id"] is not None

        app.dependency_overrides = {}

    @patch('handlers.chat.NotificationService.notify', new_callable=AsyncMock)
    @patch('agent.client.call_agent', new_callable=AsyncMock)
    @patch('handlers.chat.agent_registry.ensure_agent', return_value=MOCK_AGENT_ID)
    def test_handle_existing_message_with_mocked_agent(
        self, mock_ensure, mock_chat, mock_notify
    ):
        mock_chat.return_value = AgentResponse(reply=MOCK_AGENT_REPLY, side_effects=[])
        app.dependency_overrides[get_db] = lambda: self.db

        mock_sms_router = Mock()
        mock_sms_router.resolve.return_value = ("default-account", self.tenant, "inbound", "tenant")

        payload = {
            "type": "message.received",
            "data": {
                "from": self.from_number,
                "to": [self.to_number],
                "body": "Message1",
            },
        }

        with patch('integrations.wire.sms_router', mock_sms_router), \
             patch('handlers.chat.PHONE_WHITELIST', [self.from_number]):
            self.client.post("/quo-webhook", json=payload)

            payload2 = {"type": "message.received", "data": {**payload["data"], "body": "Message2"}}
            response2 = self.client.post("/quo-webhook", json=payload2)

        assert response2.status_code == 200

        # Agent was called twice
        self.assertEqual(mock_chat.call_count, 2)

        # Second call included the first message as history
        messages = mock_chat.call_args.kwargs.get("messages")
        user_msgs = [m for m in messages if m["role"] == "user"]
        self.assertGreaterEqual(len(user_msgs), 2)
        self.assertEqual(messages[-1]["content"], "Message2")

        app.dependency_overrides = {}

    def test_is_in_whitelist(self):
        """
        Test the `is_in_whitelist` helper function.
        """
        import handlers.chat as chat_module
        original = chat_module.PHONE_WHITELIST
        chat_module.PHONE_WHITELIST = ["5550001234", "5550009876"]
        try:
            self.assertTrue(is_in_whitelist("5550001234"))
            self.assertTrue(is_in_whitelist("5550009876"))
            self.assertFalse(is_in_whitelist("1234567890"))
            self.assertTrue(is_in_whitelist("+15550001234"))
            self.assertFalse(is_in_whitelist("5550001235"))
        finally:
            chat_module.PHONE_WHITELIST = original


if __name__ == '__main__':
    unittest.main()
