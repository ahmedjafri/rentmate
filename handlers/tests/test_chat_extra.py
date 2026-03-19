"""Tests for handlers/chat.py — /chat endpoint and helpers."""
import os
import pytest
import unittest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient
from main import app
from handlers.deps import get_db
from backends.local_auth import DEFAULT_USER_ID


def make_token():
    import jwt
    return jwt.encode(
        {"sub": DEFAULT_USER_ID, "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {make_token()}"}


# ---------------------------------------------------------------------------
# /chat
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestChatEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_requires_auth(self):
        response = self.client.post("/chat", json={"message": "hello"})
        assert response.status_code == 401

    def test_returns_reply(self):
        with (
            patch("handlers.chat.load_account_context", return_value="Account context"),
            patch("handlers.chat.chat_with_agent", new_callable=AsyncMock, return_value="Hi there!"),
        ):
            response = self.client.post(
                "/chat",
                json={"message": "Hello", "conversation_history": []},
                headers=AUTH,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["reply"] == "Hi there!"
        assert "conversation_id" in data

    def test_preserves_conversation_id(self):
        with (
            patch("handlers.chat.load_account_context", return_value="ctx"),
            patch("handlers.chat.chat_with_agent", new_callable=AsyncMock, return_value="reply"),
        ):
            response = self.client.post(
                "/chat",
                json={"message": "Hey", "conversation_id": "conv-abc", "conversation_history": []},
                headers=AUTH,
            )
        assert response.json()["conversation_id"] == "conv-abc"

    def test_agent_error_returns_502(self):
        with (
            patch("handlers.chat.load_account_context", return_value="ctx"),
            patch("handlers.chat.chat_with_agent", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        ):
            response = self.client.post(
                "/chat",
                json={"message": "fail", "conversation_history": []},
                headers=AUTH,
            )
        assert response.status_code == 502

    def test_builds_history_from_prior_messages(self):
        captured = {}

        async def _fake_chat(agent_id, session_key, messages):
            captured["messages"] = messages
            return "ok"

        with (
            patch("handlers.chat.load_account_context", return_value="system-ctx"),
            patch("handlers.chat.chat_with_agent", side_effect=_fake_chat),
        ):
            self.client.post(
                "/chat",
                json={
                    "message": "follow-up",
                    "conversation_history": [
                        {"role": "user", "content": "first message"},
                        {"role": "assistant", "content": "first reply"},
                    ],
                },
                headers=AUTH,
            )
        msgs = captured.get("messages", [])
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "first message"
        assert msgs[-1]["content"] == "follow-up"


# ---------------------------------------------------------------------------
# is_in_whitelist helper
# ---------------------------------------------------------------------------

class TestIsInWhitelist:
    def test_matches_substring(self):
        from handlers.chat import is_in_whitelist
        with patch("handlers.chat.PHONE_WHITELIST", ["+1555"]):
            assert is_in_whitelist("+15551234567") is True

    def test_no_match(self):
        from handlers.chat import is_in_whitelist
        with patch("handlers.chat.PHONE_WHITELIST", ["+1555"]):
            assert is_in_whitelist("+14161234567") is False

    def test_empty_whitelist(self):
        from handlers.chat import is_in_whitelist
        with patch("handlers.chat.PHONE_WHITELIST", []):
            assert is_in_whitelist("+15551234567") is False
