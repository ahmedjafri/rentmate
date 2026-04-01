"""Vendor-facing REST endpoints. All require a vendor JWT."""
from datetime import UTC, datetime
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from db.models import Conversation, ConversationParticipant, ExternalContact, Message, MessageType, ParticipantType
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


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/login")
def vendor_login(body: LoginBody, request: Request):
    db = get_db(request)
    try:
        vendor, jwt_token = VendorService.authenticate_vendor(db, body.email, body.password)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {"access_token": jwt_token, "vendor_id": str(vendor.id), "name": vendor.name}


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
        "email": (vendor.extra or {}).get("vendor_email"),
    }


@router.get("/tasks")
def vendor_tasks(request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    all_tasks = db.execute(
        select(Conversation).where(Conversation.is_task == True)  # noqa: E712
    ).scalars().all()
    mine = [t for t in all_tasks if (t.extra or {}).get("assigned_vendor_id") == info["vendor_id"]]
    return [
        {
            "id": str(t.id),
            "title": t.subject,
            "status": t.task_status,
            "category": t.category,
            "created_at": str(t.created_at),
        }
        for t in mine
    ]


def _task_messages_for_vendor(task: Conversation) -> list:
    """Return messages visible to the vendor: skip internal/approval/context types."""
    visible = []
    for m in sorted(task.messages, key=lambda x: x.sent_at):
        if m.message_type in (MessageType.INTERNAL, MessageType.APPROVAL, MessageType.CONTEXT):
            continue
        visible.append({
            "id": str(m.id),
            "body": m.body or "",
            "sender_name": m.sender_name or "",
            "sender_type": m.sender_type.value if m.sender_type else "account_user",
            "is_ai": m.is_ai,
            "sent_at": str(m.sent_at),
        })
    return visible


@router.get("/tasks/{task_id}")
def vendor_task_detail(task_id: str, request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    task = db.execute(
        select(Conversation).where(Conversation.id == task_id, Conversation.is_task == True)  # noqa: E712
    ).scalar_one_or_none()
    if not task or (task.extra or {}).get("assigned_vendor_id") != info["vendor_id"]:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "id": str(task.id),
        "title": task.subject,
        "status": task.task_status,
        "category": task.category,
        "urgency": task.urgency,
        "created_at": str(task.created_at),
        "messages": _task_messages_for_vendor(task),
    }


class SendMessageBody(BaseModel):
    body: str


@router.post("/tasks/{task_id}/messages")
def vendor_send_message(task_id: str, body: SendMessageBody, request: Request):
    info = _require_vendor(request)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Message body required")
    db = get_db(request)
    task = db.execute(
        select(Conversation).where(Conversation.id == task_id, Conversation.is_task == True)  # noqa: E712
    ).scalar_one_or_none()
    if not task or (task.extra or {}).get("assigned_vendor_id") != info["vendor_id"]:
        raise HTTPException(status_code=404, detail="Task not found")

    vendor = db.get(ExternalContact, info["vendor_id"])
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # Ensure vendor is a participant (idempotent)
    existing = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == task_id,
            ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
            ConversationParticipant.external_contact_id == info["vendor_id"],
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(ConversationParticipant(
            id=str(uuid.uuid4()),
            conversation_id=task_id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            external_contact_id=info["vendor_id"],
            is_active=True,
            joined_at=datetime.now(UTC),
        ))
        db.flush()

    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=task_id,
        sender_type=ParticipantType.EXTERNAL_CONTACT,
        sender_external_contact_id=info["vendor_id"],
        body=body.body.strip(),
        message_type=MessageType.MESSAGE,
        sender_name=vendor.name,
        is_ai=False,
        is_system=False,
        sent_at=datetime.now(UTC),
    )
    db.add(msg)
    task.last_message_at = datetime.now(UTC)
    db.commit()

    return {
        "id": str(msg.id),
        "body": msg.body,
        "sender_name": msg.sender_name,
        "sender_type": "external_contact",
        "is_ai": False,
        "sent_at": str(msg.sent_at),
    }
