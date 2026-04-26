"""Demo-only background loop that makes seeded tenants and vendors reply
to outbound messages with personality-shaped drafts and realistic
5-30 second jitter.

Off by default; enabled when ``RENTMATE_DEMO_SIMULATOR=1``.

The simulator posts replies through the **portal pathway** — it inserts
a ``Message`` row exactly like ``tenant_portal.tenant_send_conversation_message``
and ``vendor_portal.vendor_send_conversation_message`` do, then calls the
same ``notify_task_owner_of_portal_message`` and ``trigger_task_autoreply``
helpers. Simulated traffic is therefore indistinguishable from real
portal traffic and triggers every downstream side-effect (notifications +
RentMate's autoreply) automatically, which makes the dev portfolio
self-sustaining for demos.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

# Imported at module level so tests can patch them on this module's namespace.
# These helpers have no demo-side dependencies, so no risk of circular import.
from handlers.portals._common import (
    notify_task_owner_of_portal_message,
    trigger_task_autoreply,
)

logger = logging.getLogger("rentmate.demo.simulator")


_JITTER_MIN_SECONDS = 5.0
_JITTER_MAX_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 5.0


# In-memory: maps conversation_id -> id of the last manager-side Message
# we've already scheduled a reply for. Prevents the loop from re-firing on
# the same outbound message every poll. Process-local; reset on restart,
# which is fine for a dev simulator.
_HANDLED_MESSAGES: dict[int, int] = {}


def _build_history(messages: list[Any], *, limit: int = 6) -> list[dict[str, str]]:
    """Recent visible messages → simple alternating role list for the LLM.
    Manager-side messages (account_user / AI) become 'Manager:'; the
    simulated entity's own past replies become 'You:'."""
    from db.models import ParticipantType

    visible = [
        m for m in messages
        if getattr(m, "deleted_at", None) is None
    ]
    visible.sort(key=lambda m: m.sent_at)
    tail = visible[-limit:]
    out: list[dict[str, str]] = []
    for m in tail:
        if m.sender_type == ParticipantType.ACCOUNT_USER:
            speaker = "Manager"
        else:
            speaker = "You"
        body = (m.body or "").strip()
        if not body:
            continue
        out.append({"speaker": speaker, "body": body})
    return out


def _format_role(user: Any, conversation_type: str) -> str:
    if conversation_type == "tenant":
        return "tenant"
    role_label = getattr(user, "role_label", None)
    return f"{role_label} (vendor)" if role_label else "vendor"


def _build_prompt(
    *,
    user: Any,
    personality: Any,
    conversation_type: str,
    property_name: str | None,
    history: list[dict[str, str]],
) -> tuple[str, str]:
    role = _format_role(user, conversation_type)
    where = f" at {property_name}" if property_name else ""
    full_name = " ".join(filter(None, [user.first_name, user.last_name])) or "User"
    portal = "tenant portal" if conversation_type == "tenant" else "vendor portal"

    system = (
        f"You are {full_name}, a {role}{where}.\n"
        f"Personality: {personality.voice}\n"
        f"Style: {personality.formality}, {personality.response_style}.\n"
        f"You're replying to your property manager via your {portal}. "
        "Keep replies natural, 1-3 sentences, in character. Do not break "
        "character. Do not write more than 320 characters. Never mention "
        "that you are an AI."
    )
    transcript = "\n".join(
        f"{turn['speaker']}: {turn['body']}" for turn in history
    ) or "(no prior history)"
    user_msg = (
        "Recent conversation with your property manager:\n\n"
        f"{transcript}\n\n"
        "Reply now in character. Do not include a 'You:' prefix; just the "
        "message body."
    )
    return system, user_msg


def _canned_fallback(personality: Any) -> str:
    bucket = {
        "brief": "Got it, thanks.",
        "detail-seeking": "Thanks — could you share a bit more on timing?",
        "anxious": "Okay — when do you think this will be sorted?",
        "chatty": "Thanks for the update! Appreciate you keeping me posted.",
        "organized": "Confirmed. I'll plan around that.",
        "confident": "Sounds good — will follow up shortly.",
    }
    return bucket.get(personality.response_style, "Thanks, got it.")


