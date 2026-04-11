"""Tenant-facing REST endpoints. All require a tenant JWT."""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from db.models import Conversation, ConversationParticipant, Lease, Message, MessageType, ParticipantType, Task, Tenant
from gql.services.tenant_service import TenantService
from handlers.deps import get_db

router = APIRouter(prefix="/api/tenant")


def _require_tenant(request: Request) -> dict:
    """Validate tenant JWT from Authorization header."""
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return TenantService.validate_tenant_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/me")
def tenant_me(request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    tenant = db.get(Tenant, info["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {
        "id": str(tenant.id),
        "name": tenant.user.name,
        "email": tenant.user.email,
        "phone": tenant.user.phone,
    }


def _tenant_tasks(db, tenant_id: str) -> list:
    """Find tasks linked to this tenant via unit, property, or conversation."""
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return []
    task_ids: set[str] = set()

    # Via lease → unit → task
    leases = db.execute(
        select(Lease).where(Lease.tenant_id == tenant_id)
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
        for t in db.execute(select(Task)).scalars():
            if t.parent_conversation_id in conv_set or t.external_conversation_id in conv_set:
                task_ids.add(t.id)

    if not task_ids:
        return []

    tasks = db.execute(
        select(Task).where(Task.id.in_(task_ids))
    ).scalars().all()
    return [
        {
            "id": str(t.id),
            "task_number": t.task_number,
            "title": t.title,
            "status": t.task_status,
            "category": t.category,
            "created_at": t.created_at.isoformat() + "Z",
        }
        for t in tasks
        if t.task_status not in ("dismissed", "cancelled")
    ]


@router.get("/tasks")
def tenant_tasks(request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    return _tenant_tasks(db, info["tenant_id"])


def _task_messages_for_tenant(db, task: Task, tenant_id: str) -> list:
    """Return messages from the tenant conversation on this task."""
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return []
    # Check parent_conversation_id first (tenant convos usually linked here)
    conv_id = None
    for cid in [task.parent_conversation_id, task.external_conversation_id]:
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
    return [
        {
            "id": str(m.id),
            "body": m.body or "",
            "sender_name": m.sender_name or "",
            "sender_type": m.sender_type.value if m.sender_type else "account_user",
            "is_ai": m.is_ai,
            "sent_at": m.sent_at.isoformat() + "Z",
        }
        for m in msgs
        if m.message_type not in (MessageType.INTERNAL, MessageType.APPROVAL, MessageType.SUGGESTION, MessageType.CONTEXT)
    ]


def _verify_tenant_task(db, task_id: str, tenant_id: str) -> Task:
    """Load a task and verify the tenant has access."""
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    task = db.execute(select(Task).where(Task.id == task_id)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check via unit lease
    if task.unit_id:
        lease = db.execute(
            select(Lease).where(Lease.tenant_id == tenant_id, Lease.unit_id == task.unit_id)
        ).scalars().first()
        if lease:
            return task

    # Check via property lease (task without unit_id)
    if task.property_id:
        lease = db.execute(
            select(Lease).where(Lease.tenant_id == tenant_id, Lease.property_id == task.property_id)
        ).scalars().first()
        if lease:
            return task

    # Check via conversation participant
    for cid in [task.parent_conversation_id, task.external_conversation_id]:
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
    tenant = db.get(Tenant, info["tenant_id"])
    task = _verify_tenant_task(db, task_id, info["tenant_id"])
    # Check typing indicator on the conversation
    typing = False
    for cid in [task.parent_conversation_id, task.external_conversation_id]:
        if cid:
            conv = db.get(Conversation, cid)
            if conv and (conv.extra or {}).get("ai_typing"):
                typing = True
                break
    return {
        "id": str(task.id),
        "task_number": task.task_number,
        "title": task.title,
        "status": task.task_status,
        "category": task.category,
        "created_at": task.created_at.isoformat() + "Z",
        "messages": _task_messages_for_tenant(db, task, info["tenant_id"]),
        "typing": typing,
    }


class SendMessageBody(BaseModel):
    body: str


@router.post("/tasks/{task_id}/messages")
def tenant_send_message(task_id: str, msg: SendMessageBody, request: Request):
    info = _require_tenant(request)
    db = get_db(request)
    task = _verify_tenant_task(db, task_id, info["tenant_id"])
    tenant = db.get(Tenant, info["tenant_id"])
    tenant_name = tenant.user.name if tenant and tenant.user else "Tenant"

    # Find the tenant conversation
    conv_id = None
    for cid in [task.parent_conversation_id, task.external_conversation_id]:
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
    db.commit()
    db.refresh(message)

    # Trigger agent heartbeat in background
    import threading
    _task_id = str(task.id)
    _hint = f"{tenant_name} sent a message: {msg.body.strip()[:100]}"
    threading.Thread(
        target=_run_heartbeat, args=(_task_id, _hint), daemon=True,
    ).start()

    return {
        "id": str(message.id),
        "body": message.body,
        "sender_name": message.sender_name,
        "sender_type": "tenant",
        "is_ai": False,
        "sent_at": message.sent_at.isoformat() + "Z",
    }


def _run_heartbeat(task_id: str, hint: str):
    import time
    print(f"\033[33m[heartbeat] Triggering for task {task_id}: {hint}\033[0m")
    time.sleep(1)  # let the request session close before accessing DB
    try:
        from handlers.chat import agent_task_heartbeat
        result = agent_task_heartbeat(task_id, hint=hint)
        print(f"\033[33m[heartbeat] Result for task {task_id}: {'replied' if result else 'no response'}\033[0m")
    except Exception as e:
        print(f"\033[31m[heartbeat] Failed for task {task_id}: {e}\033[0m")
        import traceback
        traceback.print_exc()
