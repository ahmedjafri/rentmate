import unittest
import unittest.mock
from unittest.mock import AsyncMock, Mock, patch
import pytest
import uuid
import httpx
from fastapi.testclient import TestClient
from main import app
from handlers.chat import send_sms_reply, is_in_whitelist
from handlers.deps import get_db
from db.models import (
    Tenant,
    Conversation,
    ConversationParticipant,
    Message,
)
from db.utils import normalize_phone

MOCK_AGENT_REPLY = "This is a mock response."
MOCK_AGENT_ID = "mock-agent-id"


@pytest.mark.usefixtures("db")
class TestDialpadWebhook(unittest.TestCase):

    def setUp(self):
        """Set up the test client for the FastAPI app."""
        self.client = TestClient(app)
        # --------------------------------------------------
        # Seed DB so sms_router can resolve the tenant
        # --------------------------------------------------
        self.from_number = "5550001234"   # tenant
        self.to_number = "5559876543"     # admin

        self.tenant = Tenant(
            first_name="Test",
            last_name="Tenant",
            email=None,
            phone=normalize_phone(self.from_number),
        )
        self.db.add(self.tenant)
        self.db.flush()

    @patch('handlers.chat.send_via_channel', new_callable=AsyncMock)
    @patch('handlers.chat.chat_with_agent', new_callable=AsyncMock, return_value=MOCK_AGENT_REPLY)
    @patch('handlers.chat.agent_registry.ensure_agent', return_value=MOCK_AGENT_ID)
    def test_handle_new_message_with_mocked_agent(
        self, mock_ensure, mock_chat, mock_send_via_channel
    ):
        app.dependency_overrides[get_db] = lambda: self.db

        # Mock the sms_router to resolve our tenant
        mock_sms_router = Mock()
        mock_sms_router.resolve.return_value = ("default-account", self.tenant, "inbound")

        payload = {
            "from_number": self.from_number,
            "to_number": [self.to_number],
            "text": "Hello, how can I help?",
        }

        with patch('backends.wire.sms_router', mock_sms_router), \
             patch('handlers.chat.PHONE_WHITELIST', [self.from_number]):
            response = self.client.post("/dialpad-webhook", json=payload)

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        # Agent was called with the right session key and messages
        mock_chat.assert_called_once()
        _, call_session, call_messages = mock_chat.call_args.args
        self.assertIn("sms:", call_session)
        self.assertEqual(call_messages[-1]["role"], "user")
        self.assertEqual(call_messages[-1]["content"], "Hello, how can I help?")

        # send_via_channel was called
        mock_send_via_channel.assert_called_once()

        # DB assertions: tenant conversation + message persisted
        conv = (
            self.db.query(Conversation)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .filter(ConversationParticipant.tenant_id == self.tenant.id)
            .one()
        )
        self.assertFalse(conv.is_archived)
        self.assertIsNone(conv.task_id)

        msg = (
            self.db.query(Message)
            .filter(
                Message.conversation_id == conv.id,
                Message.sender_tenant_id == self.tenant.id,
            )
            .one()
        )
        self.assertEqual(msg.body, payload["text"])
        self.assertEqual(msg.meta.get("source"), "dialpad")
        self.assertEqual(msg.meta.get("direction"), "inbound")

        app.dependency_overrides = {}

    @patch('handlers.chat.send_via_channel', new_callable=AsyncMock)
    @patch('handlers.chat.chat_with_agent', new_callable=AsyncMock, return_value=MOCK_AGENT_REPLY)
    @patch('handlers.chat.agent_registry.ensure_agent', return_value=MOCK_AGENT_ID)
    def test_handle_existing_message_with_mocked_agent(
        self, mock_ensure, mock_chat, mock_send_via_channel
    ):
        app.dependency_overrides[get_db] = lambda: self.db

        mock_sms_router = Mock()
        mock_sms_router.resolve.return_value = ("default-account", self.tenant, "inbound")

        payload = {
            "from_number": self.from_number,
            "to_number": [self.to_number],
            "text": "Message1",
        }

        with patch('backends.wire.sms_router', mock_sms_router), \
             patch('handlers.chat.PHONE_WHITELIST', [self.from_number]):
            self.client.post("/dialpad-webhook", json=payload)

            payload2 = dict(payload)
            payload2["text"] = "Message2"
            response2 = self.client.post("/dialpad-webhook", json=payload2)

        assert response2.status_code == 200

        # Agent was called twice
        self.assertEqual(mock_chat.call_count, 2)

        # Second call included the first message as history
        _, _, messages = mock_chat.call_args.args
        user_msgs = [m for m in messages if m["role"] == "user"]
        self.assertGreaterEqual(len(user_msgs), 2)
        self.assertEqual(messages[-1]["content"], "Message2")

        app.dependency_overrides = {}

    @patch('handlers.chat.httpx.AsyncClient')
    async def test_send_sms_reply_success(self, mock_client_class):
        """
        Test the `send_sms_reply` function to ensure it makes a successful API call.
        """
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json={"status": "ok"}))

        await send_sms_reply("5559876543", "5550001234", "This is a test reply.")

        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        self.assertIn("https://dialpad.com/api/v2/sms", args[0])
        self.assertEqual(kwargs["json"]["from_number"], "5559876543")
        self.assertIn("5550001234", kwargs["json"]["to_numbers"])
        self.assertEqual(kwargs["json"]["text"], "This is a test reply.")

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