async def _generate_reply(
    *,
    user: Any,
    personality: Any,
    conversation_type: str,
    property_name: str | None,
    history: list[dict[str, str]],
) -> str:
    """Single LiteLLM completion. Falls back to a canned reply on failure
    so a flaky LLM never crashes the simulator."""
    import litellm

    from llm.model_config import build_litellm_request_kwargs

    system, user_content = _build_prompt(
        user=user,
        personality=personality,
        conversation_type=conversation_type,
        property_name=property_name,
        history=history,
    )

    model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    kwargs = build_litellm_request_kwargs(
        model=model,
        api_base=os.getenv("LLM_BASE_URL") or None,
        api_key=os.getenv("LLM_API_KEY"),
        app_name="rentmate-demo-simulator",
    )
    try:
        resp = await litellm.acompletion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            max_tokens=180,
            temperature=0.7,
            **kwargs,
        )
        body = (resp.choices[0].message.content or "").strip()
        if not body:
            raise ValueError("empty reply")
        # Clamp to keep things SMS-like even if the model overruns.
        return body[:320]
    except Exception as exc:
        logger.warning(
            "[demo] LLM reply generation failed for %s; using fallback: %s",
            user.first_name, exc,
        )
        return _canned_fallback(personality)


def _post_as_portal(
    db: Any,
    *,
    conversation: Any,
    task: Any,
    user: Any,
    body: str,
) -> Any:
    """Insert the Message + fire the same notification the portal does.
    Mirrors ``tenant_portal.tenant_send_conversation_message`` and the
    vendor analogue. Caller still needs to trigger autoreply after commit."""
    from db.models import (
        ConversationParticipant,
        Message,
        MessageType,
        ParticipantType,
    )

    is_tenant = conversation.conversation_type == "tenant"
    sender_type = ParticipantType.TENANT if is_tenant else ParticipantType.EXTERNAL_CONTACT
    actor_kind = "tenant" if is_tenant else "vendor"

    participant = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation.id,
            ConversationParticipant.user_id == user.id,
            ConversationParticipant.is_active.is_(True),
        )
    ).scalar_one()

    name = " ".join(filter(None, [user.first_name, user.last_name])) or "User"
    now = datetime.now(UTC)
    message = Message(
        org_id=conversation.org_id,
        conversation_id=conversation.id,
        sender_type=sender_type,
        sender_id=participant.id,
        body=body,
        message_type=MessageType.MESSAGE,
        sender_name=name,
        is_ai=False,
        sent_at=now,
    )
    db.add(message)
    if task is not None:
        task.last_message_at = now
    notify_task_owner_of_portal_message(
        db,
        task=task,
        conversation=conversation,
        sender_label=name,
        body=body,
        actor_kind=actor_kind,
    )
    db.commit()
    db.refresh(message)
    return message


def _find_pending_targets(db: Any) -> list[dict[str, Any]]:
    """Return a list of conversations ready for a simulated reply.

    Selected when:
    - conversation_type is 'tenant' or 'vendor'
    - not archived
    - has exactly one active non-account-user participant whose phone is
      in the personality registry (i.e. it's a seeded entity)
    - latest visible message has sender_type = ACCOUNT_USER (manager/AI)
      AND that message id has not been handled yet
    """
    from db.models import (
        Conversation,
        ConversationParticipant,
        ParticipantType,
    )

    from demo.personalities import is_seeded_phone

    convs = db.execute(
        select(Conversation)
        .where(Conversation.conversation_type.in_(["tenant", "vendor"]))
        .where(Conversation.is_archived.is_(False))
    ).scalars().all()

    targets: list[dict[str, Any]] = []
    for conv in convs:
        externals = [
            p for p in (conv.participants or [])
            if getattr(p, "is_active", True)
            and p.participant_type != ParticipantType.ACCOUNT_USER
        ]
        if len(externals) != 1:
            continue
        external = externals[0]
        user = getattr(external, "user", None)
        if user is None or not is_seeded_phone(user.phone):
            continue

        visible = [
            m for m in (conv.messages or [])
            if getattr(m, "deleted_at", None) is None and (m.body or "").strip()
        ]
        if not visible:
            continue
        visible.sort(key=lambda m: m.sent_at)
        last = visible[-1]
        if last.sender_type != ParticipantType.ACCOUNT_USER:
            continue
        if _HANDLED_MESSAGES.get(conv.id) == last.id:
            continue

        # Mark immediately so the next poll doesn't double-schedule while
        # this reply sleeps out its jitter delay.
        _HANDLED_MESSAGES[conv.id] = last.id

        task = getattr(conv, "parent_task", None)
        property_name = None
        if conv.property is not None:
            property_name = (
                getattr(conv.property, "name", None)
                or getattr(conv.property, "address_line1", None)
            )
        targets.append({
            "conversation_id": conv.id,
            "user_id": user.id,
            "task_id": getattr(task, "id", None) if task else None,
            "last_message_id": last.id,
            "property_name": property_name,
        })
    return targets


