"""Vendor-facing REST endpoints. All require a vendor JWT."""
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Integer, cast, or_, select
from sqlalchemy.orm import selectinload

from db.models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageType,
    ParticipantType,
    Task,
    User,
)
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
from services import chat_service
from services.vendor_service import VendorService, get_vendor_login_email, vendor_has_account

router = APIRouter(prefix="/api/vendor")


def _require_vendor(request: Request) -> dict:
    token = read_bearer_token(request)
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
    return [serialize_task_list_row(t) for t in tasks]


def _vendor_portal_conversations(db, vendor_id: int) -> list[dict]:
    tasks = db.execute(
        select(Task)
        .options(selectinload(Task.external_conversations).selectinload(Conversation.messages))
    ).scalars().all()
    rows: list[dict] = []
    for task in tasks:
        convo = _vendor_conversation_for_task(db, task, vendor_id)
        if convo:
            rows.append(serialize_portal_conversation_row(convo, task=task))
    rows.sort(key=lambda row: row["last_message_at"] or row["updated_at"], reverse=True)
    return rows


@router.get("/conversations")
def vendor_conversations(request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    return _vendor_portal_conversations(db, vendor.id)


def _vendor_conversation_for_task(db, task: Task, vendor_id: int) -> Conversation | None:
    """Find the external conversation on this task where the vendor is a participant."""
    for convo in task.external_conversations:
        participant = db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == convo.id,
                ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
                ConversationParticipant.user_id == vendor_id,
            )
        ).scalar_one_or_none()
        if participant:
            return convo
    return None


def _task_messages_for_vendor(db, task: Task, vendor_id: int) -> list:
    """Return messages from the vendor's external conversation for this task."""
    convo = _vendor_conversation_for_task(db, task, vendor_id)
    if not convo:
        return []
    msgs = db.execute(
        select(Message)
        .where(Message.conversation_id == convo.id)
        .order_by(Message.sent_at)
    ).scalars().all()
    return serialize_visible_messages(msgs)


def _verify_vendor_task(db, task_id: str, vendor_id: int) -> Task:
    """Load a task and verify the vendor is assigned to it."""
    task = db.execute(select(Task).where(Task.id == task_id)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    ai_convo = db.get(Conversation, task.ai_conversation_id) if task.ai_conversation_id else None
    if not _is_assigned_vendor(ai_convo.extra if ai_convo else None, vendor_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _verify_vendor_conversation(db, conversation_id: int, vendor_id: int) -> tuple[Task | None, Conversation]:
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
            ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
            ConversationParticipant.user_id == vendor_id,
            ConversationParticipant.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if not participant:
        raise HTTPException(status_code=404, detail="Conversation not found")
    task = None
    if conversation.parent_task_id:
        task = db.execute(select(Task).where(Task.id == conversation.parent_task_id)).scalar_one_or_none()
    return task, conversation


@router.get("/tasks/{task_id}")
def vendor_task_detail(task_id: str, request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    task = _verify_vendor_task(db, task_id, vendor.id)
    # Check if someone is typing in the vendor's external conversation
    ai_typing = False
    ext_conv = _vendor_conversation_for_task(db, task, vendor.id)
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
        "messages": _task_messages_for_vendor(db, task, vendor.id),
        "typing": ai_typing,
    }


@router.get("/conversations/{conversation_id}")
def vendor_conversation_detail(conversation_id: int, request: Request):
    info = _require_vendor(request)
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    task, conversation = _verify_vendor_conversation(db, conversation_id, vendor.id)
    return {
        **serialize_portal_conversation_row(conversation, task=task),
        "messages": serialize_visible_messages(conversation.messages),
    }


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
    convo = _vendor_conversation_for_task(db, task, vendor.id)
    if not convo:
        raise HTTPException(status_code=400, detail="No external conversation for this task")
    convo_id = convo.id

    # Ensure vendor is a participant on the external conversation (idempotent)
    existing = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == convo_id,
            ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
            ConversationParticipant.user_id == vendor.id,
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(ConversationParticipant(
            org_id=vendor.org_id,
            creator_id=vendor.creator_id,
            conversation_id=convo_id,
            user_id=vendor.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            is_active=True,
        ))
        db.flush()
        existing = db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == convo_id,
                ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
                ConversationParticipant.user_id == vendor.id,
            )
        ).scalar_one()

    now = datetime.now(UTC)
    msg = Message(
        org_id=vendor.org_id,
        conversation_id=convo_id,
        sender_type=ParticipantType.EXTERNAL_CONTACT,
        sender_id=existing.id,
        body=body.body.strip(),
        message_type=MessageType.MESSAGE,
        sender_name=vendor.name,
        is_ai=False,
        sent_at=now,
    )
    db.add(msg)
    db.flush()
    chat_service.create_unread_receipts_for_message(db, message=msg)
    task.last_message_at = now
    notify_task_owner_of_portal_message(
        db,
        task=task,
        conversation=convo,
        sender_label=vendor.name,
        body=body.body,
        actor_kind="vendor",
        message_id=msg.id,
    )
    db.commit()

    trigger_task_autoreply(str(task.id), sender_name=vendor.name, body=body.body)

    return {
        "id": str(msg.id),
        "body": msg.body,
        "sender_name": msg.sender_name,
        "sender_type": "external_contact",
        "is_ai": False,
        "sent_at": msg.sent_at.isoformat() + "Z",
    }


@router.post("/conversations/{conversation_id}/messages")
def vendor_send_conversation_message(conversation_id: int, body: SendMessageBody, request: Request):
    info = _require_vendor(request)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Message body required")
    db = get_db(request)
    vendor = _load_vendor(db, info["vendor_id"])
    task, conversation = _verify_vendor_conversation(db, conversation_id, vendor.id)

    existing = db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation.id,
            ConversationParticipant.participant_type == ParticipantType.EXTERNAL_CONTACT,
            ConversationParticipant.user_id == vendor.id,
        )
    ).scalar_one()

    now = datetime.now(UTC)
    msg = Message(
        org_id=vendor.org_id,
        conversation_id=conversation.id,
        sender_type=ParticipantType.EXTERNAL_CONTACT,
        sender_id=existing.id,
        body=body.body.strip(),
        message_type=MessageType.MESSAGE,
        sender_name=vendor.name,
        is_ai=False,
        sent_at=now,
    )
    db.add(msg)
    db.flush()
    chat_service.create_unread_receipts_for_message(db, message=msg)
    if task is not None:
        task.last_message_at = now
    notify_task_owner_of_portal_message(
        db,
        task=task,
        conversation=conversation,
        sender_label=vendor.name,
        body=body.body,
        actor_kind="vendor",
        message_id=msg.id,
    )
    db.commit()

    if task is not None:
        trigger_task_autoreply(str(task.id), sender_name=vendor.name, body=body.body)

    return {
        "id": str(msg.id),
        "body": msg.body,
        "sender_name": msg.sender_name,
        "sender_type": "external_contact",
        "is_ai": False,
        "sent_at": msg.sent_at.isoformat() + "Z",
    }
