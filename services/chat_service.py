import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from agent.history_filters import is_transient_tool_failure_text
from db.id_utils import normalize_optional_id
from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageReceipt,
    MessageType,
    Notification,
    ParticipantType,
    Task,
    Tenant,
    User,
)
from db.queries import fetch_conversations
from integrations.local_auth import resolve_account_id, resolve_org_id

logger = logging.getLogger("rentmate.chat_service")


def build_agent_system_context(*, conversation: Conversation | None, context: str) -> str:
    """Prefix prompt context with the role of the active conversation.

    This prevents the model from confusing the PM's internal task AI thread
    with a tenant/vendor-facing conversation when both transcript types are
    present in the broader task context.
    """
    if conversation is None:
        return context

    convo_type = getattr(conversation, "conversation_type", None)
    prefix_lines: list[str] = []
    if convo_type == ConversationType.TASK_AI:
        prefix_lines = [
            "Active conversation: internal task AI conversation.",
            "The latest user message is from the PM/manager, not from the tenant or vendor.",
            "Do not reply as though you are already talking to the tenant/vendor unless you explicitly send them a separate message.",
        ]
    elif convo_type == ConversationType.USER_AI:
        prefix_lines = [
            "Active conversation: internal AI conversation with the PM/manager.",
            "The latest user message is from the PM/manager.",
            "Retrieved tasks, quotes, vendor threads, and prior coordination may be background from other issues. Do not merge those facts into the current request unless the PM explicitly indicates it is the same task, quote, approval, or thread.",
            "For a fresh operational request in this PM chat, create or propose a task before starting vendor or tenant coordination unless the PM explicitly asked for direct one-off outreach or a direct draft.",
        ]
    elif convo_type == ConversationType.TENANT:
        prefix_lines = [
            "Active conversation: tenant-facing conversation.",
            "The latest user message is from the tenant.",
        ]
    elif convo_type == ConversationType.VENDOR:
        prefix_lines = [
            "Active conversation: vendor-facing conversation.",
            "The latest user message is from the vendor.",
        ]

    if not prefix_lines:
        return context
    return "\n".join(prefix_lines) + "\n\n" + context


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

    kind: Literal["suggestion", "property", "tenant", "document", "question"]
    title: str
    summary: str | None = None
    fields: list[MessageActionCardField] | None = None
    links: list[MessageActionCardLink] | None = None
    units: list[MessageActionCardUnit] | None = None


class MessageReviewCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["on_track", "needs_action", "blocked", "waiting", "recorded"]
    summary: str | None = None
    next_step: str | None = None


class MessageMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str | None = None
    direction: Literal["inbound", "outbound"] | None = None
    draft_reply: str | None = None
    related_task_ids: MessageRelatedTaskIds | None = None
    action_card: MessageActionCard | None = None
    review_card: MessageReviewCard | None = None


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
        if key == "review_card" and value is not None:
            value = MessageReviewCard.model_validate(value)
        setattr(parsed, key, value)
    dumped = parsed.model_dump(exclude_none=True)
    return dumped or None


VISIBLE_UNREAD_MESSAGE_TYPES = (MessageType.MESSAGE, MessageType.THREAD)


def _current_manager_participant(db: Session, *, conversation_id: int) -> ConversationParticipant | None:
    return db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.org_id == resolve_org_id(),
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == resolve_account_id(),
            ConversationParticipant.participant_type == ParticipantType.ACCOUNT_USER,
            ConversationParticipant.is_active.is_(True),
        )
    ).scalar_one_or_none()


def _ensure_manager_participant(db: Session, *, conversation: Conversation) -> ConversationParticipant:
    participant = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.org_id == conversation.org_id,
            ConversationParticipant.conversation_id == conversation.id,
            ConversationParticipant.user_id == conversation.creator_id,
            ConversationParticipant.participant_type == ParticipantType.ACCOUNT_USER,
            ConversationParticipant.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if participant is not None:
        return participant

    participant = ConversationParticipant(
        org_id=conversation.org_id,
        creator_id=conversation.creator_id,
        conversation_id=conversation.id,
        user_id=conversation.creator_id,
        participant_type=ParticipantType.ACCOUNT_USER,
        is_active=True,
    )
    db.add(participant)
    db.flush()
    return participant


