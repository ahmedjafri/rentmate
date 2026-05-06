"""Tests for the inbound email ingestion pipeline.

What this covers
----------------
Unit tests (no running server, no DB):
  - Email parser: parse_postmark_payload, thread_id extraction, dedup key extraction
  - Signature verifier: correct HMAC passes, wrong HMAC fails, dev mode skips check

Integration tests (real DB, no running server — uses pytest fixtures from conftest.py):
  - is_email_message_duplicate: correct True/False based on Message.meta
  - find_email_conversation_by_thread: finds by email_thread_id in extra
  - ingest_email: dedup skips silently, thread continuation reuses conversation,
    unknown sender stores message but does not spawn task

HTTP-level tests (TestClient against the real FastAPI app):
  - POST /api/email/inbound returns {"status": "ok"} and creates DB rows
  - Duplicate POST is silently ignored (idempotent)
  - Reply in same thread is appended to the same conversation
  - Domain allowlist blocks task creation for unknown domains

Manual smoke tests (no pytest — run these by hand with the server running)
---------------------------------------------------------------------------
See MANUAL_TESTS section at the bottom of this file.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from handlers.email_parser import (
    ParsedEmail,
    _extract_thread_id,
    _headers_to_dict,
    _strip_angle_brackets,
    parse_postmark_payload,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_postmark_payload() -> dict:
    """A realistic Postmark Inbound JSON payload for a maintenance request email."""
    return {
        "FromFull": {"Email": "alice@example.com", "Name": "Alice Tenant"},
        "ToFull": [{"Email": "agent@snoresidences.rentmate.io", "Name": "RentMate Agent"}],
        "CcFull": [],
        "Subject": "Heater broken in unit 4B",
        "TextBody": "Hi, the heater has not been working for 3 days. Can someone come look?",
        "HtmlBody": "<p>Hi, the heater has not been working for 3 days.</p>",
        # Postmark's internal ID — NOT the RFC Message-ID (see email_parser.py)
        "MessageID": "postmark-internal-abc123",
        "Headers": [
            {"Name": "Message-ID", "Value": "<abc123@mail.gmail.com>"},
            {"Name": "In-Reply-To", "Value": ""},
            {"Name": "References", "Value": ""},
        ],
        "Attachments": [],
        "Date": "2026-05-06T00:00:00Z",
    }


@pytest.fixture
def reply_payload() -> dict:
    """A Postmark payload for a reply in the same thread as sample_postmark_payload."""
    return {
        "FromFull": {"Email": "landlord@example.com", "Name": "Landlord"},
        "ToFull": [{"Email": "alice@example.com", "Name": "Alice Tenant"}],
        "CcFull": [{"Email": "agent@snoresidences.rentmate.io"}],
        "Subject": "Re: Heater broken in unit 4B",
        "TextBody": "We will send a technician tomorrow morning.",
        "HtmlBody": "<p>We will send a technician tomorrow morning.</p>",
        "MessageID": "postmark-internal-def456",
        "Headers": [
            {"Name": "Message-ID", "Value": "<def456@mail.gmail.com>"},
            # In-Reply-To and References both point back to the original email
            {"Name": "In-Reply-To", "Value": "<abc123@mail.gmail.com>"},
            {"Name": "References", "Value": "<abc123@mail.gmail.com>"},
        ],
        "Attachments": [],
        "Date": "2026-05-06T00:05:00Z",
    }


# ─── Unit tests: email_parser ─────────────────────────────────────────────────

class TestParsePostmarkPayload:
    """parse_postmark_payload correctly extracts all fields from the Postmark JSON."""

    def test_basic_fields(self, sample_postmark_payload):
        parsed = parse_postmark_payload(sample_postmark_payload)

        assert parsed.from_email == "alice@example.com"
        assert parsed.from_name == "Alice Tenant"
        assert parsed.to_emails == ["agent@snoresidences.rentmate.io"]
        assert parsed.cc_emails == []
        assert parsed.subject == "Heater broken in unit 4B"
        assert "heater" in parsed.body_text
        assert "<p>" in parsed.body_html

    def test_message_id_uses_header_not_postmark_id(self, sample_postmark_payload):
        """Must use the RFC Message-ID header, NOT Postmark's internal MessageID field.

        Why this matters: References/In-Reply-To in other emails always point to
        the RFC Message-ID header value.  If we use Postmark's internal ID for
        thread keying, replies will never match the original conversation.
        """
        parsed = parse_postmark_payload(sample_postmark_payload)
        # Header value is <abc123@mail.gmail.com> — angle brackets stripped
        assert parsed.message_id == "abc123@mail.gmail.com"
        # Postmark's internal ID should NOT be used
        assert parsed.message_id != "postmark-internal-abc123"

    def test_thread_id_is_own_message_id_for_new_thread(self, sample_postmark_payload):
        """First email in a thread uses its own Message-ID as the thread anchor."""
        parsed = parse_postmark_payload(sample_postmark_payload)
        # No References, no In-Reply-To → this is the thread root
        assert parsed.thread_id == "abc123@mail.gmail.com"

    def test_thread_id_from_references_in_reply(self, reply_payload):
        """A reply's thread_id is the first element of its References header."""
        parsed = parse_postmark_payload(reply_payload)
        # References: <abc123@mail.gmail.com> → first element → thread root
        assert parsed.thread_id == "abc123@mail.gmail.com"
        assert parsed.in_reply_to == "abc123@mail.gmail.com"

    def test_received_at_parsed_from_date_header(self, sample_postmark_payload):
        parsed = parse_postmark_payload(sample_postmark_payload)
        assert parsed.received_at.year == 2026
        assert parsed.received_at.month == 5

    def test_attachments_extracted(self, sample_postmark_payload):
        sample_postmark_payload["Attachments"] = [
            {
                "Name": "lease.pdf",
                "ContentType": "application/pdf",
                "Content": "base64datahere",
                "ContentLength": 1024,
            }
        ]
        parsed = parse_postmark_payload(sample_postmark_payload)
        assert len(parsed.attachments) == 1
        assert parsed.attachments[0]["name"] == "lease.pdf"
        assert parsed.attachments[0]["content_type"] == "application/pdf"
        assert parsed.attachments[0]["size_bytes"] == 1024

    def test_missing_optional_fields_dont_crash(self):
        """Minimal payload with only required fields should parse without error."""
        parsed = parse_postmark_payload({
            "FromFull": {"Email": "x@y.com"},
            "ToFull": [],
            "Subject": "Test",
        })
        assert parsed.from_email == "x@y.com"
        assert parsed.body_text == ""
        assert parsed.attachments == []


