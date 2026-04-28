"""Tests for ``gql/services/extension_service.py`` — the helpers behind
the chrome-extension GraphQL surface (``searchTenants`` query and
``suggestReply`` mutation)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backends.local_auth import reset_request_context, set_request_context
from db.models import (
    AgentRun,
    Conversation,
    ConversationType,
    Lease,
    Message,
    ParticipantType,
    Property,
    Tenant,
    Unit,
    User,
)
from gql.services.extension_service import (
    _FALLBACK_REPLY,
    MirrorConversationReadOnly,
    draft_reply,
    rank_tenants,
)
from llm.client import AgentResponse


@contextmanager
def _request_scope(*, account_id=1, org_id=1):
    token = set_request_context(account_id=account_id, org_id=org_id)
    try:
        yield
    finally:
        reset_request_context(token)


def _seed_tenant(db, *, first, last, email=None, phone=None, with_lease=False):
    user = User(
        org_id=1, creator_id=1, user_type="tenant",
        first_name=first, last_name=last,
        email=email, phone=phone,
    )
    db.add(user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=user.id)
    db.add(tenant)
    db.flush()
    if with_lease:
        prop = Property(
            id=f"prop-{first.lower()}",
            org_id=1, creator_id=1,
            name="The Meadows", address_line1="1 Main St",
            property_type="multi_family",
        )
        unit = Unit(
            id=f"unit-{first.lower()}",
            org_id=1, creator_id=1,
            property_id=prop.id, label="1A",
        )
        db.add_all([prop, unit])
        db.flush()
        lease = Lease(
            id=f"lease-{first.lower()}",
            org_id=1, creator_id=1,
            tenant_id=tenant.id, property_id=prop.id, unit_id=unit.id,
            start_date=date(2024, 1, 1), end_date=date(2099, 1, 1),
            rent_amount=1500.0,
        )
        db.add(lease)
        db.flush()
    return tenant


# ─── rank_tenants ───────────────────────────────────────────────────────


def test_rank_tenants_exact_email_beats_partial_name(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson", email="marcus@example.com")
        _seed_tenant(db, first="Marcus", last="Williams", email="marcusw@example.com")

        out = rank_tenants(db, "marcus@example.com")

        # The first hit is the exact-email match (score 100). The second
        # tenant scores lower because "marcus@example.com" is a substring
        # of his email but not an exact match.
        assert len(out) >= 1
        assert out[0]["name"] == "Marcus Johnson"
        assert out[0]["score"] == 100
        if len(out) > 1:
            assert out[1]["score"] < 100


def test_rank_tenants_drops_zero_score_results(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson")
        _seed_tenant(db, first="Priya", last="Patel")

        assert rank_tenants(db, "xyzzy") == []


def test_rank_tenants_caps_at_three(db):
    with _request_scope():
        for i in range(5):
            _seed_tenant(db, first="Marcus", last=f"Last{i}")

        out = rank_tenants(db, "marcus")

        assert len(out) == 3
        assert all("Marcus" in r["name"] for r in out)


def test_rank_tenants_includes_property_and_unit_when_lease_exists(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson", with_lease=True)

        out = rank_tenants(db, "marcus")

        assert len(out) == 1
        assert out[0]["unit_label"] == "1A"
        assert out[0]["property_id"] == "prop-marcus"


def test_rank_tenants_blank_query_returns_empty(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson")
        assert rank_tenants(db, "   ") == []


# ─── draft_reply ────────────────────────────────────────────────────────


def _fake_completion(text: str):
    fake = MagicMock()
    fake.choices = [MagicMock(message=MagicMock(content=text))]
    return fake


def test_draft_reply_includes_tenant_name_in_system_prompt(db):
    with _request_scope():
        tenant = _seed_tenant(
            db, first="Marcus", last="Johnson", email="marcus@example.com",
            with_lease=True,
        )
        captured: dict = {}

        async def fake_acompletion(messages, **_kwargs):
            captured["system"] = messages[0]["content"]
            captured["user"] = messages[1]["content"]
            return _fake_completion("Hi Marcus — I'll get someone out to take a look.")

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[
                    {"sender": "Marcus", "text": "The dishwasher leaked again."},
                ],
                header_title="Dishwasher leak",
                header_description=None,
                tenant_id=str(tenant.external_id),
                property_id=None,
            ))

    assert "Marcus Johnson" in captured["system"]
    assert "Dishwasher leak" in captured["system"]
    assert result["matched_tenant"]["tenant_id"] == str(tenant.external_id)
    assert result["suggestion"].startswith("Hi Marcus")


def test_draft_reply_falls_back_on_llm_error(db):
    with _request_scope():
        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("connection refused")):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "Tenant", "text": "Hi?"}],
                header_title=None,
                header_description=None,
                tenant_id=None,
                property_id=None,
            ))

    assert result["suggestion"] == _FALLBACK_REPLY
    assert result["matched_tenant"] is None
    assert result["fallback"] is True
    # Connection-style failures get the "endpoint unreachable" message,
    # not a generic LLM error blob.
    assert result["error"] is not None
    assert "unreachable" in result["error"].lower() or "base url" in result["error"].lower()


def test_draft_reply_classifies_auth_error(db):
    """The exact error PMs hit when the OpenAI/OpenRouter key is wrong
    deserves an actionable message that names the env var."""
    class FakeAuthError(Exception):
        pass
    FakeAuthError.__name__ = "AuthenticationError"

    with _request_scope():
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=FakeAuthError("Incorrect API key provided: sk-xxx"),
        ):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
            ))

    assert result["fallback"] is True
    assert "LLM_API_KEY" in result["error"]


def test_draft_reply_success_has_no_error(db):
    """Happy path doesn't set ``error`` or ``fallback`` so the extension
    can trust the suggestion."""
    with _request_scope():
        async def fake_acompletion(*_args, **_kwargs):
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(content="A real reply."))]
            return fake

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
            ))

    assert result["suggestion"] == "A real reply."
    assert result["fallback"] is False
    assert result["error"] is None


def test_draft_reply_clamps_long_completion(db):
    with _request_scope():
        long_text = "x" * 1000

        async def fake_acompletion(*_args, **_kwargs):
            return _fake_completion(long_text)

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
            ))

    assert len(result["suggestion"]) <= 500


def test_draft_reply_unknown_tenant_id_returns_no_match(db):
    with _request_scope():
        async def fake_acompletion(*_args, **_kwargs):
            return _fake_completion("OK.")

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id="00000000-0000-0000-0000-000000000bad",
                property_id=None,
            ))

    assert result["matched_tenant"] is None


# ─── External-chat mirror + agent routing ──────────────────────────────


def _patch_call_agent(reply: str):
    """Mock the agent so ``draft_reply`` returns deterministic text without
    spinning up the real LLM. Mirrors how the chat-stream tests stub
    ``llm.client.call_agent`` in ``tests/test_chat_integration.py``."""
    async def fake_call_agent(_agent_id, *, session_key, messages, trace_context=None, **_kwargs):
        fake_call_agent.last_call = {
            "session_key": session_key,
            "messages": messages,
            "trace_context": trace_context,
        }
        return AgentResponse(reply=reply, side_effects=[])
    fake_call_agent.last_call = None
    return patch("llm.client.call_agent", side_effect=fake_call_agent), fake_call_agent


def test_draft_reply_with_thread_id_creates_mirror_conversation(db):
    """First Suggest click for a thread creates a MIRRORED_CHAT conversation
    keyed by ``external_thread_id`` and dedup-mirrors the scraped history."""
    with _request_scope():
        tenant = _seed_tenant(
            db, first="Marcus", last="Johnson", email="marcus@example.com",
            with_lease=True,
        )
        ctx, _spy = _patch_call_agent("Hi Marcus — booking a plumber for tomorrow.")
        with ctx:
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[
                    {"sender": "Marcus", "text": "The dishwasher is leaking."},
                    {"sender": "You", "text": "Got it, I'll send someone over."},
                    {"sender": "Marcus", "text": "Thanks!"},
                ],
                header_title="Dishwasher leak",
                header_description="Standing water under the unit.",
                tenant_id=str(tenant.external_id),
                property_id=None,
                external_thread_id="ext-thread:9001",
            ))

    assert result["fallback"] is False
    assert result["conversation_external_id"] is not None

    conv = db.query(Conversation).filter_by(
        external_id=result["conversation_external_id"],
    ).one()
    assert conv.conversation_type == ConversationType.MIRRORED_CHAT
    # Default ``source`` is ``chrome_extension`` when the client
    # doesn't supply a platform identifier — keeps backend rentmate
    # source-agnostic. Specific platforms pass their own value.
    assert (conv.extra or {}).get("source") == "chrome_extension"
    assert (conv.extra or {}).get("read_only") is True
    assert (conv.extra or {}).get("external_thread_id") == "ext-thread:9001"
    assert conv.subject == "Dishwasher leak"

    msgs = sorted(
        db.query(Message).filter_by(conversation_id=conv.id).all(),
        key=lambda m: (m.meta or {}).get("mirror_index", -1),
    )
    assert [m.body for m in msgs] == [
        "The dishwasher is leaking.",
        "Got it, I'll send someone over.",
        "Thanks!",
    ]
    # Sender mapping: PM tokens ("You") map to ACCOUNT_USER, others to
    # EXTERNAL_CONTACT — matters because the read-only guard and any
    # downstream UI styling key off sender_type.
    types = [m.sender_type for m in msgs]
    assert types == [
        ParticipantType.EXTERNAL_CONTACT,
        ParticipantType.ACCOUNT_USER,
        ParticipantType.EXTERNAL_CONTACT,
    ]


def test_draft_reply_repeat_call_dedups_existing_messages(db):
    """Re-clicking Suggest after the tenant sent another message only inserts
    the new turn — the first three are already mirrored."""
    with _request_scope():
        ctx, _spy = _patch_call_agent("OK.")
        history = [
            {"sender": "Marcus", "text": "The dishwasher is leaking."},
            {"sender": "You", "text": "Got it, I'll send someone over."},
            {"sender": "Marcus", "text": "Thanks!"},
        ]
        with ctx:
            asyncio.run(draft_reply(
                db,
                conversation_history=history,
                header_title="Dishwasher leak",
                header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id="ext-thread:9002",
            ))
            # Second click: same thread, one extra tenant turn appended.
            asyncio.run(draft_reply(
                db,
                conversation_history=[
                    *history,
                    {"sender": "Marcus", "text": "Plumber just left, all good."},
                ],
                header_title="Dishwasher leak",
                header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id="ext-thread:9002",
            ))

    convs = db.query(Conversation).filter_by(
        conversation_type=ConversationType.MIRRORED_CHAT,
    ).all()
    assert len(convs) == 1
    msgs = db.query(Message).filter_by(conversation_id=convs[0].id).all()
    bodies = sorted(m.body for m in msgs)
    assert bodies == [
        "Got it, I'll send someone over.",
        "Plumber just left, all good.",
        "Thanks!",
        "The dishwasher is leaking.",
    ]


def test_draft_reply_records_agent_run_against_mirror(db):
    """Routing through ``call_agent`` writes an ``AgentRun`` row whose
    ``conversation_id`` is the mirror's external_id, so DevTools groups
    extension drafts alongside chat/task agent runs."""
    with _request_scope():
        ctx, _spy = _patch_call_agent("Replied.")
        with ctx:
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id="ext-thread:9003",
            ))

    convo_uid = result["conversation_external_id"]
    assert convo_uid is not None
    runs = db.query(AgentRun).filter_by(conversation_id=convo_uid).all()
    assert len(runs) == 1
    assert runs[0].source == "extension"
    assert runs[0].status == "completed"


def test_draft_reply_session_key_uses_mirror_external_id(db):
    """``session_key`` carries the mirror's external_id so downstream
    agent state (memory, tracing) is partitioned per external thread
    instead of leaking into the PM's normal chat history."""
    with _request_scope():
        ctx, spy = _patch_call_agent("OK.")
        with ctx:
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id="ext-thread:9004",
            ))

    assert spy.last_call is not None
    assert spy.last_call["session_key"] == f"extension:{result['conversation_external_id']}"
    assert spy.last_call["trace_context"]["conversation_id"] == result["conversation_external_id"]


