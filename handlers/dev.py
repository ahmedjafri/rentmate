# handlers/dev.py
"""Developer / test-lab endpoints for wiping state, memory debugging, and trace inspection.

Inbound-message simulation has been removed — use the tenant/vendor portals directly
with a valid portal JWT to drive the real chat flow.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Conversation,
    Suggestion,
    Task,
)
from handlers.deps import get_db, require_user
from llm.retrieval import (
    PgVectorMemoryIndex,
    RetrievalRequest,
    list_memory_items,
    retrieve_context,
    sync_memory_index,
)

router = APIRouter()

DEV_SIM_SOURCE = "dev_sim"


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
        for c in t.external_conversations:
            conv_ids.add(c.id)
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
    tasks = db.query(Task).filter(Task.ai_conversation_id.isnot(None)).all()
    for t in tasks:
        t.ai_conversation_id = None
    db.flush()
    # Conversation.parent_task_id has ON DELETE SET NULL, so it'll be cleared
    # automatically when the task rows are removed. No explicit unlink needed.
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
        PgVectorMemoryIndex(db).reset()
    count = sync_memory_index(db)
    return {"count": count, "reset_index": reset_index}


@router.get("/traces")
async def list_traces(
    request: Request,
    db: Session = Depends(get_db),
    run_id: str | None = None,
    task_id: str | None = None,
    task_scope: str | None = None,
    conversation_id: str | None = None,
    source: str | None = None,
    trace_type: str | None = None,
    limit: int = 100,
):
    """Return recent agent traces for debugging.

    When ``run_id`` is supplied, traces are returned in their
    ``sequence_num`` order so the UI can render the run's timeline.
    """
    await require_user(request)
    from sqlalchemy import select

    from db.models import AgentRun, AgentTrace

    needs_run_join = bool(task_id or conversation_id)
    if run_id:
        order = (AgentTrace.sequence_num.asc(),)
    else:
        order = (AgentTrace.timestamp.desc(),)
    q = select(AgentTrace).order_by(*order)
    if needs_run_join:
        q = q.join(
            AgentRun,
            (AgentTrace.org_id == AgentRun.org_id) & (AgentTrace.run_id == AgentRun.id),
        )
    if run_id:
        q = q.where(AgentTrace.run_id == run_id)
    if task_id:
        q = q.where(AgentRun.task_id == task_id)
        if task_scope == "routine":
            q = q.where(AgentTrace.source.in_(["routine", "simulate"]))
        elif task_scope == "task":
            q = q.where(
                (AgentTrace.source.is_(None))
                | (~AgentTrace.source.in_(["routine", "simulate"]))
            )
    if conversation_id:
        q = q.where(AgentRun.conversation_id == conversation_id)
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
            "run_id": t.run_id,
            "sequence_num": t.sequence_num,
            "task_id": t.run.task_id if t.run else None,
            "conversation_id": t.run.conversation_id if t.run else None,
            "tool_name": t.tool_name,
            "summary": t.summary,
            "detail": t.detail,
            "suggestion_id": t.suggestion_id,
        }
        for t in traces
    ]


@router.get("/runs")
async def list_runs(
    request: Request,
    db: Session = Depends(get_db),
    task_id: str | None = None,
    conversation_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
    limit: int = 50,
):
    """Return recent agent runs (newest first) with per-run step + trace counts.

    The dev UI groups events by run; per-run trajectory comes from
    ``GET /dev/runs/{run_id}/trajectory`` (ATIF v1.4). ``step_count`` is
    the post-cutover ATIF step count; ``trace_count`` is the legacy
    AgentTrace count for runs that pre-date the trajectory rewrite.
    """
    await require_user(request)
    from sqlalchemy import func, select

    from db.models import AgentRun, AgentStep, AgentTrace

    trace_count_subq = (
        select(AgentTrace.run_id, func.count().label("trace_count"))
        .group_by(AgentTrace.run_id)
        .subquery()
    )
    step_count_subq = (
        select(AgentStep.run_id, func.count().label("step_count"))
        .group_by(AgentStep.run_id)
        .subquery()
    )
    q = (
        select(
            AgentRun,
            trace_count_subq.c.trace_count,
            step_count_subq.c.step_count,
        )
        .outerjoin(trace_count_subq, trace_count_subq.c.run_id == AgentRun.id)
        .outerjoin(step_count_subq, step_count_subq.c.run_id == AgentRun.id)
        .order_by(AgentRun.started_at.desc())
    )
    if task_id:
        q = q.where(AgentRun.task_id == task_id)
    if conversation_id:
        q = q.where(AgentRun.conversation_id == conversation_id)
    if source:
        q = q.where(AgentRun.source == source)
    if status:
        q = q.where(AgentRun.status == status)
    q = q.limit(min(limit, 200))

    rows = db.execute(q).all()
    out: list[dict] = []
    for run, trace_count, step_count in rows:
        duration_ms: int | None = None
        if run.ended_at and run.started_at:
            duration_ms = int((run.ended_at - run.started_at).total_seconds() * 1000)
        out.append({
            "id": run.id,
            "source": run.source,
            "status": run.status,
            "task_id": run.task_id,
            "conversation_id": run.conversation_id,
            "model": run.model,
            "agent_version": run.agent_version,
            "execution_path": run.execution_path,
            "started_at": run.started_at.isoformat() + "Z" if run.started_at else None,
            "ended_at": run.ended_at.isoformat() + "Z" if run.ended_at else None,
            "duration_ms": duration_ms,
            "iteration_count": run.iteration_count,
            "total_input_tokens": run.total_input_tokens,
            "total_output_tokens": run.total_output_tokens,
            "total_cost_cents": str(run.total_cost_cents) if run.total_cost_cents is not None else "0",
            "trigger_input": (run.trigger_input or "")[:240] or None,
            "final_response": (run.final_response or "")[:240] or None,
            "error_message": run.error_message,
            "step_count": int(step_count or 0),
            "trace_count": int(trace_count or 0),
        })
    return out


@router.get("/runs/{run_id}/trajectory")
async def get_run_trajectory(
    run_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the run's full ATIF v1.4 trajectory.

    Reads from ``agent_steps`` for post-cutover runs; falls back to a
    legacy adapter that synthesizes ATIF Steps from ``agent_traces`` for
    runs that pre-date the trajectory rewrite. See
    ``llm/trajectory.py:to_trajectory``.
    """
    await require_user(request)
    from llm.trajectory import to_trajectory

    trajectory = to_trajectory(db, run_id)
    if trajectory is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return trajectory