def conversation_unread_count(db: Session, *, conversation_id: int) -> int:
    participant = _current_manager_participant(db, conversation_id=conversation_id)
    if participant is None:
        return db.query(Message).filter(
            Message.org_id == resolve_org_id(),
            Message.conversation_id == conversation_id,
            Message.message_type.in_(VISIBLE_UNREAD_MESSAGE_TYPES),
            Message.is_system.is_(False),
            Message.sender_type != ParticipantType.ACCOUNT_USER,
        ).count()

    read_receipt_exists = exists().where(
        MessageReceipt.org_id == Message.org_id,
        MessageReceipt.conversation_id == Message.conversation_id,
        MessageReceipt.message_id == Message.id,
        MessageReceipt.conversation_participant_id == participant.id,
        MessageReceipt.read_at.is_not(None),
    )
    return db.query(Message).filter(
        Message.org_id == resolve_org_id(),
        Message.conversation_id == conversation_id,
        Message.message_type.in_(VISIBLE_UNREAD_MESSAGE_TYPES),
        Message.is_system.is_(False),
        Message.sender_type != ParticipantType.ACCOUNT_USER,
        or_(Message.sender_id.is_(None), Message.sender_id != participant.id),
        ~read_receipt_exists,
    ).count()


def mark_conversation_seen(db: Session, *, conversation_uid: str) -> Conversation:
    conversation = db.execute(
        select(Conversation).where(
            Conversation.external_id == conversation_uid,
            Conversation.org_id == resolve_org_id(),
            Conversation.creator_id == resolve_account_id(),
            Conversation.is_archived.is_(False),
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise ValueError(f"Conversation {conversation_uid} not found")

    participant = _ensure_manager_participant(db, conversation=conversation)

    now = datetime.now(UTC)
    unread_rows = db.query(Message, MessageReceipt).outerjoin(
        MessageReceipt,
        (MessageReceipt.org_id == Message.org_id)
        & (MessageReceipt.conversation_id == Message.conversation_id)
        & (MessageReceipt.message_id == Message.id)
        & (MessageReceipt.conversation_participant_id == participant.id),
    ).filter(
        Message.org_id == resolve_org_id(),
        Message.conversation_id == conversation.id,
        Message.message_type.in_(VISIBLE_UNREAD_MESSAGE_TYPES),
        Message.is_system.is_(False),
        Message.sender_type != ParticipantType.ACCOUNT_USER,
        or_(Message.sender_id.is_(None), Message.sender_id != participant.id),
        or_(MessageReceipt.id.is_(None), MessageReceipt.read_at.is_(None)),
    ).all()
    for message, receipt in unread_rows:
        if receipt is None:
            db.add(MessageReceipt(
                org_id=message.org_id,
                conversation_id=message.conversation_id,
                message_id=message.id,
                conversation_participant_id=participant.id,
                delivered_at=message.sent_at,
                read_at=now,
            ))
        else:
            receipt.read_at = now

    # Opening the conversation also dismisses any in-app notifications
    # routed to it ("Task needs your input", etc.). Without this the bell
    # badge in the header keeps surfacing the same item even after the
    # PM has obviously seen it by reading the thread.
    db.query(Notification).filter(
        Notification.org_id == resolve_org_id(),
        Notification.recipient_user_id == resolve_account_id(),
        Notification.conversation_id == conversation.id,
        Notification.read_at.is_(None),
        Notification.archived_at.is_(None),
    ).update({Notification.read_at: now}, synchronize_session=False)
    return conversation


def _existing_receipt(db: Session, *, message: Message, participant: ConversationParticipant) -> MessageReceipt | None:
    return db.query(MessageReceipt).filter(
        MessageReceipt.org_id == message.org_id,
        MessageReceipt.message_id == message.id,
        MessageReceipt.conversation_participant_id == participant.id,
    ).first()


def create_unread_receipts_for_message(db: Session, *, message: Message) -> None:
    conversation = db.execute(
        select(Conversation).where(
            Conversation.org_id == message.org_id,
            Conversation.id == message.conversation_id,
        )
    ).scalar_one_or_none()
    if conversation is not None:
        _ensure_manager_participant(db, conversation=conversation)

    participants = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.org_id == message.org_id,
            ConversationParticipant.conversation_id == message.conversation_id,
            ConversationParticipant.is_active.is_(True),
        )
    ).scalars().all()
    for participant in participants:
        if participant.id == message.sender_id:
            continue
        if _existing_receipt(db, message=message, participant=participant):
            continue
        db.add(MessageReceipt(
            org_id=message.org_id,
            conversation_id=message.conversation_id,
            message_id=message.id,
            conversation_participant_id=participant.id,
            delivered_at=message.sent_at,
            read_at=None,
        ))