def test_draft_reply_without_thread_id_falls_back_to_one_shot(db):
    """Older extension versions don't send ``external_thread_id``. Those
    requests still get a draft (one-shot LiteLLM) but produce no mirror
    conversation and no AgentRun — preserving v1 behavior during rollout."""
    with _request_scope():
        async def fake_acompletion(*_args, **_kwargs):
            return _fake_completion("Legacy reply.")
        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id=None,
            ))

    assert result["suggestion"] == "Legacy reply."
    assert result["conversation_external_id"] is None
    assert db.query(Conversation).filter_by(
        conversation_type=ConversationType.MIRRORED_CHAT,
    ).count() == 0


def test_send_message_blocks_for_mirror_conversation(db):
    """``chat_service.send_message`` refuses to write into an external-chat
    mirror — replies must go through the source platform, not rentmate."""
    from gql.services import chat_service

    with _request_scope():
        ctx, _spy = _patch_call_agent("OK.")
        with ctx:
            asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id="ext-thread:9005",
            ))

        conv = db.query(Conversation).filter_by(
            conversation_type=ConversationType.MIRRORED_CHAT,
        ).one()

        with pytest.raises(MirrorConversationReadOnly):
            chat_service.send_message(db, conversation_id=conv.id, body="nope")


def test_draft_reply_refine_mode_includes_user_draft_in_prompt(db):
    """When the PM has typed text into the source platform's reply box the
    extension sends ``draft_text`` and rentmate flips into Refine mode:
    the system prompt asks the agent to polish, and the user prompt
    quotes the draft verbatim so the agent can preserve PM intent."""
    with _request_scope():
        captured: dict = {}

        async def fake_call_agent(_agent_id, *, session_key, messages, trace_context=None, **_kwargs):
            captured["system"] = messages[0]["content"]
            captured["user"] = messages[1]["content"]
            return AgentResponse(reply="Refined: hi Marcus, plumber tomorrow at 10.", side_effects=[])

        with patch("llm.client.call_agent", side_effect=fake_call_agent):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[
                    {"sender": "Marcus", "text": "Dishwasher leaked again."},
                ],
                header_title="Dishwasher leak",
                header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id="ext-thread:9100",
                draft_text="hey marcus plumber will come tomorrow at 10",
            ))

    assert result["fallback"] is False
    assert "refining a draft" in captured["system"].lower()
    assert "preserving the PM's intent" in captured["system"]
    assert "hey marcus plumber will come tomorrow at 10" in captured["user"]
    assert result["suggestion"].startswith("Refined:")


