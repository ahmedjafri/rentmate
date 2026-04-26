"""Tenant-facing REST endpoints. All require a tenant JWT."""
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.models import Conversation, ConversationParticipant, Lease, Message, MessageType, ParticipantType, Task, Tenant
from gql.services import chat_service
from gql.services.tenant_service import TenantService
from handlers.deps import get_db
from handlers.portals._common import (
    SendMessageBody,
    notify_task_owner_of_portal_message,
    read_bearer_token,
    serialize_portal_conversation_row,
    serialize_task_list_row,
    serialize_visible_messages,
    trigger_task_autoreply,
)

router = APIRouter(prefix="/api/tenant")


def _require_tenant(request: Request) -> dict:
    """Validate tenant JWT from Authorization header."""
    token = read_bearer_token(request)
    try:
        return TenantService.validate_tenant_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


def _load_tenant(db, tenant_external_id: str) -> Tenant:
    tenant = db.execute(
        select(Tenant).where(Tenant.external_id == tenant_external_id)
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.get("/me")
def tenant_me(request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    tenant = _load_tenant(db, info["tenant_id"])
    return {
        "id": str(tenant.external_id),
        "name": tenant.user.name,
        "email": tenant.user.email,
        "phone": tenant.user.phone,
    }


def _tenant_tasks(db, tenant_external_id: str) -> list:
    """Find tasks linked to this tenant via unit, property, or conversation."""
    tenant = _load_tenant(db, tenant_external_id)
    task_ids: set[str] = set()

    # Via lease → unit → task
    leases = db.execute(
        select(Lease).where(Lease.tenant_id == tenant.id)
    ).scalars().all()
    unit_ids = {l.unit_id for l in leases if l.unit_id}
    property_ids = {l.property_id for l in leases if l.property_id}

    if unit_ids:
        for t in db.execute(select(Task).where(Task.unit_id.in_(unit_ids))).scalars():
            task_ids.add(t.id)

    # Via property (tasks without unit_id but on tenant's property)
    if property_ids:
        for t in db.execute(select(Task).where(
            Task.property_id.in_(property_ids), Task.unit_id.is_(None),
        )).scalars():
            task_ids.add(t.id)

    # Via conversation participant (tenant is on a linked conversation)
    participant_convos = db.execute(
        select(ConversationParticipant.conversation_id).where(
            ConversationParticipant.user_id == tenant.user_id,
            ConversationParticipant.is_active.is_(True),
        )
    ).scalars().all()
    if participant_convos:
        conv_set = set(participant_convos)
        # Bulk-load the task→external-conversation map instead of touching
        # the lazy ``Task.external_conversations`` relationship per row;
        # that relationship uses a custom primaryjoin and detaches in some
        # session contexts here, throwing DetachedInstanceError.
        ext_convo_rows = db.execute(
            select(Conversation.parent_task_id, Conversation.id).where(
                Conversation.parent_task_id.is_not(None),
                Conversation.id.in_(conv_set),
            )
        ).all()
        external_convo_task_ids = {row[0] for row in ext_convo_rows}

        for t in db.execute(
            select(Task.id, Task.parent_conversation_id)
        ).all():
            tid, parent_convo_id = t
            if parent_convo_id in conv_set or tid in external_convo_task_ids:
                task_ids.add(tid)

    if not task_ids:
        return []

    tasks = db.execute(
        select(Task).where(Task.id.in_(task_ids))
    ).scalars().all()
    return [
        serialize_task_list_row(t)
        for t in tasks
        if t.task_status not in ("dismissed", "cancelled")
    ]


def _tenant_conversation_for_task(db, task: Task, tenant: Tenant) -> Conversation | None:
    candidate_ids: list[int] = []
    if task.parent_conversation_id:
        candidate_ids.append(task.parent_conversation_id)
    candidate_ids.extend(c.id for c in task.external_conversations)
    for cid in candidate_ids:
        if not cid:
            continue
        participant = db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == cid,
                ConversationParticipant.user_id == tenant.user_id,
                ConversationParticipant.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if participant:
            return db.execute(
                select(Conversation)
                .where(Conversation.id == cid)
                .options(selectinload(Conversation.messages))
            ).scalar_one_or_none()
    return None


def _tenant_portal_conversations(db, tenant_external_id: str) -> list[dict]:
    tenant = _load_tenant(db, tenant_external_id)
    rows: list[dict] = []
    seen_conv_ids: set[int] = set()

    # 1. Task-linked conversations — walk each tenant task to its tenant-side
    #    conversation so the row's `linked_task` payload is populated.
    task_ids = [int(row["id"]) for row in _tenant_tasks(db, tenant_external_id)]
    if task_ids:
        tasks = db.execute(
            select(Task)
            .where(Task.id.in_(task_ids))
            .options(selectinload(Task.external_conversations).selectinload(Conversation.messages))
        ).scalars().all()
        for task in tasks:
            conversation = _tenant_conversation_for_task(db, task, tenant)
            if conversation and conversation.id not in seen_conv_ids:
                seen_conv_ids.add(conversation.id)
                rows.append(serialize_portal_conversation_row(conversation, task=task))

    # 2. Standalone conversations — any conversation the tenant participates in
    #    that isn't attached to a task (e.g. routine check-ins or one-off
    #    outreach). These were previously hidden from the portal.
    standalone = db.execute(
        select(Conversation)
        .join(
            ConversationParticipant,
            ConversationParticipant.conversation_id == Conversation.id,
        )
        .where(
            ConversationParticipant.user_id == tenant.user_id,
            ConversationParticipant.is_active.is_(True),
            Conversation.parent_task_id.is_(None),
        )
        .options(selectinload(Conversation.messages))
    ).scalars().all()
    for conversation in standalone:
        if conversation.id in seen_conv_ids:
            continue
        seen_conv_ids.add(conversation.id)
        rows.append(serialize_portal_conversation_row(conversation, task=None))

    rows.sort(key=lambda row: row["last_message_at"] or row["updated_at"], reverse=True)
    return rows


@router.get("/tasks")
def tenant_tasks(request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    return _tenant_tasks(db, info["tenant_id"])


@router.get("/conversations")
def tenant_conversations(request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    return _tenant_portal_conversations(db, info["tenant_id"])


def _task_messages_for_tenant(db, task: Task, tenant_external_id: str) -> list:
    """Return messages from the tenant conversation on this task."""
    tenant = _load_tenant(db, tenant_external_id)
    # Check parent_conversation_id first (tenant convos usually linked here),
    # then any linked external conversations.
    candidate_ids: list[int] = []
    if task.parent_conversation_id:
        candidate_ids.append(task.parent_conversation_id)
    candidate_ids.extend(c.id for c in task.external_conversations)
    conv_id = None
    for cid in candidate_ids:
        if not cid:
            continue
        conv = db.get(Conversation, cid)
        if not conv:
            continue
        # Check if tenant is a participant
        participant = db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == cid,
                ConversationParticipant.user_id == tenant.user_id,
            )
        ).scalar_one_or_none()
        if participant:
            conv_id = cid
            break

    if not conv_id:
        return []

    msgs = db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.sent_at)
    ).scalars().all()
    return serialize_visible_messages(msgs)