async def _schedule_reply(target: dict[str, Any]) -> None:
    """Sleep for a jittered delay, then post a personality-shaped reply.

    Always fetches a fresh DB session inside the task so it doesn't hold
    a session across a 5-30s sleep.
    """
    from backends.local_auth import set_request_context, reset_request_context
    from db.models import Conversation, User
    from db.session import SessionLocal

    from demo.personalities import get_personality_by_phone

    delay = random.uniform(_JITTER_MIN_SECONDS, _JITTER_MAX_SECONDS)

    # Open one session just to read the user's name for the log line, so
    # the operator sees "Marcus replies in 12.4s" before the sleep.
    name_for_log = "User"
    sess = SessionLocal.session_factory()
    try:
        user = sess.query(User).filter_by(id=target["user_id"]).first()
        if user:
            name_for_log = " ".join(filter(None, [user.first_name, user.last_name])) or "User"
    finally:
        sess.close()
    logger.info(
        "[demo] %s will reply on conv=%s in %.1fs",
        name_for_log, target["conversation_id"], delay,
    )

    await asyncio.sleep(delay)

    # Fresh session for the actual post; portal handlers create one per
    # request and so do we.
    db = SessionLocal.session_factory()
    ctx_token = set_request_context(account_id=1, org_id=1)
    try:
        from sqlalchemy.orm import selectinload

        from db.models import ConversationParticipant

        conv = db.execute(
            select(Conversation)
            .where(Conversation.id == target["conversation_id"])
            .options(
                selectinload(Conversation.participants).selectinload(ConversationParticipant.user),
                selectinload(Conversation.messages),
                selectinload(Conversation.property),
                selectinload(Conversation.parent_task),
            )
        ).scalar_one_or_none()
        if conv is None:
            return

        # Bail if the conversation moved on (someone replied during sleep).
        visible = sorted(
            [m for m in (conv.messages or []) if (m.body or "").strip()],
            key=lambda m: m.sent_at,
        )
        if not visible or visible[-1].id != target["last_message_id"]:
            logger.info(
                "[demo] skipping reply for conv=%s — conversation moved on",
                conv.id,
            )
            return

        user = db.query(User).filter_by(id=target["user_id"]).first()
        if user is None:
            return
        personality = get_personality_by_phone(user.phone)
        history = _build_history(visible)

        body = await _generate_reply(
            user=user,
            personality=personality,
            conversation_type=conv.conversation_type,
            property_name=target.get("property_name"),
            history=history,
        )

        task = getattr(conv, "parent_task", None)
        message = _post_as_portal(
            db,
            conversation=conv,
            task=task,
            user=user,
            body=body,
        )
        logger.info(
            "[demo] posted simulated reply (conv=%s msg_id=%s, user=%s)",
            conv.id, message.id, user.first_name,
        )

        # Fire RentMate's autoreply through the same daemon-thread path
        # the portal endpoints use. Lets the agent react like a real
        # tenant/vendor message just arrived.
        if task is not None:
            sender_name = " ".join(filter(None, [user.first_name, user.last_name])) or "User"
            trigger_task_autoreply(str(task.id), sender_name=sender_name, body=body)
    except Exception:
        logger.exception("[demo] simulated reply failed for conv=%s", target["conversation_id"])
    finally:
        reset_request_context(ctx_token)
        db.close()


async def simulator_loop(*, poll_interval: float = _POLL_INTERVAL_SECONDS) -> None:
    """Run forever; poll for outbound messages on seeded tenant/vendor
    conversations and schedule personality-shaped replies."""
    from sqlalchemy.orm import selectinload

    from db.models import Conversation, ConversationParticipant
    from db.session import SessionLocal

    logger.info("[demo] simulator loop started (poll=%.1fs)", poll_interval)

    while True:
        try:
            db = SessionLocal.session_factory()
            try:
                # Re-read with eager-loaded participants/messages so
                # _find_pending_targets doesn't N+1 over each conv.
                db.execute(
                    select(Conversation).options(
                        selectinload(Conversation.participants).selectinload(ConversationParticipant.user),
                        selectinload(Conversation.messages),
                        selectinload(Conversation.property),
                        selectinload(Conversation.parent_task),
                    )
                )
                targets = _find_pending_targets(db)
            finally:
                db.close()

            for target in targets:
                asyncio.create_task(_schedule_reply(target))
        except Exception:
            logger.exception("[demo] simulator poll failed")
        await asyncio.sleep(poll_interval)
