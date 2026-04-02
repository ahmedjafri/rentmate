"""Nanobot tool classes that create Suggestions in the database.

Each tool maps an agent action to a Suggestion row so the manager can
approve or dismiss it via the action desk UI.

When a tool creates a suggestion during a chat, it also posts an APPROVAL
message to the originating conversation so the suggestion appears inline.
The conversation_id is communicated via the ``active_conversation_id``
context variable, set by the chat handler before the agent runs.
"""
import contextvars
import json
from typing import Any

from nanobot.agent.tools.base import Tool

from db.enums import AgentSource, SuggestionOption, TaskCategory, Urgency
from db.models import MessageType

# Set by the chat handler before calling the agent so tools can link
# suggestions back to the originating conversation.
active_conversation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_conversation_id", default=None,
)


def _create_suggestion(
    *,
    title: str,
    ai_context: str,
    category: str | None = None,
    urgency: str | None = None,
    action_payload: dict | None = None,
    options: list[SuggestionOption],
    task_id: str | None = None,
    property_id: str | None = None,
) -> str:
    """Write a Suggestion row and return its ID.

    If ``active_conversation_id`` is set, also adds an APPROVAL message to
    that conversation so the suggestion appears inline in the chat.
    """
    from handlers.deps import SessionLocal
    from gql.services import suggestion_service, chat_service

    db = SessionLocal()
    try:
        suggestion = suggestion_service.create_suggestion(
            db,
            title=title,
            ai_context=ai_context,
            category=category,
            urgency=urgency,
            source=AgentSource(),
            options=options,
            action_payload=action_payload,
            property_id=property_id,
        )
        if task_id:
            suggestion.task_id = task_id

        # Post an inline message to the originating conversation
        conv_id = active_conversation_id.get()
        if conv_id:
            chat_service.send_message(
                db, conv_id,
                body=title,
                message_type=MessageType.APPROVAL,
                sender_name="RentMate",
                is_ai=True,
            )

        db.commit()
        return suggestion.id
    finally:
        db.close()


def _get_task_title(task_id: str) -> str:
    """Look up a task's title for use in suggestion headlines."""
    from handlers.deps import SessionLocal
    from db.models import Task

    db = SessionLocal()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        return task.title if task else task_id
    finally:
        db.close()


class ProposeTaskTool(Tool):
    """Create a task proposal for manager review."""

    @property
    def name(self) -> str:
        return "propose_task"

    @property
    def description(self) -> str:
        return (
            "Propose a new task for the property manager to review. "
            "The proposal appears in the action desk for approval."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["title", "category"],
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "category": {
                    "type": "string",
                    "enum": [c.value for c in TaskCategory],
                    "description": "Task category",
                },
                "urgency": {
                    "type": "string",
                    "enum": [u.value for u in Urgency],
                    "description": "Urgency level (default: medium)",
                },
                "description": {"type": "string", "description": "Detailed context for the task"},
                "property_id": {"type": "string", "description": "Property ID (if applicable)"},
                "task_id": {"type": "string", "description": "Originating task ID (if applicable)"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        options = [
            SuggestionOption(key="accept", label="Create Task", action="accept_task", variant="default"),
            SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
        ]
        sid = _create_suggestion(
            title=kwargs["title"],
            ai_context=kwargs.get("description") or kwargs["title"],
            category=kwargs["category"],
            urgency=kwargs.get("urgency", Urgency.MEDIUM.value),
            options=options,
            task_id=kwargs.get("task_id"),
            property_id=kwargs.get("property_id"),
        )
        return json.dumps({"status": "ok", "suggestion_id": sid, "message": f"Task proposal '{kwargs['title']}' created for manager review."})


class CloseTaskTool(Tool):
    """Propose closing a task for manager confirmation."""

    @property
    def name(self) -> str:
        return "close_task"

    @property
    def description(self) -> str:
        return (
            "Propose closing a task. The manager will see a confirmation "
            "in the action desk before the task is actually closed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to close"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        task_title = _get_task_title(task_id)
        options = [
            SuggestionOption(key="close", label="Close Task", action="close_task", variant="default"),
            SuggestionOption(key="keep", label="Keep Open", action="reject_task", variant="ghost"),
        ]
        sid = _create_suggestion(
            title=f"Close task: {task_title}",
            ai_context="The agent recommends closing this task.",
            options=options,
            action_payload={"action": "close_task"},
            task_id=task_id,
        )
        return json.dumps({"status": "ok", "suggestion_id": sid, "message": "Close request created for manager confirmation."})


class SetModeTool(Tool):
    """Propose changing a task's operating mode."""

    @property
    def name(self) -> str:
        return "set_mode"

    @property
    def description(self) -> str:
        return (
            "Propose changing a task's operating mode (autonomous, manual, "
            "or waiting_approval). The manager must approve the change."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "mode"],
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task"},
                "mode": {
                    "type": "string",
                    "enum": ["autonomous", "manual", "waiting_approval"],
                    "description": "New operating mode",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        mode = kwargs["mode"]
        task_title = _get_task_title(task_id)
        options = [
            SuggestionOption(key="approve", label=f"Switch to {mode}", action="set_mode", variant="default"),
            SuggestionOption(key="reject", label="Keep Current", action="reject_task", variant="ghost"),
        ]
        sid = _create_suggestion(
            title=f"Change mode to {mode}: {task_title}",
            ai_context=f"The agent recommends changing this task's mode to '{mode}'.",
            options=options,
            action_payload={"action": "set_mode", "mode": mode},
            task_id=task_id,
        )
        return json.dumps({"status": "ok", "suggestion_id": sid, "message": f"Mode change to '{mode}' created for manager confirmation."})
