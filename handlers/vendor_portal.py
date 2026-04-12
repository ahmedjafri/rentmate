"""Vendor-facing REST endpoints. All require a vendor JWT."""
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Integer, cast, or_, select

from db.models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageType,
    ParticipantType,
    Task,
    User,
)
from gql.services.vendor_service import VendorService, get_vendor_login_email, vendor_has_account
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


def _load_vendor(db, vendor_external_id: str) -> User:
    vendor = db.execute(
        select(User).where(
            User.external_id == vendor_external_id,
            User.user_type == "vendor",
            User.active.is_(True),
        )
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


def _is_assigned_vendor(extra: dict | None, vendor_id: int) -> bool:
    assigned_vendor_id = (extra or {}).get("assigned_vendor_id")
    return str(assigned_vendor_id) == str(vendor_id)


@router.get("/me")
def vendor_me(request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    return {
        "id": str(vendor.external_id),
        "name": vendor.name,
        "company": vendor.company,
        "vendor_type": vendor.role_label,
        "email": get_vendor_login_email(db, vendor),
        "phone": vendor.phone,
        "has_account": vendor_has_account(vendor),
    }


@router.get("/tasks")
def vendor_tasks(request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    vendor_id = vendor.id
    convo_ids = [
        convo_id
        for convo_id in db.execute(
            select(Conversation.id).where(
                or_(
                    Conversation.extra["assigned_vendor_id"].as_string() == str(vendor_id),
                    cast(Conversation.extra["assigned_vendor_id"].as_string(), Integer) == vendor_id,
                )
            )
        ).scalars().all()
    ]
    if not convo_ids:
        return []
    tasks = db.execute(
        select(Task).where(Task.ai_conversation_id.in_(convo_ids))
    ).scalars().all()
    return [
        {
            "id": str(t.id),
            "task_number": t.id,
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


def _verify_vendor_task(db, task_id: str, vendor_id: int) -> Task:
    """Load a task and verify the vendor is assigned to it."""
    task = db.execute(select(Task).where(Task.id == task_id)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    ai_convo = db.get(Conversation, task.ai_conversation_id) if task.ai_conversation_id else None
    if not _is_assigned_vendor(ai_convo.extra if ai_convo else None, vendor_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}")
def vendor_task_detail(task_id: str, request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    task = _verify_vendor_task(db, task_id, vendor.id)
    # Check if someone is typing in the external conversation
    ai_typing = False
    if task.external_conversation_id:
        ext_conv = db.get(Conversation, task.external_conversation_id)
        if ext_conv and (ext_conv.extra or {}).get("ai_typing"):
            ai_typing = True
    return {
        "id": str(task.id),
        "task_number": task.id,
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


class VendorLoginBody(BaseModel):
    email: str
    password: str
    token: str | None = None


class VendorAccountBody(BaseModel):
    email: str
    password: str


@router.post("/login")
def vendor_login(body: VendorLoginBody, request: Request):
    db = get_db(request)
    try:
        vendor, access_token = VendorService.login_with_password(
            db,
            email=body.email,
            password=body.password,
            portal_token=body.token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "access_token": access_token,
        "vendor_id": str(vendor.external_id),
        "name": vendor.name,
        "email": get_vendor_login_email(db, vendor),
    }


@router.post("/account")
def vendor_create_account(body: VendorAccountBody, request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    try:
        vendor, access_token = VendorService.create_account_from_vendor(
            db,
            vendor=vendor,
            email=body.email,
            password=body.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "access_token": access_token,
        "vendor_id": str(vendor.external_id),
        "name": vendor.name,
        "email": vendor.email,
        "has_account": True,
    }


@router.post("/tasks/{task_id}/messages")
def vendor_send_message(task_id: str, body: SendMessageBody, request: Request):
    info = _require_vendor(request)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Message body required")
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    task = _verify_vendor_task(db, task_id, vendor.id)
    if not task.external_conversation_id:
        raise HTTPException(status_code=400, detail="No external conversation for this task")

    # Ensure vendor is a participant on the external conversation (idempotent)
    existing = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == task.external_conversation_id,
            ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
            ConversationParticipant.user_id == vendor.id,
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(ConversationParticipant(
            org_id=vendor.org_id,
            creator_id=vendor.creator_id,
            conversation_id=task.external_conversation_id,
            user_id=vendor.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            is_active=True,
        ))
        db.flush()
        existing = db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == task.external_conversation_id,
                ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
                ConversationParticipant.user_id == vendor.id,
            )
        ).scalar_one()

    now = datetime.now(UTC)
    msg = Message(
        org_id=vendor.org_id,
        conversation_id=task.external_conversation_id,
        sender_type=ParticipantType.EXTERNAL_CONTACT,
        sender_id=existing.id,
        body=body.body.strip(),
        message_type=MessageType.MESSAGE,
        sender_name=vendor.name,
        is_ai=False,
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