def _verify_tenant_conversation(db, conversation_id: int, tenant_external_id: str) -> tuple[Tenant, Conversation, Task | None]:
    tenant = _load_tenant(db, tenant_external_id)
    conversation = db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    ).scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    participant = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == tenant.user_id,
            ConversationParticipant.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if not participant:
        raise HTTPException(status_code=404, detail="Conversation not found")

    task = None
    if conversation.parent_task_id:
        task = db.execute(select(Task).where(Task.id == conversation.parent_task_id)).scalar_one_or_none()
    if task is None:
        task = db.execute(select(Task).where(Task.parent_conversation_id == conversation.id)).scalar_one_or_none()
    return tenant, conversation, task


def _verify_tenant_task(db, task_id: str, tenant_external_id: str) -> Task:
    """Load a task and verify the tenant has access."""
    tenant = _load_tenant(db, tenant_external_id)
    task = db.execute(select(Task).where(Task.id == task_id)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check via unit lease
    if task.unit_id:
        lease = db.execute(
            select(Lease).where(Lease.tenant_id == tenant.id, Lease.unit_id == task.unit_id)
        ).scalars().first()
        if lease:
            return task

    # Check via property lease (task without unit_id)
    if task.property_id:
        lease = db.execute(
            select(Lease).where(Lease.tenant_id == tenant.id, Lease.property_id == task.property_id)
        ).scalars().first()
        if lease:
            return task

    # Check via conversation participant
    candidate_ids: list[int] = []
    if task.parent_conversation_id:
        candidate_ids.append(task.parent_conversation_id)
    candidate_ids.extend(c.id for c in task.external_conversations)
    for cid in candidate_ids:
        if cid:
            participant = db.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == cid,
                    ConversationParticipant.user_id == tenant.user_id,
                )
            ).scalar_one_or_none()
            if participant:
                return task

    raise HTTPException(status_code=404, detail="Task not found")


