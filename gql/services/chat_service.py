import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Task,
    Tenant,
    User,
)
from db.queries import fetch_conversations
from llm.history_filters import is_transient_tool_failure_text

logger = logging.getLogger("rentmate.chat_service")


def model_history_messages(db_msgs: list[Message]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for m in db_msgs:
        body = m.body or ""
        if m.is_ai and is_transient_tool_failure_text(body):
            continue
        role = "assistant" if m.is_ai else "user"
        messages.append({"role": role, "content": body})
    return messages


class ConversationExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_typing: bool | None = None
    require_vendor_type: str | None = None
    assigned_vendor_id: str | int | None = None
    assigned_vendor_name: str | None = None
    suggestion_options: list[dict] | None = None


class MessageRelatedTaskIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: int | None = None
    task_id: int | None = None


class MessageActionCardField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


class MessageActionCardLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    entity_type: Literal["suggestion", "property", "tenant", "unit", "document"]
    entity_id: str
    property_id: str | None = None


class MessageActionCardUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    label: str
    property_id: str


class MessageActionCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["suggestion", "property", "tenant", "document"]
    title: str
    summary: str | None = None
    fields: list[MessageActionCardField] | None = None
    links: list[MessageActionCardLink] | None = None
    units: list[MessageActionCardUnit] | None = None


class MessageMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    direction: Literal["inbound", "outbound"] | None = None
    draft_reply: str | None = None
    related_task_ids: MessageRelatedTaskIds | None = None
    action_card: MessageActionCard | None = None


class MessageAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    url: str | None = None
    content_type: str | None = None


class ConversationDeliveryPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str | None = None
    enabled: bool | None = None


class MessageReceiptMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    provider_message_id: str | None = None


def parse_conversation_extra(extra: dict | None) -> ConversationExtra:
    return ConversationExtra.model_validate(extra or {})


def dump_conversation_extra(extra: ConversationExtra) -> dict:
    return extra.model_dump(exclude_none=True)


def set_conversation_ai_typing(extra: dict | None, *, ai_typing: bool | None) -> dict:
    parsed = parse_conversation_extra(extra)
    parsed.ai_typing = ai_typing
    return dump_conversation_extra(parsed)


def assign_conversation_vendor(extra: dict | None, *, vendor_id: str | int, vendor_name: str) -> dict:
    parsed = parse_conversation_extra(extra)
    parsed.assigned_vendor_id = vendor_id
    parsed.assigned_vendor_name = vendor_name
    return dump_conversation_extra(parsed)


def parse_message_meta(meta: dict | None) -> MessageMeta:
    return MessageMeta.model_validate(meta or {})


def dump_message_meta(meta: MessageMeta | dict | None = None, **updates) -> dict | None:
    parsed = meta if isinstance(meta, MessageMeta) else parse_message_meta(meta)
    for key, value in updates.items():
        if key == "related_task_ids" and value is not None:
            value = MessageRelatedTaskIds.model_validate(value)
        if key == "action_card" and value is not None:
            value = MessageActionCard.model_validate(value)
        setattr(parsed, key, value)
    dumped = parsed.model_dump(exclude_none=True)
    return dumped or None


def get_or_create_conversation(
    db: Session,
    *, uid: str | None = None,
) -> Conversation:
    """Look up a user_ai conversation by external_id; create one if not found.

    If *uid* is provided, look up by ``Conversation.external_id``.
    If not found (or uid is None), create a new conversation.
    """
    if uid is not None:
        conv = db.query(Conversation).filter_by(
            external_id=uid,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if conv is not None:
            return conv

    conv = Conversation(
        org_id=resolve_org_id(),
        creator_id=resolve_account_id(),
        external_id=uid,
        subject="Chat with RentMate",
        is_group=False,
        is_archived=False,
        conversation_type=ConversationType.USER_AI,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(conv)
    db.flush()
    return conv


def build_agent_message_history(
    db: Session,
    *, conv_id: int,
    user_message: str,
    context: str,
    exclude_last: bool = False,
) -> list[dict]:
    """Load last 20 messages, format as [{role, content}], prepend system context, append user message."""
    full_conv = db.execute(
        select(Conversation)
        .where(
            Conversation.id == conv_id,
            Conversation.org_id == resolve_org_id(),
            Conversation.creator_id == resolve_account_id(),
        )
        .options(selectinload(Conversation.messages))
    ).scalar_one_or_none()
    db_msgs = sorted(full_conv.messages, key=lambda m: m.sent_at) if full_conv else []
    if exclude_last and db_msgs:
        db_msgs = db_msgs[:-1]
    db_msgs = db_msgs[-20:]
    messages = [{"role": "system", "content": context}]
    messages.extend(model_history_messages(db_msgs))
    messages.append({"role": "user", "content": user_message})
    return messages


def persist_user_ai_messages(
    db: Session,
    *, conv_id: int,
    user_message: str,
    ai_reply: str,
) -> None:
    """Persist the user message + AI reply and bump conversation.updated_at."""
    now = datetime.now(UTC)
    db.add(Message(
        org_id=resolve_org_id(),
        conversation_id=conv_id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=user_message,
        message_type=MessageType.MESSAGE,
        sender_name="You",
        is_ai=False,
        sent_at=now,
    ))
    db.add(Message(
        org_id=resolve_org_id(),
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


def persist_user_message_only(db: Session, *, conv_id: int, body: str) -> None:
    """Persist only the user message (no AI reply) and bump conversation.updated_at."""
    now = datetime.now(UTC)
    db.add(Message(
        org_id=resolve_org_id(),
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
    vendor_id: int | None = None,
    tenant_id: int | None = None,
    ai_typing: bool = False,
) -> Conversation:
    """Create a new conversation for a vendor or tenant.

    Always creates a fresh conversation so each task gets its own thread.
    """
    now = datetime.now(UTC)
    creator_id = resolve_account_id()

    conv = Conversation(
        org_id=resolve_org_id(),
        creator_id=creator_id,
        subject=subject,
        property_id=property_id,
        unit_id=unit_id,
        conversation_type=conversation_type,
        is_group=False,
        is_archived=False,
        extra=set_conversation_ai_typing(None, ai_typing=True) if ai_typing else None,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.flush()

    # Resolve participant user_id and type
    if vendor_id is not None:
        vendor = db.execute(
            select(User).where(User.id == vendor_id, User.user_type == "vendor")
        ).scalar_one()
        db.add(ConversationParticipant(
            org_id=resolve_org_id(),
            conversation_id=conv.id,
            user_id=vendor.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            creator_id=creator_id,
            is_active=True,
        ))
    elif tenant_id is not None:
        tenant = db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        ).scalar_one()
        db.add(ConversationParticipant(
            org_id=resolve_org_id(),
            conversation_id=conv.id,
            user_id=tenant.user_id,
            participant_type=ParticipantType.TENANT,
            creator_id=creator_id,
            is_active=True,
        ))

    db.flush()
    return conv


def send_autonomous_message(
    db: Session,
    *, conversation_id: int,
    body: str,
    task_id: int | None = None,
) -> Message:
    """Send an AI-generated message to a conversation and clear the typing indicator."""
    now = datetime.now(UTC)
    convo = db.query(Conversation).filter_by(
        id=conversation_id,
        org_id=resolve_org_id(),
        creator_id=resolve_account_id(),
    ).first()
    if not convo:
        raise ValueError(f"Conversation {conversation_id} not found")

    msg = Message(
        org_id=resolve_org_id(),
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
    convo.extra = set_conversation_ai_typing(convo.extra, ai_typing=None)
    flag_modified(convo, "extra")

    if task_id:
        task = db.execute(
            select(Task).where(
                Task.id == task_id,
                Task.org_id == resolve_org_id(),
                Task.creator_id == resolve_account_id(),
            )
        ).scalar_one_or_none()
        if task:
            task.last_message_at = now

    db.commit()
    db.refresh(msg)
    return msg


def send_message(
    db: Session,
    *, conversation_id: int,
    body: str,
    message_type: str = MessageType.MESSAGE,
    sender_name: str = "You",
    is_ai: bool = False,
    draft_reply: str | None = None,
    related_task_ids: dict | None = None,
    meta: dict | MessageMeta | None = None,
    sent_at: datetime | None = None,
) -> Message:
    """Add a message to any conversation by conversation_id."""
    now = sent_at or datetime.now(UTC)
    msg = Message(
        org_id=resolve_org_id(),
        conversation_id=conversation_id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=body,
        message_type=message_type,
        sender_name=sender_name,
        is_ai=is_ai,
        meta=dump_message_meta(meta, draft_reply=draft_reply, related_task_ids=related_task_ids),
        is_system=False,
        sent_at=now,
    )
    db.add(msg)
    db.flush()
    return msg


def clear_typing_indicator(db: Session, conversation_id: int) -> None:
    """Remove the ai_typing flag from a conversation."""
    convo = db.query(Conversation).filter_by(
        id=conversation_id,
        org_id=resolve_org_id(),
        creator_id=resolve_account_id(),
    ).first()
    if convo and convo.extra and convo.extra.get("ai_typing"):
        convo.extra = set_conversation_ai_typing(convo.extra, ai_typing=None)
        flag_modified(convo, "extra")
        db.commit()