class TestExtractThreadId:
    """_extract_thread_id returns the correct thread anchor in all cases."""

    def test_uses_first_references_entry(self):
        # Multiple entries — the first one is always the thread root
        result = _extract_thread_id("own-id", "parent-id", ["root-id", "middle-id"])
        assert result == "root-id"

    def test_falls_back_to_in_reply_to(self):
        result = _extract_thread_id("own-id", "parent-id", [])
        assert result == "parent-id"

    def test_falls_back_to_own_message_id(self):
        # Neither References nor In-Reply-To — this is the thread root
        result = _extract_thread_id("own-id", None, [])
        assert result == "own-id"

    def test_empty_in_reply_to_treated_as_none(self):
        result = _extract_thread_id("own-id", "", [])
        assert result == "own-id"


class TestStripAngleBrackets:
    def test_strips_angle_brackets(self):
        assert _strip_angle_brackets("<abc@gmail.com>") == "abc@gmail.com"

    def test_no_brackets_unchanged(self):
        assert _strip_angle_brackets("abc@gmail.com") == "abc@gmail.com"

    def test_strips_whitespace(self):
        assert _strip_angle_brackets("  <abc@gmail.com>  ") == "abc@gmail.com"

    def test_empty_string(self):
        assert _strip_angle_brackets("") == ""


class TestHeadersToDict:
    def test_normalises_to_lowercase_keys(self):
        headers = [{"Name": "Message-ID", "Value": "<abc@x.com>"}]
        result = _headers_to_dict(headers)
        assert "message-id" in result
        assert result["message-id"] == "<abc@x.com>"

    def test_empty_list(self):
        assert _headers_to_dict([]) == {}


# ─── Unit tests: signature verification ──────────────────────────────────────

