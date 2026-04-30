"""Task-review tools — record_task_review + ask_manager."""
import json
import logging
from datetime import UTC, datetime
from typing import Any

from llm.tools._common import (
    Tool,
    _check_placeholder_ids,
    _resolve_task_id_from_active_conversation,
    current_request_context,
    tool_session,
)

logger = logging.getLogger(__name__)


_VALID_STATUSES: frozenset[str] = frozenset({
    "on_track", "needs_action", "blocked", "waiting",
})


def record_task_review_result(
    *,
    task_id: str,
    status: str,
    summary: str,
    next_step: str | None = None,
    trace_context: dict[str, Any] | None = None,
) -> datetime | None:
    """Persist review columns and log the matching trace row.

    The tool and the task-review loop fallback share this path so every
    persisted review captures the same server-side run context.
    """
    from db.models import Task as TaskModel

    now = datetime.now(UTC)
    with tool_session() as db:
        task = db.query(TaskModel).filter_by(id=task_id).first()
        if not task:
            return None
        task.last_reviewed_at = now
        task.last_review_status = status
        task.last_review_summary = summary
        task.last_review_next_step = next_step

    detail: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "next_step": next_step,
    }
    run_context = (
        trace_context if trace_context is not None else current_request_context.get()
    )
    if run_context is not None:
        detail["trace_context"] = run_context

    # Log a mirror AgentTrace row for history. log_trace runs in its own
    # savepoint and never raises, so a trace failure won't undo the
    # column update above.
    from llm.tracing import log_trace
    log_trace(
        "task_review",
        "task_review",
        summary[:500],
        tool_name="record_task_review",
        detail=detail,
    )
    return now


