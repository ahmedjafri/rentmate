"""Periodic task-review loop.

Scans active tasks on a cadence (default hourly cooldown, 5-min sweep
interval) and asks the agent to produce a structured review per task via
the ``record_task_review`` tool. A new tenant/vendor message on any task
conversation makes that task eligible immediately — no cooldown-wait —
so a reply always gets a fresh review instead of a stale cached one.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from agent.time import current_utc
from handlers.deps import require_user

logger = logging.getLogger("rentmate.task_review")
router = APIRouter()

_TASK_REVIEW_POLL_SECONDS = 300          # 5-minute sweep cadence
_TASK_REVIEW_COOLDOWN = timedelta(hours=1)


def _follow_up_wait_window_text(task) -> str:
    from db.enums import Urgency, parse_urgency

    urgency = parse_urgency(getattr(task, "urgency", None))
    if urgency == Urgency.CRITICAL:
        return "for critical-urgency tasks, same-day follow-up can be appropriate when the issue is still urgent"
    if urgency == Urgency.HIGH:
        return "for high-urgency tasks, wait roughly 4 hours before another follow-up unless new information arrived"
    if urgency == Urgency.MEDIUM:
        return "for medium-urgency tasks, wait roughly 12 hours before another follow-up unless new information arrived"
    return "for low-urgency tasks, wait about 24 hours before another follow-up unless new information arrived"


def _resolve_review_recipients(db, task) -> tuple[str, str]:
    """Return ``(tenant_block, vendor_block)`` strings for the review prompt.

    The review loop runs detached from any chat context, so the agent has
    no implicit knowledge of who's involved — we have to inline the exact
    external UUIDs the recipients use in ``message_person`` calls. Missing
    recipients render as "(none)".
    """
    from agent.tools._common import _resolve_task_tenant
    from db.models import User

    tenant_line = "(none)"
    tenant = _resolve_task_tenant(db, str(task.id))
    if tenant and getattr(tenant, "user", None):
        user = tenant.user
        full_name = " ".join(filter(None, [user.first_name, user.last_name])) or "Tenant"
        phone = user.phone or "(no phone)"
        email = user.email or "(no email)"
        tenant_line = (
            f"{full_name} — tenant_id={tenant.external_id}, phone={phone}, email={email}"
        )

    vendor_line = "(none)"
    # Reach into the latest external conversation's extra for vendor info —
    # same source chat_service uses for assigned_vendor_id. Re-query via
    # the passed-in session instead of relying on ORM relationships, since
    # the review flow passes a detached task instance.
    from db.models import Conversation as ConvModel
    ext_convo = (
        db.query(ConvModel)
        .filter_by(parent_task_id=task.id)
        .order_by(ConvModel.updated_at.desc())
        .first()
    )
    extra = getattr(ext_convo, "extra", None) if ext_convo else None
    if isinstance(extra, dict):
        vendor_id = extra.get("assigned_vendor_id")
        if vendor_id is not None:
            vendor = db.query(User).filter_by(id=vendor_id, user_type="vendor").first()
            if vendor:
                phone = vendor.phone or "(no phone)"
                vendor_line = (
                    f"{vendor.name} — vendor_id={vendor.external_id}, phone={phone}"
                )
    return tenant_line, vendor_line


def _build_review_prompt(task, db) -> str:
    """Compose the user-message prompt for a single task review.

    Everything the agent needs to make an honest call is inlined here so
    the review LLM call is self-contained and repeatable — in particular
    the external UUIDs for any tenant/vendor attached to the task, since
    those are the IDs the agent needs to call ``message_person``.
    """
    goal = (task.goal or "").strip() or "(no goal set)"
    status = task.task_status.value if getattr(task.task_status, "value", None) else str(task.task_status or "")
    steps = task.steps or []
    steps_text = (
        "\n".join(
            f"  - [{s.get('status', '?')}] {s.get('label', '')}"
            for s in steps
            if isinstance(s, dict)
        )
        if steps else "(no steps)"
    )
    context_notes = (task.context or "").strip() or "(none)"
    tenant_line, vendor_line = _resolve_review_recipients(db, task)
    follow_up_window = _follow_up_wait_window_text(task)

    return (
        f"Review task #{task.id} against its goal and move it forward.\n\n"
        f"**Current time**: {current_utc().isoformat()}\n"
        f"**Title**: {task.title or '(untitled)'}\n"
        f"**Status**: {status}\n"
        f"**Goal**: {goal}\n"
        f"**Steps**:\n{steps_text}\n"
        f"**Context notes**: {context_notes}\n"
        f"**Tenant on this task**: {tenant_line}\n"
        f"**Vendor on this task**: {vendor_line}\n"
        f"**Last reviewed**: "
        f"{task.last_reviewed_at.isoformat() if task.last_reviewed_at else 'never'}\n\n"
        "Inspect any recent external-conversation activity on this task, "
        "then decide: is it on_track, needs_action, blocked, or waiting? "
        "When you need to call `message_person`, pass the exact "
        "tenant_id or vendor_id shown above — never the lease id, unit "
        "id, or any other UUID. If that line says '(none)' then no "
        "recipient is attached and you should ask the manager before "
        "messaging anyone. "
        f"You MUST pass `task_id=\"{task.id}\"` to every `message_person` "
        "call you make on this task — without it the suggestion gets "
        "orphaned from the task and the manager can't open it in the "
        "right tenant/vendor conversation. "
        "If a concrete next step is obvious and safe — for example, "
        "messaging a tenant or vendor for information you're waiting on "
        "— take it yourself using the normal tools (message_person, "
        "propose_task, etc.). The outbound-message policy gate will hold "
        "back anything high-risk for manager review automatically. "
        "IMPORTANT: before messaging someone, check the recent "
        "conversation history — if you already reached out and the ball "
        "is in their court, record status=waiting rather than nagging "
        f"them again; {follow_up_window}. If a vendor has already provided quote and/or "
        "availability information, do NOT ask them for quote/pricing/"
        "availability again unless the manager explicitly requested a "
        "new or refreshed quote. Use that existing vendor response to "
        "decide the next step — usually tenant coordination, approval "
        "handling, or waiting. "
        "Never send or stage a tenant/vendor message with bracketed placeholders "
        "like [payment portal link], [vendor phone], or [time window]. If a "
        "concrete detail is missing, call `ask_manager` and get the exact value "
        "before messaging anyone. "
        "Do not mark a confirmation/check-it-worked step done just because "
        "you sent a reminder or follow-up. A confirmation-style step is only "
        "done after the tenant/vendor actually confirms the outcome. "
        "Progress steps must stay in sync with your review. If you conclude "
        "that a listed step has been completed, call `update_task_progress` "
        "to mark that exact step done before you call `record_task_review`. "
        "If work has moved to the next listed step, mark that next step "
        "`active` as well. `record_task_review` only records review metadata "
        "and does NOT update task progress for you. "
        "If the blocker is PM approval or another manager decision on this "
        "same task, use `ask_manager` in the task AI conversation. Do not "
        "create a suggestion for an in-task approval step. "
        "If you need clarification from the manager to move forward — "
        "e.g. a decision only they can make, missing context, or an "
        "approval — call `ask_manager` to post a direct question to "
        "this task's AI conversation. Don't guess at manager intent; "
        "ask.\n\n"
        f"At the end, call `record_task_review` exactly once with "
        f"task_id=\"{task.id}\" summarising what you found and what "
        "action you took (or why you took none)."
    )


def _select_due_tasks(db):
    """Return active tasks eligible for review on this sweep.

    A task is eligible when ANY of:
      - it has never been reviewed
      - cooldown has expired
      - a tenant/vendor message landed on one of its external
        conversations after the last review (recent_external_msg EXISTS)
    """
    from db.enums import TaskStatus
    from db.models import Conversation, Message, ParticipantType, Task

    now = datetime.now(UTC)
    cooldown_floor = now - _TASK_REVIEW_COOLDOWN

    recent_external_msg = (
        select(1)
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.parent_task_id == Task.id,
            Message.sender_type.in_([
                ParticipantType.TENANT,
                ParticipantType.EXTERNAL_CONTACT,
            ]),
            Message.sent_at > func.coalesce(Task.last_reviewed_at, Task.created_at),
        )
        .exists()
    )

    stmt = (
        select(Task)
        .where(
            Task.task_status == TaskStatus.ACTIVE,
            or_(
                Task.last_reviewed_at.is_(None),
                Task.last_reviewed_at < cooldown_floor,
                recent_external_msg,
            ),
        )
        .order_by(Task.last_reviewed_at.asc().nulls_first(), Task.id.asc())
    )
    return db.execute(stmt).scalars().all()


_REVIEW_CARD_STATUSES = {"on_track", "needs_action", "blocked", "waiting", "recorded"}


def _persist_review_summary_to_ai_conversation(
    *,
    task_id: int,
    ai_conversation_id: int | None,
    trace_events: list[str],
) -> None:
    """Persist this review into the task's AI chat as two rows:

    1. an ``INTERNAL`` row carrying the reasoning trace (rendered by
       ``ChatMessage.tsx`` as a compact ThinkingChain);
    2. an ``ACTION`` row whose ``meta.review_card`` carries the structured
       status / summary / next step (rendered as a distinct status card,
       not a chat reply).

    Skips silently when the task has no AI conversation (some legacy
    tasks don't). Re-reads review fields from a fresh Task row so we pick
    up whatever ``record_task_review`` just wrote.
    """
    if ai_conversation_id is None:
        return
    from db.models import (
        Message,
        MessageType,
        ParticipantType,
        Task as TaskModel,
    )
    from db.session import SessionLocal
    from services.chat_service import dump_message_meta

    db = SessionLocal()
    try:
        fresh = db.query(TaskModel).filter_by(id=task_id).first()
        if fresh is None:
            return

        now = datetime.now(UTC)

        # 1. Reasoning trace as INTERNAL — chat UI renders ThinkingChain.
        trace_lines = [line for line in trace_events if line]
        if trace_lines:
            db.add(Message(
                conversation_id=ai_conversation_id,
                sender_type=ParticipantType.ACCOUNT_USER,
                body="\n".join(trace_lines),
                message_type=MessageType.INTERNAL,
                sender_name="RentMate",
                is_ai=True,
                sent_at=now,
            ))
            db.flush()

        # 2. Status update as ACTION + meta.review_card. The bare summary
        # text goes in body so legacy/screen-reader paths still have
        # something readable; the bubble renders from review_card.
        raw_status = (fresh.last_review_status or "recorded")
        status = raw_status if raw_status in _REVIEW_CARD_STATUSES else "recorded"
        summary_text = (fresh.last_review_summary or "").strip() or None
        next_step_text = (fresh.last_review_next_step or "").strip() or None
        review_card = {"status": status}
        if summary_text:
            review_card["summary"] = summary_text
        if next_step_text:
            review_card["next_step"] = next_step_text

        db.add(Message(
            conversation_id=ai_conversation_id,
            sender_type=ParticipantType.ACCOUNT_USER,
            body=summary_text or "",
            message_type=MessageType.ACTION,
            sender_name="RentMate",
            is_ai=True,
            meta=dump_message_meta(review_card=review_card),
            sent_at=now,
        ))
        db.commit()
    finally:
        db.close()


def _task_review_trace_detail(
    *,
    session_key: str,
    task_id: str,
    prompt: str,
    context_data: dict[str, Any],
    reply: str | None = None,
) -> dict:
    """Build the llm_request / llm_exchange trace envelope for a review.

    Matches the shape produced by ``handlers/routines.py::_routine_trace_detail``
    so the trace UI surfaces context + retrieval for task reviews exactly
    the way it does for routine runs.
    """
    from agent.tracing import make_trace_envelope

    context_text = context_data.get("text") or ""
    context_sections = context_data.get("sections") or []
    retrieval = context_data.get("retrieval")
    messages_payload = [
        {"role": "system", "content": context_text},
        {"role": "user", "content": prompt},
    ]
    return make_trace_envelope(
        "llm_exchange" if reply is not None else "llm_request",
        flow="task_review",
        session_key=session_key,
        task_id=task_id,
        conversation_id=None,
        messages_payload=messages_payload,
        messages_breakdown={
            "system": messages_payload[0],
            "history": [],
            "latest_user": messages_payload[1],
        },
        context={"text": context_text, "sections": context_sections},
        retrieval=retrieval,
        reply=reply,
        side_effects=[],
        reasoning={
            "available": False,
            "note": "No provider reasoning trace available for this response.",
        },
    )


def _review_timestamp_is_fresh(reviewed_at: datetime | None, started_at: datetime) -> bool:
    if reviewed_at is None:
        return False
    if reviewed_at.tzinfo is None:
        reviewed_at = reviewed_at.replace(tzinfo=UTC)
    return reviewed_at >= started_at


def _ensure_review_recorded(
    *,
    task_id: int,
    review_started_at: datetime,
    trace_context: dict[str, Any],
) -> None:
    from agent.tools.task_review import record_task_review_result
    from db.models import Task as TaskModel
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        fresh = db.query(TaskModel).filter_by(id=task_id).first()
        if (
            fresh is not None
            and fresh.last_review_status
            and _review_timestamp_is_fresh(fresh.last_reviewed_at, review_started_at)
        ):
            return
    finally:
        db.close()

    summary = (
        "The task review agent completed a run but did not record a "
        "structured review decision."
    )
    next_step = "Inspect the agent run trace and rerun the task review."
    record_task_review_result(
        task_id=str(task_id),
        status="blocked",
        summary=summary,
        next_step=next_step,
        trace_context=trace_context,
    )


async def _review_one_task(
    task,
    *,
    on_progress=None,
) -> None:
    """Run one agent review for a single task.

    Persists the structured decision via ``record_task_review`` (tool side),
    captures the agent's reasoning events via ``on_progress``, and writes a
    compact summary message into the task's AI conversation so the manager
    can see what the agent did right alongside the rest of the task chat.

    ``on_progress`` is an optional async callback that receives each
    reasoning event as it fires (used by the SSE endpoint to relay live
    progress to the frontend).
    """
    from agent.client import call_agent
    from agent.context import build_task_context_data
    from agent.registry import agent_registry
    from agent.tracing import log_trace
    from integrations.local_auth import reset_request_context, set_request_context

    creator_id = task.creator_id
    org_id = getattr(task, "org_id", None)
    ai_conversation_id = getattr(task, "ai_conversation_id", None)
    trace_events: list[str] = []

    async def _capture_progress(text: str, **_kwargs):
        if not text:
            return
        trace_events.append(text)
        if on_progress is not None:
            try:
                await on_progress(text)
            except Exception:
                logger.exception("task_review external on_progress failed")

    tokens = set_request_context(account_id=creator_id, org_id=org_id)
    try:
        from db.session import SessionLocal
        db = SessionLocal()
        try:
            prompt = _build_review_prompt(task, db)
            agent_id = agent_registry.ensure_agent(creator_id, db)
            context_data = build_task_context_data(db, str(task.id), query=prompt)
        finally:
            db.close()

        context_text = context_data.get("text") or ""
        session_key = f"task_review:{task.id}"
        review_started_at = datetime.now(UTC)
        trace_detail = _task_review_trace_detail(
            session_key=session_key,
            task_id=str(task.id),
            prompt=prompt,
            context_data=context_data,
        )
        from agent.runs import derive_run_metadata, start_run
        with start_run(
            **derive_run_metadata(
                session_key=session_key,
                task_id=str(task.id),
                source_override="task_review",
            ),
            trigger_input=prompt,
        ):
            log_trace(
                "llm_request",
                "task_review",
                f"Prepared review for task {task.id}",
                detail=trace_detail,
            )
            messages = [
                {"role": "system", "content": context_text},
                {"role": "user", "content": prompt},
            ]
            resp = await call_agent(
                agent_id,
                session_key=session_key,
                messages=messages,
                on_progress=_capture_progress,
                trace_context=trace_detail,
            )
            log_trace(
                "llm_reply",
                "task_review",
                (resp.reply or "")[:200],
                detail=_task_review_trace_detail(
                    session_key=session_key,
                    task_id=str(task.id),
                    prompt=prompt,
                    context_data=context_data,
                    reply=resp.reply,
                ),
            )
            _ensure_review_recorded(
                task_id=task.id,
                review_started_at=review_started_at,
                trace_context=trace_detail,
            )
            _persist_review_summary_to_ai_conversation(
                task_id=task.id,
                ai_conversation_id=ai_conversation_id,
                trace_events=trace_events,
            )
    finally:
        reset_request_context(tokens)


async def _task_review_tick() -> int:
    """Review every due task once. Returns the number of tasks reviewed."""
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        due = _select_due_tasks(db)
        # Detach so the later per-task request_context swap can re-query on
        # its own session without stale-attribute issues.
        for task in due:
            db.expunge(task)
    finally:
        db.close()

    reviewed = 0
    for task in due:
        try:
            await _review_one_task(task)
            reviewed += 1
        except Exception:
            logger.exception("task_review failed for task %s", getattr(task, "id", "?"))
    return reviewed


def _load_task_for_manual_review(task_id: int):
    """Fetch + detach a task for a manual trigger. 404 when not found."""
    from db.models import Task
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if task is None:
            return None
        db.expunge(task)
        return task
    finally:
        db.close()


@router.post("/tasks/{task_id}/review")
async def trigger_task_review(task_id: int, request: Request):
    """On-demand review trigger — runs the same review the loop runs and
    streams reasoning progress back as Server-Sent Events.

    The SSE envelope matches the existing chat-stream shape so the
    frontend's progress-log renderer can consume both interchangeably:

      data: {"type": "progress", "text": "..."}
      data: {"type": "done"}
      data: {"type": "error", "message": "..."}
    """
    await require_user(request)
    task = _load_task_for_manual_review(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_source():
        queue: asyncio.Queue = asyncio.Queue()

        async def _forward_progress(text: str):
            await queue.put(("progress", text))

        async def _run():
            try:
                await _review_one_task(
                    task, on_progress=_forward_progress,
                )
                await queue.put(("done", None))
            except Exception as exc:
                logger.exception("manual review for task %s failed", task_id)
                await queue.put(("error", str(exc)[:500]))

        runner = asyncio.create_task(_run())
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "progress":
                    yield f"data: {json.dumps({'type': 'progress', 'text': payload})}\n\n"
                elif kind == "done":
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                elif kind == "error":
                    yield f"data: {json.dumps({'type': 'error', 'message': payload})}\n\n"
                    break
        finally:
            # Ensure the background task finishes even if the client disconnects.
            try:
                await runner
            except Exception:
                pass

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def task_review_loop() -> None:
    """Background loop — fires on startup, then every ``_TASK_REVIEW_POLL_SECONDS``."""
    logger.info(
        "task_review loop started (poll=%ds, cooldown=%ss)",
        _TASK_REVIEW_POLL_SECONDS, int(_TASK_REVIEW_COOLDOWN.total_seconds()),
    )
    # Let the rest of the app (routine_loop, reply_scanner, etc.) settle
    # before our first sweep so we don't pile on at boot.
    await asyncio.sleep(15)
    while True:
        try:
            count = await _task_review_tick()
            if count:
                logger.info("task_review tick: reviewed %d task(s)", count)
        except Exception:
            logger.exception("task_review tick crashed")
        await asyncio.sleep(_TASK_REVIEW_POLL_SECONDS)
