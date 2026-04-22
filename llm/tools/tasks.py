"""Task-lifecycle tools: propose, close, generic suggestion, scheduled task."""
import json
import re
from typing import Any

from db.enums import SuggestionOption, TaskCategory, TaskStatus, Urgency
from gql.services.task_service import dump_task_steps

from llm.tools._common import (
    Tool,
    _create_suggestion,
    _load_vendor_by_public_id,
    _queue_simulation_suggestion,
    _recent_user_messages,
    _resolve_task_id_from_active_conversation,
    _sanitize_tenant_outbound_draft,
    current_user_message,
)


_DIRECT_DRAFT_NEGATIVE_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bdon't create (a )?suggestion\b",
        r"\bdont create (a )?suggestion\b",
        r"\bdo not create (a )?suggestion\b",
        r"\bdon't create (a )?task\b",
        r"\bdont create (a )?task\b",
        r"\bdo not create (a )?task\b",
        r"\bnot a suggestion\b",
        r"\bnot a task\b",
    ]
]

_DIRECT_DRAFT_REQUEST_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bcreate (the )?draft\b",
        r"\bdraft (the )?(notice|letter|document)\b",
        r"\bdraft it directly\b",
        r"\bcreate it directly\b",
        r"\bdirectly\b.*\bdraft\b",
    ]
]


_SUGGESTION_CONFIRM_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bcreate (a )?suggestion\b",
        r"\bgo ahead\b",
        r"\byes\b",
        r"\bdo that\b",
        r"\badd it\b",
        r"\baction desk\b",
        r"\bupload\b",
        r"\bnotice\b",
    ]
]


def _has_user_confirmed_upload_request(task_id: str) -> bool:
    current_message = current_user_message.get()
    if current_message and any(pattern.search(current_message) for pattern in _SUGGESTION_CONFIRM_PATTERNS):
        return True
    for message in _recent_user_messages(task_id):
        if any(pattern.search(message) for pattern in _SUGGESTION_CONFIRM_PATTERNS):
            return True
    return False


