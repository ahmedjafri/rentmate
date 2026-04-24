"""Task-review tools — record_task_review + ask_manager."""
import json
from datetime import UTC, datetime
from typing import Any

from llm.tools._common import Tool, tool_session


_VALID_STATUSES: frozenset[str] = frozenset({
    "on_track", "needs_action", "blocked", "waiting",
})


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
        task_id = str(kwargs.get("task_id") or "").strip()
        status = (kwargs.get("status") or "").strip().lower()
        summary = (kwargs.get("summary") or "").strip()
        next_step = (kwargs.get("next_step") or "").strip() or None

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

        from db.models import Task as TaskModel

        now = datetime.now(UTC)
        with tool_session() as db:
            task = db.query(TaskModel).filter_by(id=task_id).first()
            if not task:
                return json.dumps({
                    "status": "error",
                    "message": f"Task {task_id} not found",
                })
            task.last_reviewed_at = now
            task.last_review_status = status
            task.last_review_summary = summary
            task.last_review_next_step = next_step

        # Log a mirror AgentTrace row for history. log_trace runs in its own
        # savepoint and never raises, so a trace failure won't undo the
        # column update above.
        from llm.tracing import log_trace
        log_trace(
            "task_review",
            "task_review",
            summary[:500],
            task_id=task_id,
            tool_name="record_task_review",
            detail={
                "status": status,
                "summary": summary,
                "next_step": next_step,
            },
        )

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
            "conversation. Use this when you need clarification, a "
            "decision, or an approval to unblock the task. The message "
            "appears in the manager's task chat; they reply there."
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
        task_id = str(kwargs.get("task_id") or "").strip()
        question = (kwargs.get("question") or "").strip()

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

        from db.models import Task as TaskModel
        from gql.services import chat_service

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
            msg = chat_service.send_autonomous_message(
                db,
                conversation_id=task.ai_conversation_id,
                body=question,
                task_id=int(task_id),
            )
            return json.dumps({
                "status": "ok",
                "task_id": task_id,
                "conversation_id": str(task.ai_conversation_id),
                "message_id": str(msg.id),
            })


__all__ = ["AskManagerTool", "RecordTaskReviewTool"]