def test_draft_reply_blank_draft_text_uses_compose_mode(db):
    """A whitespace-only ``draft_text`` shouldn't trigger Refine mode —
    the PM hasn't actually typed anything meaningful, so we compose fresh."""
    with _request_scope():
        captured: dict = {}

        async def fake_call_agent(_agent_id, *, session_key, messages, trace_context=None, **_kwargs):
            captured["system"] = messages[0]["content"]
            return AgentResponse(reply="Hi.", side_effects=[])

        with patch("llm.client.call_agent", side_effect=fake_call_agent):
            asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id="ext-thread:9101",
                draft_text="   \n  ",
            ))

    assert "drafting a reply" in captured["system"].lower()
    assert "refining a draft" not in captured["system"].lower()


def test_send_autonomous_message_blocks_for_mirror_conversation(db):
    """The agent's autonomous-message path is also blocked so a buggy
    agent run can't accidentally post into the read-only mirror."""
    from gql.services import chat_service

    with _request_scope():
        ctx, _spy = _patch_call_agent("OK.")
        with ctx:
            asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
                external_thread_id="ext-thread:9006",
            ))

        conv = db.query(Conversation).filter_by(
            conversation_type=ConversationType.MIRRORED_CHAT,
        ).one()

        with pytest.raises(MirrorConversationReadOnly):
            chat_service.send_autonomous_message(db, conversation_id=conv.id, body="nope")