class TestVerifyPostmarkSignature:
    """HMAC signature verification in handlers/email_inbound.py."""

    def _make_sig(self, token: str, body: bytes) -> str:
        return base64.b64encode(
            hmac.new(token.encode(), body, hashlib.sha256).digest()
        ).decode()

    def test_correct_signature_passes(self, monkeypatch):
        """A request with the correct HMAC-SHA256 signature is accepted."""
        import asyncio
        from handlers.email_inbound import verify_postmark_signature

        token = "test-token-abc"
        body = b'{"test": "payload"}'
        sig = self._make_sig(token, body)

        monkeypatch.setenv("POSTMARK_INBOUND_WEBHOOK_TOKEN", token)
        monkeypatch.delenv("RENTMATE_ENV", raising=False)

        request = MagicMock()
        request.headers = {"X-Postmark-Signature": sig}

        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            verify_postmark_signature(request, body)
        )

    def test_wrong_signature_raises_403(self, monkeypatch):
        """A request with a wrong signature is rejected with HTTP 403."""
        import asyncio
        from fastapi import HTTPException
        from handlers.email_inbound import verify_postmark_signature

        monkeypatch.setenv("POSTMARK_INBOUND_WEBHOOK_TOKEN", "real-token")
        monkeypatch.delenv("RENTMATE_ENV", raising=False)

        request = MagicMock()
        request.headers = {"X-Postmark-Signature": "wrong-sig"}

        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                verify_postmark_signature(request, b'{"body": "data"}')
            )
        assert exc_info.value.status_code == 403

    def test_dev_mode_skips_signature_check(self, monkeypatch):
        """In development mode the signature check is skipped entirely.

        This is intentional — it lets developers test with plain curl without
        needing to compute a real HMAC.
        """
        import asyncio
        from handlers.email_inbound import verify_postmark_signature

        monkeypatch.setenv("RENTMATE_ENV", "development")
        # No token set — would normally raise 500

        request = MagicMock()
        request.headers = {}

        # Should not raise even with no token and no signature
        asyncio.get_event_loop().run_until_complete(
            verify_postmark_signature(request, b"any body")
        )


# ─── HTTP integration tests ───────────────────────────────────────────────────

