# handlers/dev.py
"""Developer / test-lab endpoints.

POST /dev/simulate-inbound  — simulate an inbound tenant message (SMS or email)
  without sending any real SMS or email.  Writes a real task + messages to the DB
  (source='dev_sim') so the DevTools page can restore them.  These tasks are
  excluded from the normal Action Desk view.

GET  /dev/history/{tenant_id} — returns the most recent dev_sim task messages
  for a tenant, used by the DevTools chat panel to restore history on reload.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.lib import route_inbound_to_task, get_conversation_with_messages
from db.models import Conversation, ConversationParticipant, Message, MessageType, ParticipantType, Suggestion, Task, Tenant
from handlers.deps import get_db, require_user
from llm.context import build_task_context
from llm.registry import agent_registry

router = APIRouter()

DEV_SIM_SOURCE = "dev_sim"


class SimulateInboundRequest(BaseModel):
    tenant_id: str
    channel_type: str   # 'sms' | 'email'
    message: str
    force_new: bool = False  # if True, always create a new task


class SimulateInboundResponse(BaseModel):
    task_id: str
    reply: str
    task_created: bool


class DevChatMessage(BaseModel):
    role: str        # 'tenant' | 'agent'
    text: str
    task_id: str


class DevHistoryResponse(BaseModel):
    task_id: str | None
    messages: List[DevChatMessage]


@router.post("/simulate-inbound", response_model=SimulateInboundResponse)
async def simulate_inbound(
    body: SimulateInboundRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)

    tenant = db.query(Tenant).filter_by(id=body.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    sender_meta = {"source": DEV_SIM_SOURCE, "simulated": True}

    if body.force_new:
        now = datetime.now(UTC)
        task = Task(
            id=str(uuid.uuid4()),
            account_id=tenant.account_id,
            title=f"Message from {tenant.first_name} {tenant.last_name}",
            task_status="active",
            task_mode="autonomous",
            source=DEV_SIM_SOURCE,
            channel_type=body.channel_type,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        db.flush()

        conv = Conversation(
            id=str(uuid.uuid4()),
            account_id=tenant.account_id,
            task_id=task.id,
            subject=f"Message from {tenant.first_name} {tenant.last_name}",
            is_group=False,
            is_archived=False,
            created_at=now,
            updated_at=now,
        )
        db.add(conv)
        db.flush()

        db.add(ConversationParticipant(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            participant_type=ParticipantType.TENANT,
            tenant_id=tenant.id,
            is_active=True,
            joined_at=now,
        ))
        db.flush()
        db.refresh(conv)

        from db.lib import add_message
        add_message(
            db=db,
            conversation=conv,
            sender_type=ParticipantType.TENANT,
            body=body.message,
            meta=sender_meta,
            sender_tenant=tenant,
        )
        task_created = True
    else:
        # Count existing dev_sim tasks for this tenant to detect new creation
        existing_count = (
            db.query(Task)
            .join(Conversation, Conversation.task_id == Task.id)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .filter(
                Task.source == DEV_SIM_SOURCE,
                Task.task_status == "active",
                ConversationParticipant.tenant_id == tenant.id,
                ConversationParticipant.is_active.is_(True),
            )
            .count()
        )

        conv, _ = await asyncio.to_thread(
            route_inbound_to_task,
            db,
            tenant=tenant,
            body=body.message,
            channel_type=body.channel_type,
            sender_meta=sender_meta,
        )
        # Stamp the task as dev_sim (route_inbound_to_task sets source=channel_type)
        if conv.task_id:
            linked_task = db.query(Task).filter(Task.id == conv.task_id).first()
            if linked_task:
                linked_task.source = DEV_SIM_SOURCE
        db.flush()

        after_count = (
            db.query(Task)
            .join(Conversation, Conversation.task_id == Task.id)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .filter(
                Task.source == DEV_SIM_SOURCE,
                Task.task_status == "active",
                ConversationParticipant.tenant_id == tenant.id,
                ConversationParticipant.is_active.is_(True),
            )
            .count()
        )
        task_created = after_count > existing_count

    db.commit()

    # Run agent — use the conversation id for context building
    context = build_task_context(db, conv.id)
    full_conv = get_conversation_with_messages(db=db, conversation_id=conv.id)
    sorted_msgs = sorted(full_conv.messages, key=lambda m: m.sent_at)
    history_msgs = sorted_msgs[:-1][-20:]
    messages = [{"role": "system", "content": context}]
    for m in history_msgs:
        messages.append({"role": "assistant" if m.is_ai else "user", "content": m.body or ""})
    messages.append({"role": "user", "content": body.message})

    from backends.local_auth import DEFAULT_USER_ID
    from llm.client import call_agent
    from llm.side_effects import process_side_effects
    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)

    try:
        agent_resp = await call_agent(agent_id, f"sim:{conv.id}", messages)
        reply = agent_resp.reply
    except Exception as e:
        print(f"[dev/simulate-inbound] Agent failed: {e}")
        reply = "[Agent unavailable]"
        agent_resp = None

    now = datetime.now(UTC)
    db.add(Message(
        id=str(uuid.uuid4()),
        conversation_id=conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=reply,
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=now,
    ))
    if agent_resp and agent_resp.side_effects:
        process_side_effects(db, agent_resp.side_effects, conv.id, now)
    db.commit()

    task_id = conv.task_id if conv.task_id else conv.id
    return SimulateInboundResponse(task_id=task_id, reply=reply, task_created=task_created)


@router.get("/history/{tenant_id}", response_model=DevHistoryResponse)
async def get_dev_history(
    tenant_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)

    # Most recent active dev_sim task for this tenant
    conv = (
        db.query(Conversation)
        .join(Task, Task.id == Conversation.task_id)
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .filter(
            Task.source == DEV_SIM_SOURCE,
            Task.task_status == "active",
            ConversationParticipant.tenant_id == tenant_id,
            ConversationParticipant.is_active.is_(True),
        )
        .order_by(Conversation.updated_at.desc())
        .first()
    )

    if not conv:
        return DevHistoryResponse(task_id=None, messages=[])

    task_id = conv.task_id if conv.task_id else conv.id
    full = get_conversation_with_messages(db, conv.id)
    sorted_msgs = sorted(full.messages, key=lambda m: m.sent_at)

    return DevHistoryResponse(
        task_id=task_id,
        messages=[
            DevChatMessage(
                role="agent" if m.is_ai else "tenant",
                text=m.body or "",
                task_id=task_id,
            )
            for m in sorted_msgs
            if m.body  # skip empty/system messages
        ],
    )


@router.delete("/wipe-tasks")
async def wipe_tasks(request: Request, db: Session = Depends(get_db)):
    """Delete ALL tasks, linked suggestions, and their conversations/messages."""
    await require_user(request)
    # Suggestions with CASCADE FK will be auto-deleted, but collect their conv IDs first
    suggestions = db.query(Suggestion).filter(Suggestion.task_id.isnot(None)).all()
    conv_ids = set()
    for s in suggestions:
        if s.ai_conversation_id:
            conv_ids.add(s.ai_conversation_id)
    tasks = db.query(Task).all()
    for t in tasks:
        if t.ai_conversation_id:
            conv_ids.add(t.ai_conversation_id)
        if t.external_conversation_id:
            conv_ids.add(t.external_conversation_id)
        db.delete(t)  # cascades to linked suggestions
    db.flush()
    for cid in conv_ids:
        conv = db.get(Conversation, cid)
        if conv:
            db.delete(conv)
    db.commit()
    return {"deleted_tasks": len(tasks), "deleted_conversations": len(conv_ids)}


@router.delete("/wipe-suggestions")
async def wipe_suggestions(request: Request, db: Session = Depends(get_db)):
    """Delete ALL suggestions and their AI conversations."""
    await require_user(request)
    suggestions = db.query(Suggestion).all()
    conv_ids = set()
    for s in suggestions:
        if s.ai_conversation_id:
            conv_ids.add(s.ai_conversation_id)
        db.delete(s)
    db.flush()
    for cid in conv_ids:
        conv = db.get(Conversation, cid)
        if conv:
            db.delete(conv)
    db.commit()
    return {"deleted_suggestions": len(suggestions), "deleted_conversations": len(conv_ids)}


@router.delete("/wipe-chats")
async def wipe_chats(request: Request, db: Session = Depends(get_db)):
    """Delete ALL conversations and their messages (also clears task conversation FKs)."""
    await require_user(request)
    # Unlink tasks from their conversations first
    tasks = db.query(Task).filter(
        (Task.ai_conversation_id.isnot(None)) | (Task.external_conversation_id.isnot(None))
    ).all()
    for t in tasks:
        t.ai_conversation_id = None
        t.external_conversation_id = None
    db.flush()
    convos = db.query(Conversation).all()
    for c in convos:
        db.delete(c)
    db.commit()
    return {"deleted_conversations": len(convos), "unlinked_tasks": len(tasks)}


@router.get("/traces")
async def list_traces(
    request: Request,
    db: Session = Depends(get_db),
    task_id: str | None = None,
    source: str | None = None,
    trace_type: str | None = None,
    limit: int = 100,
):
    """Return recent agent traces for debugging."""
    await require_user(request)
    from db.models import AgentTrace
    from sqlalchemy import select

    q = select(AgentTrace).order_by(AgentTrace.timestamp.desc())
    if task_id:
        q = q.where(AgentTrace.task_id == task_id)
    if source:
        q = q.where(AgentTrace.source == source)
    if trace_type:
        q = q.where(AgentTrace.trace_type == trace_type)
    q = q.limit(min(limit, 500))

    traces = db.execute(q).scalars().all()
    return [
        {
            "id": t.id,
            "timestamp": t.timestamp.isoformat() + "Z",
            "trace_type": t.trace_type,
            "source": t.source,
            "task_id": t.task_id,
            "conversation_id": t.conversation_id,
            "tool_name": t.tool_name,
            "summary": t.summary,
            "detail": t.detail,
            "suggestion_id": t.suggestion_id,
        }
        for t in traces
    ]


@router.delete("/traces")
async def wipe_traces(request: Request, db: Session = Depends(get_db)):
    """Delete all agent traces."""
    await require_user(request)
    from db.models import AgentTrace
    count = db.query(AgentTrace).count()
    db.query(AgentTrace).delete()
    db.commit()
    return {"deleted_traces": count}
