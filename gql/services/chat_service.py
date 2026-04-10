import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backends.local_auth import resolve_account_id
from db.lib import get_conversation_with_messages
from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Task,
)
from db.queries import fetch_conversations

logger = logging.getLogger("rentmate.chat_service")


def get_or_create_conversation(db: Session, conversation_id: str) -> Conversation:
    """Look up a user_ai conversation by PK; create one with that ID if not found."""
    conv = db.query(Conversation).filter_by(id=conversation_id).first()
    if conv is None:
        conv = Conversation(
            id=conversation_id,
            creator_id=resolve_account_id(),
            subject="Chat with RentMate",
            is_group=False,
            is_archived=False,
            conversation_type=ConversationType.USER_AI,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(conv)
    return conv


def build_agent_message_history(
    db: Session,
    *, conv_id: str,
    user_message: str,
    context: str,
    exclude_last: bool = False,
) -> list[dict]:
    """Load last 20 messages, format as [{role, content}], prepend system context, append user message."""
    full_conv = get_conversation_with_messages(db, conv_id)
    db_msgs = sorted(full_conv.messages, key=lambda m: m.sent_at) if full_conv else []
    if exclude_last and db_msgs:
        db_msgs = db_msgs[:-1]
    db_msgs = db_msgs[-20:]
    messages = [{"role": "system", "content": context}]
    for m in db_msgs:
        role = "assistant" if m.is_ai else "user"
        messages.append({"role": role, "content": m.body or ""})
    messages.append({"role": "user", "content": user_message})
    return messages


def persist_user_ai_messages(
    db: Session,
    *, conv_id: str,
    user_message: str,
    ai_reply: str,
) -> None:
    """Persist the user message + AI reply and bump conversation.updated_at."""
    now = datetime.now(UTC)
    db.add(Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=user_message,
        message_type=MessageType.MESSAGE,
        sender_name="You",
        is_ai=False,
        sent_at=now,
    ))
    db.add(Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=ai_reply,
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=now,
    ))
    conv = db.query(Conversation).filter_by(id=conv_id).first()
    if conv:
        conv.updated_at = now
    db.commit()


def should_ai_respond(conversation: Conversation) -> bool:
    """Return True if the AI agent should reply to a message in this conversation."""
    if conversation.conversation_type == ConversationType.USER_AI:
        return True
    human_types = {ParticipantType.TENANT, ParticipantType.EXTERNAL_CONTACT}
    return not any(
        p.participant_type in human_types
        for p in (conversation.participants or [])
    )


def persist_user_message_only(db: Session, *, conv_id: str, body: str) -> None:
    """Persist only the user message (no AI reply) and bump conversation.updated_at."""
    now = datetime.now(UTC)
    db.add(Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=body,
        message_type=MessageType.MESSAGE,
        sender_name="You",
        is_ai=False,
        sent_at=now,
    ))
    conv = db.query(Conversation).filter_by(id=conv_id).first()
    if conv:
        conv.updated_at = now


def list_conversations(db: Session, *, conversation_type: str, limit: int = 50) -> list[Conversation]:
    """Thin wrapper over fetch_conversations."""
    return fetch_conversations(db, conversation_type=conversation_type, limit=limit)


def get_or_create_external_conversation(
    db: Session,
    *,
    conversation_type: ConversationType,
    subject: str,
    property_id: str | None = None,
    unit_id: str | None = None,
    vendor_id: str | None = None,
    tenant_id: str | None = None,
    ai_typing: bool = False,
) -> Conversation:
    """Create a new conversation for a vendor or tenant.

    Always creates a fresh conversation so each task gets its own thread.
    """
    now = datetime.now(UTC)
    conv = Conversation(
        id=str(uuid.uuid4()),
        creator_id=resolve_account_id(),
        subject=subject,
        property_id=property_id,
        unit_id=unit_id,
        conversation_type=conversation_type,
        is_group=False,
        is_archived=False,
        extra={"ai_typing": True} if ai_typing else None,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.flush()

    # Add participant
    if vendor_id:
        db.add(ConversationParticipant(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            external_contact_id=vendor_id,
            is_active=True,
        ))
    elif tenant_id:
        db.add(ConversationParticipant(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            participant_type=ParticipantType.TENANT,
            tenant_id=tenant_id,
            is_active=True,
        ))
    db.flush()
    return conv


def send_autonomous_message(
    db: Session,
    *, conversation_id: str,
    body: str,
    task_id: str | None = None,
) -> Message:
    """Send an AI-generated message to a conversation and clear the typing indicator.

    Use this after task/conversation creation is committed, for messages that
    can safely happen outside the creation transaction (e.g. vendor outreach).
    """
    now = datetime.now(UTC)
    convo = db.query(Conversation).filter_by(id=conversation_id).first()
    if not convo:
        raise ValueError(f"Conversation {conversation_id} not found")

    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=body,
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=now,
    )
    db.add(msg)
    convo.updated_at = now

    # Clear typing indicator
    extra = dict(convo.extra or {})
    extra.pop("ai_typing", None)
    convo.extra = extra
    flag_modified(convo, "extra")

    if task_id:
        task = db.execute(select(Task).where(Task.id == task_id)).scalar_one_or_none()
        if task:
            task.last_message_at = now

    db.commit()
    db.refresh(msg)
    return msg


def send_message(
    db: Session,
    *, conversation_id: str,
    body: str,
    message_type: str = MessageType.MESSAGE,
    sender_name: str = "You",
    is_ai: bool = False,
    draft_reply: str | None = None,
    related_task_ids: dict | None = None,
    sent_at: datetime | None = None,
) -> Message:
    """Add a message to any conversation by conversation_id."""
    now = sent_at or datetime.now(UTC)
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=body,
        message_type=message_type,
        sender_name=sender_name,
        is_ai=is_ai,
        is_system=False,
        draft_reply=draft_reply,
        related_task_ids=related_task_ids,
        sent_at=now,
    )
    db.add(msg)
    db.flush()
    return msg


def clear_typing_indicator(db: Session, conversation_id: str) -> None:
    """Remove the ai_typing flag from a conversation (e.g. when generation fails)."""
    convo = db.query(Conversation).filter_by(id=conversation_id).first()
    if convo and convo.extra and convo.extra.get("ai_typing"):
        extra = dict(convo.extra)
        extra.pop("ai_typing", None)
        convo.extra = extra
        flag_modified(convo, "extra")
        db.commit()
