"""Vendor-facing REST endpoints. All require a vendor JWT."""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from db.models import (
    Conversation,
    ConversationParticipant,
    ExternalContact,
    Message,
    MessageType,
    ParticipantType,
    Task,
)
from gql.services.vendor_service import VendorService
from handlers.deps import get_db

router = APIRouter(prefix="/api/vendor")


def _require_vendor(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return VendorService.validate_vendor_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/me")
def vendor_me(request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor = db.get(ExternalContact, info["vendor_id"])
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return {
        "id": str(vendor.id),
        "name": vendor.name,
        "company": vendor.company,
        "vendor_type": vendor.role_label,
        "email": vendor.email,
        "phone": vendor.phone,
    }


@router.get("/tasks")
def vendor_tasks(request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor_id = info["vendor_id"]
    # Find conversations where this vendor is assigned, then get their tasks
    from sqlalchemy import text
    convo_ids = [
        row[0] for row in db.execute(
            text("SELECT id FROM conversations WHERE json_extract(extra, '$.assigned_vendor_id') = :vid"),
            {"vid": vendor_id},
        ).fetchall()
    ]
    if not convo_ids:
        return []
    tasks = db.execute(
        select(Task).where(Task.ai_conversation_id.in_(convo_ids))
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
    ]


def _task_messages_for_vendor(db, task: Task) -> list:
    """Return messages from the external conversation visible to the vendor."""
    if not task.external_conversation_id:
        return []
    convo = db.get(Conversation, task.external_conversation_id)
    if not convo:
        return []
    msgs = db.execute(
        select(Message)
        .where(Message.conversation_id == convo.id)
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


def _verify_vendor_task(db, task_id: str, vendor_id: str) -> Task:
    """Load a task and verify the vendor is assigned to it."""
    task = db.execute(select(Task).where(Task.id == task_id)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    ai_convo = db.get(Conversation, task.ai_conversation_id) if task.ai_conversation_id else None
    ai_extra = (ai_convo.extra or {}) if ai_convo else {}
    if ai_extra.get("assigned_vendor_id") != vendor_id:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}")
def vendor_task_detail(task_id: str, request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    task = _verify_vendor_task(db, task_id, info["vendor_id"])
    # Check if someone is typing in the external conversation
    ai_typing = False
    if task.external_conversation_id:
        ext_conv = db.get(Conversation, task.external_conversation_id)
        if ext_conv and (ext_conv.extra or {}).get("ai_typing"):
            ai_typing = True
    return {
        "id": str(task.id),
        "task_number": task.task_number,
        "title": task.title,
        "status": task.task_status,
        "category": task.category,
        "urgency": task.urgency,
        "created_at": task.created_at.isoformat() + "Z",
        "messages": _task_messages_for_vendor(db, task),
        "typing": ai_typing,
    }


class SendMessageBody(BaseModel):
    body: str


@router.post("/tasks/{task_id}/messages")
def vendor_send_message(task_id: str, body: SendMessageBody, request: Request):
    info = _require_vendor(request)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Message body required")
    db = get_db(request)
    task = _verify_vendor_task(db, task_id, info["vendor_id"])
    if not task.external_conversation_id:
        raise HTTPException(status_code=400, detail="No external conversation for this task")

    vendor = db.get(ExternalContact, info["vendor_id"])
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # Ensure vendor is a participant on the external conversation (idempotent)
    existing = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == task.external_conversation_id,
            ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
            ConversationParticipant.external_contact_id == info["vendor_id"],
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(ConversationParticipant(
            id=str(uuid.uuid4()),
            conversation_id=task.external_conversation_id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            external_contact_id=info["vendor_id"],
            is_active=True,
            joined_at=datetime.now(UTC),
        ))
        db.flush()

    now = datetime.now(UTC)
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=task.external_conversation_id,
        sender_type=ParticipantType.EXTERNAL_CONTACT,
        sender_external_contact_id=info["vendor_id"],
        body=body.body.strip(),
        message_type=MessageType.MESSAGE,
        sender_name=vendor.name,
        is_ai=False,
        is_system=False,
        sent_at=now,
    )
    db.add(msg)
    task.last_message_at = now
    db.commit()

    # Trigger agent heartbeat in background
    import threading
    _task_id = str(task.id)
    _hint = f"{vendor.name} sent a message: {body.body.strip()[:100]}"
    threading.Thread(
        target=_run_heartbeat, args=(_task_id, _hint), daemon=True,
    ).start()

    return {
        "id": str(msg.id),
        "body": msg.body,
        "sender_name": msg.sender_name,
        "sender_type": "external_contact",
        "is_ai": False,
        "sent_at": msg.sent_at.isoformat() + "Z",
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