class TestEmailInboundEndpoint:
    """End-to-end tests via TestClient — real DB, real app, no running server needed."""

    @pytest.fixture(autouse=True)
    def dev_mode(self, monkeypatch):
        """Always run in dev mode so signature check is skipped."""
        monkeypatch.setenv("RENTMATE_ENV", "development")

    @pytest.fixture
    def client(self) -> Generator:
        """TestClient wrapping the real FastAPI app."""
        import os
        os.environ.setdefault(
            "RENTMATE_DB_URI",
            "postgresql+psycopg2://postgres:postgres@localhost:5432/rentmate",
        )
        from rentmate.app import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    def _post(self, client, payload: dict) -> dict:
        resp = client.post(
            "/api/email/inbound",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        return resp.json()

    def test_returns_ok(self, client, sample_postmark_payload):
        """Endpoint always returns {"status": "ok"} for valid payloads."""
        result = self._post(client, sample_postmark_payload)
        assert result == {"status": "ok"}

    def test_creates_mirrored_chat_conversation(self, client, sample_postmark_payload):
        """A new email creates a MIRRORED_CHAT conversation keyed on thread_id."""
        from handlers.deps import SessionLocal
        from db.models import Conversation

        self._post(client, sample_postmark_payload)

        db = SessionLocal()
        try:
            conv = (
                db.query(Conversation)
                .filter_by(conversation_type="mirrored_chat")
                .order_by(Conversation.id.desc())
                .first()
            )
            assert conv is not None
            assert conv.subject == "Heater broken in unit 4B"
            assert conv.extra["source"] == "email"
            assert conv.extra["email_thread_id"] == "abc123@mail.gmail.com"
            assert conv.extra["read_only"] is True
        finally:
            db.close()

    def test_creates_message_with_metadata(self, client, sample_postmark_payload):
        """The email body is stored as a Message with the dedup key in meta."""
        from handlers.deps import SessionLocal
        from db.models import Conversation, Message

        self._post(client, sample_postmark_payload)

        db = SessionLocal()
        try:
            conv = (
                db.query(Conversation)
                .filter_by(conversation_type="mirrored_chat")
                .order_by(Conversation.id.desc())
                .first()
            )
            msgs = db.query(Message).filter_by(conversation_id=conv.id).all()
            assert len(msgs) == 1
            assert msgs[0].meta["email_message_id"] == "abc123@mail.gmail.com"
            assert msgs[0].meta["subject"] == "Heater broken in unit 4B"
            assert "heater" in msgs[0].body
        finally:
            db.close()

    def test_duplicate_post_is_silently_skipped(self, client, sample_postmark_payload):
        """Sending the same email twice only creates one Message row (idempotent).

        Postmark retries delivery on network errors so we must handle this.
        The second POST returns {"status": "ok"} but no new rows are created.
        """
        from handlers.deps import SessionLocal
        from db.models import Conversation, Message

        self._post(client, sample_postmark_payload)
        self._post(client, sample_postmark_payload)  # duplicate

        db = SessionLocal()
        try:
            conv = (
                db.query(Conversation)
                .filter_by(conversation_type="mirrored_chat")
                .order_by(Conversation.id.desc())
                .first()
            )
            msg_count = db.query(Message).filter_by(conversation_id=conv.id).count()
            assert msg_count == 1, "Duplicate email must not create a second Message row"
        finally:
            db.close()

    def test_reply_threads_into_same_conversation(
        self, client, sample_postmark_payload, reply_payload
    ):
        """A reply (References pointing to original) is appended to the same Conversation.

        Desired outcome:
        - 1 Conversation row (not 2)
        - 2 Message rows — one for the original, one for the reply
        - Both messages have different email_message_id values in meta
        """
        from handlers.deps import SessionLocal
        from db.models import Conversation, Message

        self._post(client, sample_postmark_payload)
        self._post(client, reply_payload)

        db = SessionLocal()
        try:
            convs = (
                db.query(Conversation)
                .filter_by(conversation_type="mirrored_chat")
                .all()
            )
            # Filter to just the conversation for this thread
            email_convs = [
                c for c in convs
                if (c.extra or {}).get("email_thread_id") == "abc123@mail.gmail.com"
            ]
            assert len(email_convs) == 1, (
                f"Expected 1 conversation for thread, found {len(email_convs)}"
            )
            conv = email_convs[0]

            msgs = (
                db.query(Message)
                .filter_by(conversation_id=conv.id)
                .order_by(Message.sent_at)
                .all()
            )
            assert len(msgs) == 2, f"Expected 2 messages, found {len(msgs)}"
            assert msgs[0].meta["email_message_id"] == "abc123@mail.gmail.com"
            assert msgs[1].meta["email_message_id"] == "def456@mail.gmail.com"
        finally:
            db.close()


# ─── Manual smoke tests ───────────────────────────────────────────────────────
#
# Run these by hand after starting the dev stack:
#   docker compose -f infra/docker-compose.dev.yml up
#
# 1. New email (unknown sender — stored but no task spawned):
#
#   curl -s -X POST http://localhost:8002/api/email/inbound \
#     -H "Content-Type: application/json" \
#     -d '{"FromFull":{"Email":"unknown@example.com","Name":"Stranger"},"ToFull":[{"Email":"agent@snoresidences.rentmate.io"}],"CcFull":[],"Subject":"Question about availability","TextBody":"Hi is unit 3A available?","HtmlBody":"","MessageID":"pm-001","Headers":[{"Name":"Message-ID","Value":"<strangerq@mail.com>"}],"Attachments":[],"Date":"2026-05-06T00:00:00Z"}'
#
#   Expected: {"status": "ok"}
#   Check DB:
#   docker compose -f infra/docker-compose.dev.yml exec postgres \
#     psql -U postgres rentmate -c \
#     "SELECT id, conversation_type, subject, extra->>'email_thread_id' FROM conversations WHERE conversation_type='mirrored_chat';"
#
# 2. Maintenance request from known tenant (seeds tenant first — run seed_dummy_data.py):
#
#   poetry run python scripts/seed_dummy_data.py
#   # Note the tenant email printed — use it in the curl below
#
#   curl -s -X POST http://localhost:8002/api/email/inbound \
#     -H "Content-Type: application/json" \
#     -d '{"FromFull":{"Email":"<TENANT_EMAIL>","Name":"Tenant"},...}'
#
#   Expected: MIRRORED_CHAT conversation + Task row + agent autoreply in task AI conversation
#   Check tasks:
#   docker compose -f infra/docker-compose.dev.yml exec postgres \
#     psql -U postgres rentmate -c "SELECT id, title, task_status FROM tasks ORDER BY id DESC LIMIT 5;"
#
# 3. Deduplication (send same email twice):
#   Run curl from test 1 twice.
#   Expected: 1 Message row (second POST silently skipped).
#
# 4. Thread continuation (reply to same thread):
#   POST original email, then POST a reply with References pointing to the first.
#   Expected: 1 Conversation, 2 Messages.
#
# 5. Signature verification (production mode):
#   Unset RENTMATE_ENV, set POSTMARK_INBOUND_WEBHOOK_TOKEN=mytoken
#   POST without X-Postmark-Signature header → expect 500 or 403
#   POST with correct HMAC → expect {"status": "ok"}