def backfill_unread_receipts_for_conversation(db: Session, *, conversation: Conversation) -> None:
    participant = _ensure_manager_participant(db, conversation=conversation)
    messages = db.query(Message).filter(
        Message.org_id == conversation.org_id,
        Message.conversation_id == conversation.id,
        Message.message_type.in_(VISIBLE_UNREAD_MESSAGE_TYPES),
        Message.is_system.is_(False),
        Message.sender_type != ParticipantType.ACCOUNT_USER,
        or_(Message.sender_id.is_(None), Message.sender_id != participant.id),
    ).all()
    for message in messages:
        if _existing_receipt(db, message=message, participant=participant):
            continue
        db.add(MessageReceipt(
            org_id=message.org_id,
            conversation_id=message.conversation_id,
            message_id=message.id,
            conversation_participant_id=participant.id,
            delivered_at=message.sent_at,
            read_at=None,
        ))

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
    messages = [{"role": "system", "content": build_agent_system_context(conversation=full_conv, context=context)}]
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
    owner_id: int | None = None,
    ai_typing: bool = False,
    parent_task_id: int | None = None,
) -> Conversation:
    """Create a new conversation for a vendor, tenant, or property owner.

    Always creates a fresh conversation so each task gets its own thread.
    Passing `parent_task_id` links this coordination thread to its owning task.
    """
    now = datetime.now(UTC)
    creator_id = resolve_account_id()
    property_id = normalize_optional_id(property_id)
    unit_id = normalize_optional_id(unit_id)

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
        parent_task_id=parent_task_id,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.flush()
    _ensure_manager_participant(db, conversation=conv)

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
    elif owner_id is not None:
        owner = db.execute(
            select(User).where(User.id == owner_id, User.user_type == "owner")
        ).scalar_one()
        db.add(ConversationParticipant(
            org_id=resolve_org_id(),
            conversation_id=conv.id,
            user_id=owner.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
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
    bump_task_activity: bool = True,
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
    if convo.conversation_type == ConversationType.MIRRORED_CHAT:
        from services.extension_service import MirrorConversationReadOnly
        raise MirrorConversationReadOnly(
            f"Conversation {conversation_id} mirrors an external chat thread; "
            "replies must be sent on the source platform."
        )

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

    if task_id and bump_task_activity:
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


def persist_policy_gated_tenant_reply(
    db: Session,
    *,
    conversation_id: int,
    tenant: Tenant,
    reply: str,
    tenant_name: str | None = None,
    entity_phone: str | None = None,
    side_effects: list[dict] | None = None,
    risk_level: str = "medium",
    sent_at: datetime | None = None,
) -> tuple[bool, Message]:
    """Persist an inbound-tenant reply according to outbound message policy.

    Returns ``(sent_directly, message)`` where ``message`` is either the direct
    assistant reply or the created suggestion/approval message.
    """
    from agent.action_policy import ActionCandidate, evaluate_action_candidate
    from agent.side_effects import process_side_effects
    from db.enums import AgentSource, SuggestionOption
    from services import suggestion_service

    now = sent_at or datetime.now(UTC)
    side_effects = side_effects or []
    tenant_name = tenant_name or "Tenant"
    decision = evaluate_action_candidate(ActionCandidate(
        action_class="outbound_message",
        action_name="message_person_send",
        risk_level=risk_level,
    ))

    if decision.allowed:
        msg = send_autonomous_message(
            db,
            conversation_id=conversation_id,
            body=reply,
        )
        process_side_effects(db, side_effects=side_effects, conversation_id=conversation_id, base_time=now)
        return True, msg

    convo = db.query(Conversation).filter_by(
        id=conversation_id,
        org_id=resolve_org_id(),
        creator_id=resolve_account_id(),
    ).first()
    if not convo:
        raise ValueError(f"Conversation {conversation_id} not found")

    options = [
        SuggestionOption(key="send", label=f"Send to {tenant_name}", action="message_person_send", variant="default"),
        SuggestionOption(key="edit", label="Edit Message", action="edit_message", variant="outline"),
        SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
    ]
    action_payload = {
        "action": "message_person",
        "entity_id": str(tenant.external_id),
        "entity_type": "tenant",
        "entity_name": tenant_name,
        "entity_phone": entity_phone,
        "draft_message": reply,
    }
    suggestion = suggestion_service.create_suggestion(
        db,
        title=f"Reply to {tenant_name}",
        ai_context=f"The agent drafted a reply to {tenant_name}.\n\nDraft message:\n{reply}",
        source=AgentSource(),
        options=options,
        action_payload=action_payload,
        property_id=str(convo.property_id) if convo.property_id else None,
        unit_id=str(convo.unit_id) if convo.unit_id else None,
    )
    msg = send_message(
        db,
        conversation_id=conversation_id,
        body=f"Suggested reply for {tenant_name}.",
        message_type=MessageType.SUGGESTION,
        sender_name="RentMate",
        is_ai=True,
        draft_reply=reply,
        related_task_ids={"suggestion_id": suggestion.id},
        sent_at=now,
    )
    convo.updated_at = now
    process_side_effects(db, side_effects=side_effects, conversation_id=conversation_id, base_time=now)
    return False, msg


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
    convo = db.query(Conversation).filter_by(id=conversation_id).first()
    if convo is not None and convo.conversation_type == ConversationType.MIRRORED_CHAT:
        from services.extension_service import MirrorConversationReadOnly
        raise MirrorConversationReadOnly(
            f"Conversation {conversation_id} mirrors an external chat thread; "
            "replies must be sent on the source platform."
        )
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