def _normalize_current_task_suggestion_payload(
    *,
    task_id: str,
    title: str,
    body: str,
    action_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(action_payload or {})
    if payload.get("action"):
        return payload

    combined = f"{title}\n{body}".lower()
    if not any(keyword in combined for keyword in ["upload", "notice", "signed document", "draft 14-day", "pay or vacate"]):
        return payload

    requested_file_label = title.strip()
    for prefix in ("Draft ", "Upload "):
        if requested_file_label.startswith(prefix):
            requested_file_label = requested_file_label[len(prefix):].strip()

    requested_file_kind = "notice" if "notice" in combined else "document"
    instructions = (
        f"Upload the completed {requested_file_label} so the current task can continue."
    )
    if requested_file_kind == "notice" and "14-day" in combined:
        instructions = (
            f"Upload the completed {requested_file_label} for service on the tenant so the current task can continue."
        )

    payload.update({
        "action": "request_file_upload",
        "requested_file_kind": requested_file_kind,
        "requested_file_label": requested_file_label,
        "instructions": instructions,
        "target_task_id": task_id,
    })
    return payload


def _sanitize_tenant_message_person_payload(
    *,
    task_id: str,
    action_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(action_payload or {})
    if payload.get("action") != "message_person" or payload.get("entity_type") != "tenant":
        return payload

    draft = str(payload.get("draft_message") or "")
    if not draft:
        return payload

    from handlers.deps import SessionLocal

    db = SessionLocal()
    try:
        payload["draft_message"] = _sanitize_tenant_outbound_draft(
            db,
            task_id=task_id,
            draft_message=draft,
        )
        return payload
    finally:
        db.close()


def _mark_task_waiting_on_upload_request(
    *,
    task_id: str,
    requested_file_label: str,
    instructions: str,
) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from db.enums import TaskMode
    from db.models import Task
    from llm.tools._common import tool_session

    with tool_session() as db:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task:
            return

        steps = list(task.steps or [])
        blocked_note = f"Blocked until {requested_file_label} is uploaded."
        updated = False
        for step in steps:
            key = str(step.get("key") or "")
            label = str(step.get("label") or "")
            if key == "upload_requested_file" or requested_file_label.lower() in label.lower():
                step["status"] = "pending"
                step["note"] = blocked_note
                updated = True
                break
        if not updated:
            steps.append({
                "key": "upload_requested_file",
                "label": f"Upload {requested_file_label}",
                "status": "pending",
                "note": blocked_note,
            })

        task.steps = dump_task_steps(steps)
        flag_modified(task, "steps")
        task.task_mode = TaskMode.WAITING_APPROVAL

        context_parts = [part for part in [task.context, f"Blocked on user deliverable: {instructions}"] if part]
        task.context = "\n\n".join(dict.fromkeys(context_parts))


def _current_task_notice_service_reported(task_id: str) -> bool:
    current_message = (current_user_message.get() or "").lower()
    if not current_message:
        return False
    mentions_notice = any(term in current_message for term in ["14-day", "pay or vacate", "notice"])
    mentions_completion = any(term in current_message for term in ["uploaded", "served", "mailed", "posted", "certified mail"])
    if not (mentions_notice and mentions_completion):
        return False

    from db.models import Suggestion
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        pending = (
            db.query(Suggestion)
            .filter(
                Suggestion.task_id == task_id,
                Suggestion.status == "pending",
            )
            .all()
        )
        return any((s.action_payload or {}).get("action") == "request_file_upload" for s in pending)
    finally:
        db.close()


def _same_task_handoff_block_message() -> str:
    return (
        "Do not create a new suggestion or task for this. Stay in the current task, "
        "acknowledge that the notice was uploaded and served, tell the manager to document "
        "the service date and method, and explain that the next step is to wait out the 14-day notice period."
    )


def _current_message_requests_direct_draft() -> bool:
    current_message = (current_user_message.get() or "").strip()
    if not current_message:
        return False
    has_negative = any(pattern.search(current_message) for pattern in _DIRECT_DRAFT_NEGATIVE_PATTERNS)
    wants_draft = any(pattern.search(current_message) for pattern in _DIRECT_DRAFT_REQUEST_PATTERNS)
    return has_negative and wants_draft


def _direct_draft_block_message() -> str:
    return (
        "Do not create a suggestion or task for this. The user asked for the draft itself. "
        "Draft the requested notice or document directly in the chat response."
    )


class ProposeTaskTool(Tool):
    """Create a task proposal for manager review."""

    @property
    def name(self) -> str:
        return "propose_task"

    @property
    def description(self) -> str:
        return (
            "Propose a new task for a genuinely separate issue. "
            "Only use propose_task for a genuinely separate issue that needs its own task. "
            "Never use propose_task when the user asked you to draft a notice, letter, or document directly in chat. "
            "You MUST provide a vendor_id external UUID — use lookup_vendors first. "
            "Include a draft_message and steps (3-6 steps)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["title", "category", "vendor_id"],
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
                "vendor_id": {"type": "string", "description": "External UUID of the vendor to assign (use lookup_vendors to find this)"},
                "draft_message": {"type": "string", "description": "Draft message to send to the vendor on approval"},
                "property_id": {"type": "string", "description": "Property ID (if applicable)"},
                "task_id": {"type": "string", "description": "Originating task ID (if applicable)"},
                "risk_score": {
                    "type": "integer",
                    "description": "0-10: risk of auto-approving. 0=safe, 10=must review. Default 5.",
                },
                "steps": {
                    "type": "array",
                    "description": "Ordered progress plan for the task (3-6 steps). Each step has key, label, and status.",
                    "items": {
                        "type": "object",
                        "required": ["key", "label", "status"],
                        "properties": {
                            "key": {"type": "string", "description": "Short unique key (e.g. 'find_vendor')"},
                            "label": {"type": "string", "description": "Human-readable step label"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "active", "done"],
                                "description": "Step status",
                            },
                            "note": {"type": "string", "description": "Optional context note"},
                        },
                    },
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = str(kwargs.get("task_id") or _resolve_task_id_from_active_conversation() or "")
        if _current_message_requests_direct_draft():
            return json.dumps({"status": "error", "message": _direct_draft_block_message()})
        if task_id and _current_task_notice_service_reported(task_id):
            return json.dumps({"status": "error", "message": _same_task_handoff_block_message()})

        vendor_id = str(kwargs["vendor_id"])

        from db.session import SessionLocal
        db = SessionLocal.session_factory()
        try:
            vendor = _load_vendor_by_public_id(db, vendor_id)
            vendor_name = vendor.name if vendor else "Vendor"
        finally:
            db.close()

        action_payload: dict = {
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
        }
        draft_message = kwargs.get("draft_message")
        if draft_message:
            action_payload["draft_message"] = draft_message
        steps = kwargs.get("steps")
        if steps:
            action_payload["steps"] = dump_task_steps(steps)

        if draft_message:
            options = [
                SuggestionOption(key="send", label=f"Send to {vendor_name}", action="send_and_create_task", variant="default"),
                SuggestionOption(key="edit", label="Edit Message", action="edit_message", variant="outline"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]
        else:
            options = [
                SuggestionOption(key="send", label=f"Assign {vendor_name}", action="send_and_create_task", variant="default"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]

        # External contact messages are at least risk 4
        risk = kwargs.get("risk_score", 3)
        if draft_message and risk < 4:
            risk = 4

        sid = _create_suggestion(
            title=kwargs["title"],
            ai_context=kwargs.get("description") or kwargs["title"],
            category=kwargs["category"],
            urgency=kwargs.get("urgency", Urgency.MEDIUM.value),
            action_payload=action_payload,
            options=options,
            task_id=kwargs.get("task_id") or task_id or None,
            property_id=kwargs.get("property_id"),
            risk_score=risk,
            suggestion_type=kwargs["category"],
        )
        return json.dumps({"status": "ok", "suggestion_id": sid, "message": f"Task proposal '{kwargs['title']}' with {vendor_name} created for manager review."})


class CloseTaskTool(Tool):
    """Resolve a task when all work is complete."""

    @property
    def name(self) -> str:
        return "close_task"

    @property
    def description(self) -> str:
        return (
            "Resolve a task. Only works if all progress steps are marked done "
            "(or the task has no steps). The task is archived as resolved, not deleted."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to resolve"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]

        from datetime import UTC, datetime

        from db.models import Task as TaskModel
        from llm.tools._common import tool_session
        with tool_session() as db:
            task = db.query(TaskModel).filter_by(id=task_id).first()
            if not task:
                return json.dumps({"status": "error", "message": f"Task {task_id} not found"})

            # Enforce: all progress steps must be done before closing
            steps = task.steps or []
            incomplete = [
                s.get("label", "unnamed step")
                for s in steps
                if isinstance(s, dict) and s.get("status") not in ("done", "completed")
            ]
            if incomplete:
                return json.dumps({
                    "status": "error",
                    "message": f"Cannot close task — {len(incomplete)} step(s) still incomplete: {', '.join(incomplete)}. "
                               "Complete all steps before closing.",
                })

            task.task_status = TaskStatus.RESOLVED
            if not task.resolved_at:
                task.resolved_at = datetime.now(UTC)
            return json.dumps({"status": "ok", "message": "Task resolved."})


class CreateSuggestionTool(Tool):
    """Create a suggestion for the property manager to review."""

    @property
    def name(self) -> str:
        return "create_suggestion"

    @property
    def description(self) -> str:
        return (
            "Create a suggestion for the property manager to review and approve. "
            "Use this for actions that benefit from human review — creating entities "
            "from documents, proposing lease changes, compliance actions, etc. "
            "Never use create_suggestion when the user explicitly asked for the draft itself in chat instead of a suggestion or task. "
            "When the blocker is a user deliverable inside the current task, ask the user first "
            "whether they want a suggestion created. "
            "Set risk_score: 0 = safe to auto-approve, 10 = must have human review. "
            "Low-risk routine actions (creating a property from a clear document) can "
            "be 1-3. High-risk actions (legal notices, deposit deductions) should be 7-10."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["title", "body", "suggestion_type", "risk_score"],
            "properties": {
                "title": {"type": "string", "description": "Short title for the suggestion"},
                "body": {"type": "string", "description": "Detailed context and reasoning"},
                "suggestion_type": {
                    "type": "string",
                    "enum": ["rent", "maintenance", "leasing", "compliance"],
                    "description": "Category — maps to autonomy level for approval routing",
                },
                "risk_score": {
                    "type": "integer",
                    "description": "0-10: risk of auto-approving. 0=safe, 10=must review",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "How urgent is this suggestion (default: medium)",
                },
                "property_id": {"type": "string", "description": "Link to a property"},
                "unit_id": {"type": "string", "description": "Link to a unit"},
                "document_id": {"type": "string", "description": "Link to a source document"},
                "task_id": {"type": "string", "description": "Link to the current task when this suggestion belongs to an existing task"},
                "action_payload": {
                    "type": "object",
                    "description": "Data needed to execute this suggestion when accepted",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = str(kwargs.get("task_id") or _resolve_task_id_from_active_conversation() or "")
        if _current_message_requests_direct_draft():
            from llm.tracing import log_trace

            message = _direct_draft_block_message()
            log_trace(
                "suggestion_blocked_direct_draft",
                "policy",
                message,
                task_id=task_id or None,
                detail={"title": kwargs["title"]},
            )
            return json.dumps({"status": "error", "message": message})
        if task_id and _current_task_notice_service_reported(task_id):
            from llm.tracing import log_trace

            log_trace(
                "suggestion_blocked_same_task",
                "policy",
                _same_task_handoff_block_message(),
                task_id=task_id,
                detail={"title": kwargs["title"]},
            )
            return json.dumps({"status": "error", "message": _same_task_handoff_block_message()})
        action_payload = _normalize_current_task_suggestion_payload(
            task_id=task_id,
            title=kwargs["title"],
            body=kwargs["body"],
            action_payload=kwargs.get("action_payload"),
        )
        if task_id:
            action_payload = _sanitize_tenant_message_person_payload(
                task_id=task_id,
                action_payload=action_payload,
            )
        if action_payload.get("action") == "request_file_upload" and task_id:
            if not _has_user_confirmed_upload_request(task_id):
                message = (
                    "Ask the user first whether they want a suggestion created for this upload request. "
                    "Explain what file is needed and why the current task is blocked."
                )
                from llm.tracing import log_trace

                log_trace(
                    "suggestion_deferred_pending_user_confirmation",
                    "policy",
                    message,
                    task_id=task_id,
                    detail={"action_payload": action_payload},
                )
                return json.dumps({"status": "error", "message": message})

        simulated_id = _queue_simulation_suggestion({
            "title": kwargs["title"],
            "body": kwargs["body"],
            "category": kwargs["suggestion_type"],
            "urgency": kwargs.get("urgency", "medium"),
            "property_id": kwargs.get("property_id"),
            "unit_id": kwargs.get("unit_id"),
            "document_id": kwargs.get("document_id"),
            "task_id": task_id or None,
            "action_payload": action_payload or None,
            "risk_score": kwargs.get("risk_score", 5),
            "suggestion_type": kwargs["suggestion_type"],
        })
        if simulated_id is not None:
            return json.dumps({
                "status": "ok",
                "suggestion_id": simulated_id,
                "message": f"Suggestion simulated: {kwargs['title']}",
            })

        options: list[SuggestionOption] = []
        if action_payload.get("action") == "request_file_upload":
            options = [
                SuggestionOption(key="upload", label=action_payload.get("requested_file_label", "Upload file"), action="request_file_upload", variant="default"),
                SuggestionOption(key="dismiss", label="Dismiss", action="reject_task", variant="ghost"),
            ]

        try:
            suggestion_id = _create_suggestion(
                title=kwargs["title"],
                ai_context=kwargs["body"],
                category=kwargs["suggestion_type"],
                urgency=kwargs.get("urgency", "medium"),
                action_payload=action_payload or None,
                options=options,
                task_id=task_id or None,
                property_id=kwargs.get("property_id"),
                unit_id=kwargs.get("unit_id"),
                document_id=kwargs.get("document_id"),
                risk_score=kwargs.get("risk_score", 5),
                suggestion_type=kwargs["suggestion_type"],
            )
            if action_payload.get("action") == "request_file_upload" and task_id:
                _mark_task_waiting_on_upload_request(
                    task_id=task_id,
                    requested_file_label=action_payload.get("requested_file_label", "requested file"),
                    instructions=action_payload.get("instructions") or kwargs["body"],
                )

            return json.dumps({
                "status": "ok",
                "suggestion_id": str(suggestion_id),
                "message": f"Suggestion created: {kwargs['title']}",
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})


class CreateScheduledTaskTool(Tool):
    """Create a recurring or one-shot scheduled task."""

    @property
    def name(self) -> str:
        return "create_scheduled_task"

    @property
    def description(self) -> str:
        return (
            "Create a scheduled task that runs the AI agent on a recurring schedule. "
            "Use for recurring checks (lease expiry, rent reminders, maintenance schedules). "
            "Schedule can be: cron expression ('0 9 * * 1'), interval ('every 4h'), "
            "or named ('daily', 'weekly', 'monthly'). The prompt describes what the "
            "agent should do each time it runs."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["name", "prompt", "schedule"],
            "properties": {
                "name": {"type": "string", "description": "Human-friendly name for this scheduled task"},
                "prompt": {"type": "string", "description": "What the agent should do each run (natural language)"},
                "schedule": {
                    "type": "string",
                    "description": "Cron expression, interval, or named schedule (e.g. '0 9 * * 1', 'every 4h', 'weekly')",
                },
                "repeat": {
                    "type": "integer",
                    "description": "Number of times to run (omit for forever)",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from backends.local_auth import resolve_account_id
        from db.models import ScheduledTask
        from handlers.scheduler import human_schedule, next_run, parse_schedule
        from llm.tools._common import tool_session

        name = kwargs["name"]
        prompt = kwargs["prompt"]
        raw_schedule = kwargs["schedule"]

        cron_expr = parse_schedule(raw_schedule)
        display = human_schedule(cron_expr)
        nxt = next_run(cron_expr)

        try:
            with tool_session() as db:
                import uuid
                from datetime import UTC, datetime

                task = ScheduledTask(
                    id=str(uuid.uuid4()),
                    creator_id=resolve_account_id(),
                    name=name,
                    prompt=prompt,
                    schedule=cron_expr,
                    schedule_display=display,
                    enabled=True,
                    state="scheduled",
                    repeat=kwargs.get("repeat"),
                    next_run_at=nxt,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                db.add(task)
                task_id = task.id

            return json.dumps({
                "status": "ok",
                "scheduled_task_id": task_id,
                "schedule": display,
                "next_run": nxt.isoformat(),
                "message": f"Scheduled task '{name}' created — {display}, next run {nxt.strftime('%b %d at %H:%M')}.",
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})


__all__ = [
    "ProposeTaskTool",
    "CloseTaskTool",
    "CreateSuggestionTool",
    "CreateScheduledTaskTool",
]