class RecordTaskReviewTool(Tool):
    """Persist the agent's structured review of a task.

    The task_review loop drives the agent per active task and asks it to
    call this tool exactly once with its decision. The fields land on the
    Task row (for fast rendering in the right panel of the task detail
    page) and a mirror ``AgentTrace`` row is logged for history.
    """

    @property
    def name(self) -> str:
        return "record_task_review"

    @property
    def description(self) -> str:
        return (
            "Record your structured review of a task. Call this exactly once "
            "per review, at the end, after you've examined the task's goal, "
            "steps, context, and recent activity. "
            "status must be one of: on_track, needs_action, blocked, waiting. "
            "Use on_track when the task is progressing normally; needs_action "
            "when a concrete follow-up is required; blocked when external "
            "input is missing; waiting when the ball is legitimately in "
            "someone else's court and patience is the right move."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "status", "summary"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID of the task being reviewed.",
                },
                "status": {
                    "type": "string",
                    "enum": sorted(_VALID_STATUSES),
                    "description": (
                        "Current high-level state of the task relative to its goal."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "1–2 sentence summary of where the task stands today.",
                },
                "next_step": {
                    "type": "string",
                    "description": (
                        "What should happen next — a single actionable sentence. "
                        "Omit if no action is needed yet."
                    ),
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        err = _check_placeholder_ids(kwargs, [("task_id", "list_tasks")])
        if err:
            return err
        agent_supplied_task_id = str(kwargs.get("task_id") or "").strip() or None
        task_id = agent_supplied_task_id or ""
        status = (kwargs.get("status") or "").strip().lower()
        summary = (kwargs.get("summary") or "").strip()
        next_step = (kwargs.get("next_step") or "").strip() or None

        # Active conversation is ground truth: a review running inside a
        # task's AI conversation belongs to that task. Override any
        # agent-supplied task_id (frequently a hallucination from a
        # context window with multiple task ids).
        active_task_id = _resolve_task_id_from_active_conversation()
        if active_task_id is not None:
            if (
                agent_supplied_task_id is not None
                and str(agent_supplied_task_id) != str(active_task_id)
            ):
                logger.warning(
                    "record_task_review task_id override: agent passed %s "
                    "but active conversation belongs to task %s — using "
                    "active task",
                    agent_supplied_task_id, active_task_id,
                )
            task_id = active_task_id

        if not task_id:
            return json.dumps({"status": "error", "message": "task_id is required"})
        try:
            int(task_id)
        except (TypeError, ValueError):
            return json.dumps({
                "status": "error",
                "message": (
                    f"task_id must be the numeric id of a real task; got {task_id!r}."
                ),
            })
        if status not in _VALID_STATUSES:
            return json.dumps({
                "status": "error",
                "message": (
                    f"status must be one of {sorted(_VALID_STATUSES)}; got {status!r}."
                ),
            })
        if not summary:
            return json.dumps({"status": "error", "message": "summary is required"})

        now = record_task_review_result(
            task_id=task_id,
            status=status,
            summary=summary,
            next_step=next_step,
        )
        if now is None:
            return json.dumps({
                "status": "error",
                "message": f"Task {task_id} not found",
            })

        return json.dumps({
            "status": "ok",
            "task_id": task_id,
            "review_status": status,
            "recorded_at": now.isoformat(),
        })


class AskManagerTool(Tool):
    """Post a question from the agent to the property manager on a task's
    AI conversation.

    Use this when you need clarification, a decision, or an approval from
    the manager that you can't resolve yourself. The message lands in the
    task's AI chat as an AI-authored message, which the manager sees in
    the task detail center column just like any other agent message.
    """

    @property
    def name(self) -> str:
        return "ask_manager"

    @property
    def description(self) -> str:
        return (
            "Post a question to the property manager in the task's AI "
            "conversation. Use this whenever you need clarification, a "
            "decision, or an approval to unblock yourself — including "
            "when you can't complete the request because info is "
            "missing, an entity isn't in lookup results, you don't have "
            "a tool for the action, or the manager's intent is "
            "ambiguous. **This is the right escape hatch for "
            "blockers — do NOT call ``propose_task`` or "
            "``create_suggestion`` to ask the manager to do something "
            "themselves.** The message appears in the manager's task "
            "chat; they reply there."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "question"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID of the task to ask about.",
                },
                "question": {
                    "type": "string",
                    "description": (
                        "The question to post to the manager. Be specific "
                        "and include any context the manager needs to "
                        "answer without re-reading the whole task."
                    ),
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        err = _check_placeholder_ids(kwargs, [("task_id", "list_tasks")])
        if err:
            return err
        agent_supplied_task_id = str(kwargs.get("task_id") or "").strip() or None
        task_id = agent_supplied_task_id or ""
        question = (kwargs.get("question") or "").strip()

        # Active conversation is ground truth: posting from inside a
        # task's AI conversation means the question belongs to that
        # task. Override any hallucinated task_id and rescue agents
        # that omit it entirely.
        active_task_id = _resolve_task_id_from_active_conversation()
        if active_task_id is not None:
            if (
                agent_supplied_task_id is not None
                and str(agent_supplied_task_id) != str(active_task_id)
            ):
                logger.warning(
                    "ask_manager task_id override: agent passed %s but "
                    "active conversation belongs to task %s — using "
                    "active task",
                    agent_supplied_task_id, active_task_id,
                )
            task_id = active_task_id

        if not task_id:
            return json.dumps({"status": "error", "message": "task_id is required"})
        try:
            int(task_id)
        except (TypeError, ValueError):
            return json.dumps({
                "status": "error",
                "message": (
                    f"task_id must be the numeric id of a real task; got {task_id!r}."
                ),
            })
        if not question:
            return json.dumps({
                "status": "error",
                "message": "question is required and must not be empty",
            })

        from sqlalchemy.orm.attributes import flag_modified

        from db.models import (
            Conversation,
            Message,
            MessageType,
            ParticipantType,
            Task as TaskModel,
        )
        from gql.services.chat_service import dump_message_meta
        from gql.services.notification_service import NotificationRequest, NotificationService

        with tool_session() as db:
            task = db.query(TaskModel).filter_by(id=task_id).first()
            if not task:
                return json.dumps({
                    "status": "error",
                    "message": f"Task {task_id} not found",
                })
            if not task.ai_conversation_id:
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Task {task_id} has no AI conversation to post into."
                    ),
                })

            now = datetime.now(UTC)
            # Write the question as an action-card message so the chat UI
            # routes it through ActionCardBubble and renders the question
            # kind with an inline reply form.
            msg = Message(
                conversation_id=task.ai_conversation_id,
                sender_type=ParticipantType.ACCOUNT_USER,
                body=question,
                message_type=MessageType.ACTION,
                sender_name="RentMate",
                is_ai=True,
                sent_at=now,
                meta=dump_message_meta(action_card={
                    "kind": "question",
                    "title": question,
                }),
            )
            db.add(msg)

            convo = db.query(Conversation).filter_by(id=task.ai_conversation_id).first()
            if convo is not None:
                convo.updated_at = now
                if hasattr(convo, "extra"):
                    flag_modified(convo, "extra")

            db.flush()

            NotificationService.create(
                db,
                NotificationRequest(
                    recipient_user_id=task.creator_id,
                    task_id=int(task_id),
                    conversation_id=task.ai_conversation_id,
                    kind="manager_attention",
                    channel="in_app",
                    title=f"Task needs your input: {task.title or f'Task #{task.id}'}",
                    body=question,
                ),
            )
            return json.dumps({
                "status": "ok",
                "task_id": task_id,
                "conversation_id": str(task.ai_conversation_id),
                "message_id": str(msg.id),
                "action_card_kind": "question",
            })


__all__ = ["AskManagerTool", "RecordTaskReviewTool"]
