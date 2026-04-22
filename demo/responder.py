"""Dev-mode fake tenant/vendor responses.

When the agent sends a message to a tenant or vendor in development mode,
this module schedules a delayed LLM-generated reply that appears in the
conversation. The reply does NOT trigger the agent — it just adds a message
so the UI shows a realistic back-and-forth.
"""

from __future__ import annotations

import asyncio
import os
import random
from datetime import UTC, datetime


def schedule_fake_reply(conversation_id: int) -> None:
    """Fire-and-forget: schedule an LLM reply 3–15s from now. No-op outside dev."""
    if os.getenv("RENTMATE_ENV") != "development":
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    delay = random.uniform(3.0, 15.0)
    loop.create_task(_delayed_fake_reply(conversation_id, delay))


async def _delayed_fake_reply(conversation_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    from db.models import (
        Conversation,
        ConversationParticipant,
        ConversationType,
        Message,
        MessageType,
        ParticipantType,
        User,
    )
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        if not conv or conv.conversation_type not in (
            ConversationType.TENANT.value if hasattr(ConversationType.TENANT, "value") else ConversationType.TENANT,
            ConversationType.VENDOR.value if hasattr(ConversationType.VENDOR, "value") else ConversationType.VENDOR,
        ):
            return

        participant = (
            db.query(ConversationParticipant)
            .filter_by(conversation_id=conv.id, is_active=True)
            .filter(ConversationParticipant.participant_type.in_([
                ParticipantType.TENANT,
                ParticipantType.EXTERNAL_CONTACT,
            ]))
            .first()
        )
        if not participant:
            return
        user = db.get(User, participant.user_id)
        if not user:
            return

        if participant.participant_type == ParticipantType.TENANT:
            role = "tenant"
        else:
            role = f"{user.role_label or 'contractor'} (vendor)"

        recent = (
            db.query(Message)
            .filter_by(conversation_id=conv.id)
            .order_by(Message.sent_at.desc())
            .limit(5)
            .all()
        )[::-1]
        transcript = "\n".join(
            f"{'Manager' if m.is_ai else (user.first_name or 'Them')}: {m.body}"
            for m in recent if m.body
        )

        reply = await _llm_reply(user.first_name or "Tenant", role, transcript)

        now = datetime.now(UTC)
        db.add(Message(
            org_id=conv.org_id,
            conversation_id=conv.id,
            sender_type=participant.participant_type,
            sender_id=participant.id,
            sender_name=user.first_name or "",
            body=reply,
            message_type=MessageType.MESSAGE,
            is_ai=False,
            sent_at=now,
        ))
        conv.updated_at = now
        db.commit()
        print(f"[dev_responder] {user.first_name} replied: {reply[:80]}")
    except Exception as exc:
        print(f"[dev_responder] failed: {exc}")
        db.rollback()
    finally:
        db.close()


_CANNED_REPLIES = [
    "Ok, thanks for letting me know.",
    "Sounds good.",
    "Not sure — can you check again?",
    "When can you come by?",
    "Got it, I'll be here.",
    "Works for me, thanks!",
]


async def _llm_reply(name: str, role: str, transcript: str) -> str:
    try:
        import litellm
        model = os.getenv("LLM_MODEL") or "deepseek/deepseek-chat"
        resp = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content":
                    f"You are {name}, a {role}. Reply to your property "
                    "manager in 1-3 short sentences. Natural, casual. "
                    "Do not sign off or use greetings."},
                {"role": "user", "content":
                    f"Recent conversation:\n{transcript}\n\nWrite your next reply as {name}."},
            ],
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return random.choice(_CANNED_REPLIES)