@router.get("/trace-filters/tasks")
async def list_trace_tasks(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = 200,
):
    await require_user(request)
    rows = [
        {
            "id": f"task:{task.id}",
            "raw_id": str(task.id),
            "scope": "task",
            "title": task.title,
            "status": task.task_status,
            "mode": task.task_mode,
            "source": task.source,
            "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        }
        for task in (
            db.query(Task)
            .filter(
                (Task.ai_conversation_id.isnot(None))
                | (Task.parent_conversation_id.isnot(None))
                | (Task.id.in_(
                    select(Conversation.parent_task_id)
                    .where(Conversation.parent_task_id.isnot(None))
                ))
                | (Task.source == DEV_SIM_SOURCE)
            )
            .order_by(Task.updated_at.desc())
            .limit(min(limit, 500))
            .all()
        )
    ]

    from db.models import Routine

    routine_rows = (
        db.query(Routine)
        .filter(
            (Routine.last_run_at.isnot(None))
            | (Routine.simulated_at.isnot(None))
        )
        .order_by(Routine.updated_at.desc())
        .limit(min(limit, 500))
        .all()
    )
    rows.extend(
        {
            "id": f"routine:{task.id}",
            "raw_id": str(task.id),
            "scope": "routine",
            "title": task.name,
            "status": task.last_status or task.state,
            "mode": "routine",
            "source": "routine",
            "updated_at": task.updated_at.isoformat() + "Z" if task.updated_at else None,
        }
        for task in routine_rows
    )
    rows.sort(key=lambda item: item["updated_at"] or "", reverse=True)
    return rows[: min(limit, 500)]


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
                | (Task.id == conv.parent_task_id)
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
        "run_id": trace.run_id,
        "sequence_num": trace.sequence_num,
        "task_id": trace.run.task_id if trace.run else None,
        "conversation_id": trace.run.conversation_id if trace.run else None,
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
