"""Behavior tests for ``demo/simulator.py``.

These tests exercise the synchronous building blocks (``_find_pending_targets``,
``_post_as_portal``, ``_build_prompt``) directly. The async ``_schedule_reply``
loop is exercised with patched LiteLLM + patched ``asyncio.sleep`` to keep
the test suite fast.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Tenant,
    User,
)


@pytest.fixture(autouse=True)
def _request_context():
    from integrations.local_auth import reset_request_context, set_request_context
    token = set_request_context(account_id=1, org_id=1)
    yield
    reset_request_context(token)


@pytest.fixture(autouse=True)
def _reset_handled():
    from demo import simulator
    simulator._HANDLED_MESSAGES.clear()
    yield
    simulator._HANDLED_MESSAGES.clear()


def _seed_tenant(db, *, phone="+14255550101", first="Marcus", last="Johnson"):
    user = User(
        org_id=1, creator_id=1, user_type="tenant",
        first_name=first, last_name=last, email=f"{first.lower()}@example.com",
        phone=phone,
    )
    db.add(user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=user.id)
    db.add(tenant)
    db.flush()
    return user, tenant


def _seed_external_conversation(db, *, user, conv_type=ConversationType.TENANT, archived=False):
    convo = Conversation(
        org_id=1, creator_id=1,
        subject=f"Chat with {user.first_name}",
        conversation_type=conv_type,
        is_group=False,
        is_archived=archived,
    )
    db.add(convo)
    db.flush()
    participant = ConversationParticipant(
        org_id=1, creator_id=1,
        conversation_id=convo.id,
        user_id=user.id,
        participant_type=ParticipantType.TENANT
            if conv_type == ConversationType.TENANT
            else ParticipantType.EXTERNAL_CONTACT,
        is_active=True,
    )
    db.add(participant)
    db.flush()
    return convo


def _add_manager_message(db, convo, *, body="Hi Marcus, can you confirm?", offset_minutes=0, is_ai=True):
    msg = Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=body,
        message_type=MessageType.MESSAGE,
        sender_name="RentMate" if is_ai else "You",
        is_ai=is_ai,
        sent_at=datetime.now(UTC) + timedelta(minutes=offset_minutes),
    )
    db.add(msg)
    db.flush()
    return msg


def _add_tenant_reply(db, convo, *, body="Sure thing", offset_minutes=0):
    participant = db.query(ConversationParticipant).filter_by(
        conversation_id=convo.id,
        participant_type=ParticipantType.TENANT,
    ).first()
    msg = Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.TENANT,
        sender_id=participant.id if participant else None,
        body=body,
        message_type=MessageType.MESSAGE,
        sender_name="Marcus Johnson",
        is_ai=False,
        sent_at=datetime.now(UTC) + timedelta(minutes=offset_minutes),
    )
    db.add(msg)
    db.flush()
    return msg


# ─── _find_pending_targets ──────────────────────────────────────────────


def test_find_pending_targets_picks_up_outbound_manager_message(db):
    from demo.simulator import _find_pending_targets

    user, _ = _seed_tenant(db)
    convo = _seed_external_conversation(db, user=user)
    _add_manager_message(db, convo, body="Hey, ready?")
    db.commit()

    targets = _find_pending_targets(db)

    assert len(targets) == 1
    assert targets[0]["conversation_id"] == convo.id
    assert targets[0]["user_id"] == user.id


def test_find_pending_targets_skips_after_tenant_already_replied(db):
    from demo.simulator import _find_pending_targets

    user, _ = _seed_tenant(db)
    convo = _seed_external_conversation(db, user=user)
    _add_manager_message(db, convo, body="Hi", offset_minutes=-2)
    _add_tenant_reply(db, convo, body="Hi back", offset_minutes=-1)
    db.commit()

    assert _find_pending_targets(db) == []


def test_find_pending_targets_skips_archived_and_internal_threads(db):
    from demo.simulator import _find_pending_targets

    user, _ = _seed_tenant(db)
    archived = _seed_external_conversation(db, user=user, archived=True)
    _add_manager_message(db, archived)

    user2, _ = _seed_tenant(db, phone="+14255550102", first="Priya", last="Patel")
    internal = _seed_external_conversation(db, user=user2, conv_type=ConversationType.TASK_AI)
    _add_manager_message(db, internal)
    db.commit()

    assert _find_pending_targets(db) == []


def test_find_pending_targets_skips_unseeded_phones(db):
    """A real human user who happens to be in the dev DB must not get
    auto-replies — the simulator only fires for seeded characters."""
    from demo.simulator import _find_pending_targets

    user, _ = _seed_tenant(db, phone="+12025550199", first="Real", last="Human")
    convo = _seed_external_conversation(db, user=user)
    _add_manager_message(db, convo)
    db.commit()

    assert _find_pending_targets(db) == []


def test_find_pending_targets_dedupes_within_a_session(db):
    """Two polls in a row must not select the same outbound message twice."""
    from demo.simulator import _find_pending_targets

    user, _ = _seed_tenant(db)
    convo = _seed_external_conversation(db, user=user)
    _add_manager_message(db, convo)
    db.commit()

    first = _find_pending_targets(db)
    second = _find_pending_targets(db)

    assert len(first) == 1
    assert second == []


def test_find_pending_targets_re_fires_on_new_outbound(db):
    """After a manager sends a follow-up, the simulator must pick that up
    even though the previous message was already handled."""
    from demo.simulator import _find_pending_targets

    user, _ = _seed_tenant(db)
    convo = _seed_external_conversation(db, user=user)
    _add_manager_message(db, convo, body="ping 1", offset_minutes=-2)
    db.commit()
    assert len(_find_pending_targets(db)) == 1

    _add_manager_message(db, convo, body="ping 2", offset_minutes=-1)
    db.commit()
    targets = _find_pending_targets(db)
    assert len(targets) == 1
    assert targets[0]["last_message_id"] != _find_pending_targets(db)


# ─── _build_prompt ──────────────────────────────────────────────────────


def test_build_prompt_includes_personality_voice_and_history():
    from demo.personalities import Personality
    from demo.simulator import _build_prompt

    user = MagicMock(first_name="Marcus", last_name="Johnson", role_label=None)
    personality = Personality(
        voice="Easy-going professional. Replies in short sentences.",
        formality="casual",
        response_style="brief",
    )
    history = [
        {"speaker": "Manager", "body": "Can you confirm Thursday?"},
        {"speaker": "You", "body": "Yes."},
        {"speaker": "Manager", "body": "Great, plumber arrives 2pm."},
    ]
    system, user_content = _build_prompt(
        user=user,
        personality=personality,
        conversation_type="tenant",
        property_name="The Meadows",
        history=history,
    )
    assert "Marcus Johnson" in system
    assert "Easy-going professional" in system
    assert "tenant portal" in system
    assert "The Meadows" in system
    assert "Manager: Can you confirm Thursday?" in user_content
    assert "You: Yes." in user_content


def test_build_prompt_uses_role_label_for_vendors():
    from demo.personalities import DEFAULT_PERSONALITY
    from demo.simulator import _build_prompt

    user = MagicMock(first_name="Mike", last_name="Rivera", role_label="Plumber")
    system, _ = _build_prompt(
        user=user, personality=DEFAULT_PERSONALITY,
        conversation_type="vendor", property_name=None, history=[],
    )
    assert "Plumber (vendor)" in system
    assert "vendor portal" in system


# ─── _post_as_portal ────────────────────────────────────────────────────


def test_post_as_portal_inserts_message_and_calls_notify(db):
    from demo.simulator import _post_as_portal

    user, _ = _seed_tenant(db)
    convo = _seed_external_conversation(db, user=user)
    db.refresh(convo)

    with patch("demo.simulator.notify_task_owner_of_portal_message") as mock_notify:
        msg = _post_as_portal(
            db, conversation=convo, task=None, user=user, body="Sounds good.",
        )

    assert msg.body == "Sounds good."
    assert msg.sender_type == ParticipantType.TENANT
    assert msg.is_ai is False
    assert msg.sender_name == "Marcus Johnson"
    mock_notify.assert_called_once()
    kwargs = mock_notify.call_args.kwargs
    assert kwargs["sender_label"] == "Marcus Johnson"
    assert kwargs["actor_kind"] == "tenant"
    assert kwargs["body"] == "Sounds good."


def test_post_as_portal_uses_external_contact_for_vendor_conv(db):
    """Vendor convs must post as EXTERNAL_CONTACT and actor_kind='vendor'
    so the notification + downstream triggers match the vendor portal."""
    from demo.simulator import _post_as_portal

    vendor = User(
        org_id=1, creator_id=1, user_type="vendor",
        first_name="Mike", last_name="Rivera",
        role_label="Plumber", phone="+14255550201",
    )
    db.add(vendor)
    db.flush()
    convo = Conversation(
        org_id=1, creator_id=1, subject="Mike",
        conversation_type=ConversationType.VENDOR,
        is_group=False, is_archived=False,
    )
    db.add(convo)
    db.flush()
    db.add(ConversationParticipant(
        org_id=1, creator_id=1,
        conversation_id=convo.id, user_id=vendor.id,
        participant_type=ParticipantType.EXTERNAL_CONTACT,
        is_active=True,
    ))
    db.flush()

    with patch("demo.simulator.notify_task_owner_of_portal_message") as mock_notify:
        msg = _post_as_portal(db, conversation=convo, task=None, user=vendor, body="On my way.")

    assert msg.sender_type == ParticipantType.EXTERNAL_CONTACT
    assert mock_notify.call_args.kwargs["actor_kind"] == "vendor"


# ─── _generate_reply (LLM-mocked) ───────────────────────────────────────


def test_generate_reply_uses_litellm_acompletion():
    from demo.personalities import DEFAULT_PERSONALITY
    from demo.simulator import _generate_reply

    user = MagicMock(first_name="Marcus", last_name="Johnson", role_label=None)
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="  Sounds good.  "))]

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=fake_resp) as mock_call:
        out = asyncio.run(_generate_reply(
            user=user, personality=DEFAULT_PERSONALITY,
            conversation_type="tenant", property_name=None,
            history=[{"speaker": "Manager", "body": "Confirm?"}],
        ))

    assert out == "Sounds good."
    sent_messages = mock_call.call_args.kwargs["messages"]
    assert sent_messages[0]["role"] == "system"
    assert "Marcus Johnson" in sent_messages[0]["content"]
    assert sent_messages[1]["role"] == "user"
    assert "Manager: Confirm?" in sent_messages[1]["content"]


def test_generate_reply_falls_back_to_canned_on_llm_error():
    from demo.personalities import Personality
    from demo.simulator import _canned_fallback, _generate_reply

    personality = Personality(voice="x", formality="casual", response_style="brief")
    user = MagicMock(first_name="Marcus", last_name="Johnson", role_label=None)

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("network")):
        out = asyncio.run(_generate_reply(
            user=user, personality=personality,
            conversation_type="tenant", property_name=None, history=[],
        ))

    assert out == _canned_fallback(personality)
    assert out == "Got it, thanks."


# ─── _schedule_reply jitter ─────────────────────────────────────────────


def test_schedule_reply_jitters_within_5_to_30_seconds(db):
    """The user requirement: 5-30s jitter on every reply."""
    from demo import simulator

    user, _ = _seed_tenant(db)
    convo = _seed_external_conversation(db, user=user)
    msg = _add_manager_message(db, convo, body="Hello?")
    db.commit()

    target = {
        "conversation_id": convo.id,
        "user_id": user.id,
        "task_id": None,
        "last_message_id": msg.id,
        "property_name": None,
    }

    captured: dict[str, float] = {}

    def fake_uniform(low, high):
        captured["low"] = low
        captured["high"] = high
        return 7.5

    sleep_durations: list[float] = []

    async def fake_sleep(d):
        sleep_durations.append(d)
        return None

    fake_sl = MagicMock()
    fake_sl.session_factory.return_value = db
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="OK."))]

    with patch("random.uniform", side_effect=fake_uniform), \
         patch("asyncio.sleep", side_effect=fake_sleep), \
         patch("db.session.SessionLocal", fake_sl), \
         patch.object(db, "close", lambda: None), \
         patch("litellm.acompletion", new_callable=AsyncMock, return_value=fake_resp), \
         patch("demo.simulator.notify_task_owner_of_portal_message"), \
         patch("demo.simulator.trigger_task_autoreply") as mock_trigger:
        asyncio.run(simulator._schedule_reply(target))

    assert captured == {"low": simulator._JITTER_MIN_SECONDS, "high": simulator._JITTER_MAX_SECONDS}
    assert simulator._JITTER_MIN_SECONDS == 5.0
    assert simulator._JITTER_MAX_SECONDS == 30.0
    assert 7.5 in sleep_durations
    # No task linkage on this convo, so autoreply must NOT fire.
    mock_trigger.assert_not_called()


def test_schedule_reply_triggers_autoreply_when_task_present(db):
    """Mirroring the portal endpoints: task-linked convs fire autoreply."""
    from db.enums import TaskStatus
    from db.models import Task
    from demo import simulator
    from services.number_allocator import NumberAllocator

    user, _ = _seed_tenant(db)
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1, creator_id=1, title="Leaking faucet",
        task_status=TaskStatus.ACTIVE,
    )
    db.add(task)
    db.flush()
    convo = _seed_external_conversation(db, user=user)
    convo.parent_task_id = task.id
    db.flush()
    msg = _add_manager_message(db, convo, body="Confirm tomorrow?")
    db.commit()

    target = {
        "conversation_id": convo.id,
        "user_id": user.id,
        "task_id": task.id,
        "last_message_id": msg.id,
        "property_name": None,
    }

    fake_sl = MagicMock()
    fake_sl.session_factory.return_value = db
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="Sure, tomorrow works."))]

    async def fake_sleep(_d):
        return None

    with patch("random.uniform", return_value=0.0), \
         patch("asyncio.sleep", side_effect=fake_sleep), \
         patch("db.session.SessionLocal", fake_sl), \
         patch.object(db, "close", lambda: None), \
         patch("litellm.acompletion", new_callable=AsyncMock, return_value=fake_resp), \
         patch("demo.simulator.notify_task_owner_of_portal_message"), \
         patch("demo.simulator.trigger_task_autoreply") as mock_trigger:
        asyncio.run(simulator._schedule_reply(target))

    mock_trigger.assert_called_once()
    args = mock_trigger.call_args
    assert args.args[0] == str(task.id)
    assert args.kwargs["sender_name"] == "Marcus Johnson"
    assert args.kwargs["body"] == "Sure, tomorrow works."


def test_schedule_reply_skips_if_conversation_advanced_during_sleep(db):
    """If the tenant (or anyone) replied while the simulator was sleeping
    out its jitter, bail and don't post a stale auto-reply."""
    from demo import simulator

    user, _ = _seed_tenant(db)
    convo = _seed_external_conversation(db, user=user)
    manager_msg = _add_manager_message(db, convo, body="Ping", offset_minutes=-1)
    db.commit()
    target = {
        "conversation_id": convo.id,
        "user_id": user.id,
        "task_id": None,
        "last_message_id": manager_msg.id,
        "property_name": None,
    }

    # Simulate someone else replying *during* the jitter sleep.
    async def fake_sleep(_d):
        _add_tenant_reply(db, convo, body="oh I already saw it", offset_minutes=0)
        db.commit()
        return None

    fake_sl = MagicMock()
    fake_sl.session_factory.return_value = db

    with patch("random.uniform", return_value=0.0), \
         patch("asyncio.sleep", side_effect=fake_sleep), \
         patch("db.session.SessionLocal", fake_sl), \
         patch.object(db, "close", lambda: None), \
         patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm, \
         patch("demo.simulator.notify_task_owner_of_portal_message") as mock_notify:
        asyncio.run(simulator._schedule_reply(target))

    mock_llm.assert_not_called()
    mock_notify.assert_not_called()
