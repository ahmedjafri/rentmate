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
import json
from datetime import UTC, datetime
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id
from db.lib import get_conversation_with_messages, route_inbound_to_task
from db.models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageType,
    ParticipantType,
    Suggestion,
    Task,
    Tenant,
)
from handlers.deps import get_db, require_user
from llm.context import build_task_context_data
from llm.registry import agent_registry
from llm.retrieval import (
    ChromaMemoryIndex,
    RetrievalRequest,
    list_memory_items,
    retrieve_context,
    sync_memory_index,
)
from llm.tracing import make_trace_envelope

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


class MemoryItemResponse(BaseModel):
    id: str
    source_type: str
    source_id: str
    entity_type: str
    entity_id: str
    visibility: str
    title: str | None
    content: str
    metadata: dict[str, Any]
    updated_at: str | None


class RetrievalDebugRequest(BaseModel):
    query: str = ""
    intent: str = "answer_question"
    surface: str = "dev"
    task_id: str | None = None
    property_id: str | None = None
    unit_id: str | None = None
    tenant_id: str | None = None
    vendor_id: str | None = None
    limit: int = 12


def _parse_trace_detail(detail: str | None) -> Any:
    if not detail:
        return None
    try:
        return json.loads(detail)
    except Exception:
        return detail


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
    tenant_name = tenant.user.name if tenant.user else "Tenant"

    if body.force_new:
        now = datetime.now(UTC)
        task = Task(
            creator_id=tenant.creator_id,
            title=f"Message from {tenant_name}",
            task_status="active",
            task_mode="autonomous",
            source=DEV_SIM_SOURCE,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        db.flush()

        conv = Conversation(
            creator_id=tenant.creator_id,
            subject=f"Message from {tenant_name}",
            is_group=False,
            is_archived=False,
            created_at=now,
            updated_at=now,
        )
        db.add(conv)
        db.flush()

        db.add(ConversationParticipant(
            org_id=tenant.org_id,
            creator_id=tenant.creator_id,
            conversation_id=conv.id,
            user_id=tenant.user_id,
            participant_type=ParticipantType.TENANT,
            is_active=True,
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
                ConversationParticipant.user_id == tenant.user_id,
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
                ConversationParticipant.user_id == tenant.user_id,
                ConversationParticipant.is_active.is_(True),
            )
            .count()
        )
        task_created = after_count > existing_count

    db.commit()

    # Run agent — use the conversation id for context building
    context_data = build_task_context_data(db, conv.id)
    context = context_data["text"]
    full_conv = get_conversation_with_messages(db=db, conversation_id=conv.id)
    sorted_msgs = sorted(full_conv.messages, key=lambda m: m.sent_at)
    history_msgs = sorted_msgs[:-1][-20:]
    messages = [{"role": "system", "content": context}]
    for m in history_msgs:
        messages.append({"role": "assistant" if m.is_ai else "user", "content": m.body or ""})
    messages.append({"role": "user", "content": body.message})

    from llm.client import call_agent
    from llm.side_effects import process_side_effects
    agent_id = agent_registry.ensure_agent(str(resolve_account_id()), db)

    try:
        agent_resp = await call_agent(
            agent_id,
            session_key=f"sim:{conv.id}",
            messages=messages,
            trace_context=make_trace_envelope(
                "llm_request",
                flow="dev_sim",
                session_key=f"sim:{conv.id}",
                messages_payload=messages,
                context=context_data,
                retrieval=context_data.get("retrieval"),
                reasoning={
                    "available": False,
                    "note": "No provider reasoning trace available for this response.",
                },
            ),
        )
        reply = agent_resp.reply
    except Exception as e:
        print(f"[dev/simulate-inbound] Agent failed: {e}")
        reply = "[Agent unavailable]"
        agent_resp = None

    now = datetime.now(UTC)
    db.add(Message(
        org_id=tenant.org_id,
        conversation_id=conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=reply,
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=now,
    ))
    if agent_resp and agent_resp.side_effects:
        process_side_effects(db, side_effects=agent_resp.side_effects, conversation_id=conv.id, base_time=now)
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
    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    if not tenant:
        return DevHistoryResponse(task_id=None, messages=[])

    # Most recent active dev_sim task for this tenant
    conv = (
        db.query(Conversation)
        .join(Task, Task.id == Conversation.task_id)
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .filter(
            Task.source == DEV_SIM_SOURCE,
            Task.task_status == "active",
            ConversationParticipant.user_id == tenant.user_id,
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


@router.get("/memory-items", response_model=list[MemoryItemResponse])
async def get_memory_items(
    request: Request,
    db: Session = Depends(get_db),
    query: str = "",
    source_type: str | None = None,
    entity_type: str | None = None,
    visibility: str | None = None,
    limit: int = 200,
):
    await require_user(request)
    sync_memory_index(db)
    rows = list_memory_items(db, query=query, limit=min(limit, 500))
    if source_type:
        rows = [row for row in rows if row.source_type == source_type]
    if entity_type:
        rows = [row for row in rows if row.entity_type == entity_type]
    if visibility:
        rows = [row for row in rows if row.visibility == visibility]
    return [
        MemoryItemResponse(
            id=row.id,
            source_type=row.source_type,
            source_id=row.source_id,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            visibility=row.visibility,
            title=row.title,
            content=row.content,
            metadata=row.metadata_json or {},
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )
        for row in rows
    ]


@router.post("/retrieve-context")
async def dev_retrieve_context(
    body: RetrievalDebugRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    bundle = retrieve_context(db, RetrievalRequest(
        surface=body.surface,
        intent=body.intent,
        query=body.query,
        task_id=body.task_id,
        property_id=body.property_id,
        unit_id=body.unit_id,
        tenant_id=body.tenant_id,
        vendor_id=body.vendor_id,
        limit=min(body.limit, 30),
    ))
    return {
        "request": body.model_dump(),
        "items": [
            {
                "memory_item_id": item.memory_item_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "title": item.title,
                "content": item.content,
                "metadata": item.metadata,
                "heuristic_score": item.heuristic_score,
                "vector_score": item.vector_score,
                "final_score": item.final_score,
                "reasons": item.reasons,
            }
            for item in bundle.items
        ],
    }


@router.post("/reindex-memory")
async def reindex_memory(
    request: Request,
    db: Session = Depends(get_db),
    reset_index: bool = False,
):
    await require_user(request)
    if reset_index:
        ChromaMemoryIndex().reset()
    count = sync_memory_index(db)
    return {"count": count, "reset_index": reset_index}


@router.get("/traces")
async def list_traces(
    request: Request,
    db: Session = Depends(get_db),
    task_id: str | None = None,
    conversation_id: str | None = None,
    source: str | None = None,
    trace_type: str | None = None,
    limit: int = 100,
):
    """Return recent agent traces for debugging."""
    await require_user(request)
    from sqlalchemy import select

    from db.models import AgentTrace

    q = select(AgentTrace).order_by(AgentTrace.timestamp.desc())
    if task_id:
        q = q.where(AgentTrace.task_id == task_id)
    if conversation_id:
        q = q.where(AgentTrace.conversation_id == conversation_id)
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


@router.get("/trace-filters/tasks")
async def list_trace_tasks(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = 200,
):
    await require_user(request)
    rows = (
        db.query(Task)
        .filter(
            (Task.ai_conversation_id.isnot(None))
            | (Task.external_conversation_id.isnot(None))
            | (Task.parent_conversation_id.isnot(None))
            | (Task.source == DEV_SIM_SOURCE)
        )
        .order_by(Task.updated_at.desc())
        .limit(min(limit, 500))
        .all()
    )
    return [
        {
            "id": str(task.id),
            "title": task.title,
            "status": task.task_status,
            "mode": task.task_mode,
            "source": task.source,
            "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        }
        for task in rows
    ]


@router.get("/trace-filters/chats")
async def list_trace_chats(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = 200,
):
    await require_user(request)
    rows = db.query(Conversation).order_by(Conversation.updated_at.desc()).limit(min(limit * 3, 600)).all()
    rows = [
        conv for conv in rows
        if any((msg.is_ai or msg.sender_name == "RentMate") for msg in (conv.messages or []))
    ][: min(limit, 500)]
    payload = []
    for conv in rows:
        linked_task_id = (
            db.query(Task.id)
            .filter(
                (Task.ai_conversation_id == conv.id)
                | (Task.external_conversation_id == conv.id)
                | (Task.parent_conversation_id == conv.id)
            )
            .order_by(Task.id.asc())
            .scalar()
        )
        payload.append({
            "id": str(conv.id),
            "subject": conv.subject or "Untitled chat",
            "updated_at": conv.updated_at.isoformat() + "Z" if conv.updated_at else None,
            "task_id": str(linked_task_id) if linked_task_id else None,
        })
    return payload


@router.get("/traces/{trace_id}")
async def get_trace_detail(
    trace_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    from db.models import AgentTrace

    trace = db.query(AgentTrace).filter_by(id=trace_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    parsed_detail = _parse_trace_detail(trace.detail)
    return {
        "id": trace.id,
        "timestamp": trace.timestamp.isoformat() + "Z",
        "trace_type": trace.trace_type,
        "source": trace.source,
        "task_id": trace.task_id,
        "conversation_id": trace.conversation_id,
        "tool_name": trace.tool_name,
        "summary": trace.summary,
        "detail": parsed_detail,
        "raw_detail": trace.detail,
        "suggestion_id": trace.suggestion_id,
    }


@router.delete("/traces")
async def wipe_traces(request: Request, db: Session = Depends(get_db)):
    """Delete all agent traces."""
    await require_user(request)
    from db.models import AgentTrace
    count = db.query(AgentTrace).count()
    db.query(AgentTrace).delete()
    db.commit()
    return {"deleted_traces": count}