@router.get("/tasks/{task_id}")
def tenant_task_detail(task_id: str, request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    task = _verify_tenant_task(db, task_id, info["tenant_id"])
    # Check typing indicator on the conversation
    typing = False
    candidate_ids: list[int] = []
    if task.parent_conversation_id:
        candidate_ids.append(task.parent_conversation_id)
    candidate_ids.extend(c.id for c in task.external_conversations)
    for cid in candidate_ids:
        if cid:
            conv = db.get(Conversation, cid)
            if conv and (conv.extra or {}).get("ai_typing"):
                typing = True
                break
    return {
        "id": str(task.id),
        "task_number": task.id,
        "title": task.title,
        "status": task.task_status,
        "category": task.category,
        "created_at": task.created_at.isoformat() + "Z",
        "messages": _task_messages_for_tenant(db, task, info["tenant_id"]),
        "typing": typing,
    }


@router.get("/conversations/{conversation_id}")
def tenant_conversation_detail(conversation_id: int, request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    _tenant, conversation, task = _verify_tenant_conversation(db, conversation_id, info["tenant_id"])
    return {
        **serialize_portal_conversation_row(conversation, task=task),
        "messages": serialize_visible_messages(conversation.messages),
    }


@router.post("/tasks/{task_id}/messages")
def tenant_send_message(task_id: str, msg: SendMessageBody, request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    task = _verify_tenant_task(db, task_id, info["tenant_id"])
    tenant = _load_tenant(db, info["tenant_id"])
    tenant_name = tenant.user.name if tenant and tenant.user else "Tenant"

    # Find the tenant conversation
    conv_id = None
    candidate_ids: list[int] = []
    if task.parent_conversation_id:
        candidate_ids.append(task.parent_conversation_id)
    candidate_ids.extend(c.id for c in task.external_conversations)
    for cid in candidate_ids:
        if not cid:
            continue
        participant = db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == cid,
                ConversationParticipant.user_id == tenant.user_id,
            )
        ).scalar_one_or_none()
        if participant:
            conv_id = cid
            break

    if not conv_id:
        raise HTTPException(status_code=400, detail="No conversation found")

    now = datetime.now(UTC)
    message = Message(
        org_id=tenant.org_id,
        conversation_id=conv_id,
        sender_type=ParticipantType.TENANT,
        sender_id=participant.id if participant else None,
        body=msg.body,
        message_type=MessageType.MESSAGE,
        sender_name=tenant_name,
        is_ai=False,
        sent_at=now,
    )
    db.add(message)
    db.flush()
    chat_service.create_unread_receipts_for_message(db, message=message)
    notify_task_owner_of_portal_message(
        db,
        task=task,
        conversation=conversation,
        sender_label=tenant_name,
        body=msg.body,
        actor_kind="tenant",
        message_id=message.id,
    )
    db.commit()
    db.refresh(message)

    trigger_task_autoreply(str(task.id), sender_name=tenant_name, body=msg.body)

    return {
        "id": str(message.id),
        "body": message.body,
        "sender_name": message.sender_name,
        "sender_type": "tenant",
        "is_ai": False,
        "sent_at": message.sent_at.isoformat() + "Z",
    }


@router.post("/conversations/{conversation_id}/messages")
def tenant_send_conversation_message(conversation_id: int, msg: SendMessageBody, request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    tenant, conversation, task = _verify_tenant_conversation(db, conversation_id, info["tenant_id"])
    tenant_name = tenant.user.name if tenant and tenant.user else "Tenant"
    participant = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation.id,
            ConversationParticipant.user_id == tenant.user_id,
            ConversationParticipant.is_active.is_(True),
        )
    ).scalar_one()

    now = datetime.now(UTC)
    message = Message(
        org_id=tenant.org_id,
        conversation_id=conversation.id,
        sender_type=ParticipantType.TENANT,
        sender_id=participant.id,
        body=msg.body,
        message_type=MessageType.MESSAGE,
        sender_name=tenant_name,
        is_ai=False,
        sent_at=now,
    )
    db.add(message)
    db.flush()
    chat_service.create_unread_receipts_for_message(db, message=message)
    notify_task_owner_of_portal_message(
        db,
        task=task,
        conversation=conversation,
        sender_label=tenant_name,
        body=msg.body,
        actor_kind="tenant",
        message_id=message.id,
    )
    db.commit()
    db.refresh(message)

    if task:
        trigger_task_autoreply(str(task.id), sender_name=tenant_name, body=msg.body)

    return {
        "id": str(message.id),
        "body": message.body,
        "sender_name": message.sender_name,
        "sender_type": "tenant",
        "is_ai": False,
        "sent_at": message.sent_at.isoformat() + "Z",
    }
