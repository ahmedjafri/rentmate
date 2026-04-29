"""Task-lifecycle tools: propose, close, generic suggestion, routine."""
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from backends.local_auth import resolve_account_id, resolve_org_id
from db.enums import RoutineState, SuggestionOption, TaskCategory, TaskStatus, TaskStepStatus, Urgency
from gql.services.task_service import TaskProgressStep, dump_task_steps
from llm.tools._common import (
    Tool,
    ToolMode,
    _check_placeholder_ids,
    _create_suggestion,
    _load_vendor_by_public_id,
    _placeholder_message_block_error,
    _queue_simulation_suggestion,
    _recent_user_messages,
    _resolve_task_id_from_active_conversation,
    _sanitize_tenant_outbound_draft,
    current_request_context,
    current_user_message,
)

logger = logging.getLogger(__name__)


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

_DRAFT_SALUTATION_RE = re.compile(
    r"^\s*(?:hi|hello|hey|dear)\s+([A-Za-z][A-Za-z' -]{0,60}?)(?:[,!.\n]|$)",
    re.I,
)


def _salutation_name(draft: str | None) -> str:
    match = _DRAFT_SALUTATION_RE.search(str(draft or ""))
    if not match:
        return ""
    return " ".join(match.group(1).strip().lower().split())


def _vendor_address_names(vendor: Any) -> set[str]:
    names: set[str] = set()
    for raw in (
        getattr(vendor, "first_name", None),
        getattr(vendor, "last_name", None),
        getattr(vendor, "name", None),
        getattr(vendor, "company", None),
    ):
        value = " ".join(str(raw or "").strip().lower().split())
        if value:
            names.add(value)
    return names


def _vendor_draft_recipient_error(*, draft_message: str | None, vendor: Any, vendor_name: str) -> str | None:
    addressed_to = _salutation_name(draft_message)
    if not addressed_to:
        return None

    allowed_names = _vendor_address_names(vendor)
    if addressed_to in allowed_names:
        return None

    return (
        f"draft_message appears addressed to '{addressed_to}', but propose_task sends "
        f"draft_message to the assigned vendor ({vendor_name}). Use a vendor-addressed "
        "draft here, or omit draft_message and create a separate message_person "
        "suggestion for tenant outreach."
    )

_IN_TASK_PM_APPROVAL_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bapprove\b",
        r"\bapproval\b",
        r"\bowner approval\b",
        r"\bmanager approval\b",
        r"\bpm approval\b",
        r"\brequest approval\b",
        r"\bshould i proceed\b",
        r"\bshould we proceed\b",
        r"\bcan i proceed\b",
        r"\bokay to proceed\b",
        r"\bok to proceed\b",
        r"\bproceed with\b",
        r"\bbook\b",
        r"\bbooking\b",
        r"\bselect(?:ed|ing)? vendor\b",
        r"\bchoose between\b",
        r"\bwhich quote\b",
        r"\bwhich vendor\b",
        r"\bdecision\b",
    ]
]

# Verbs paired with outcome-state words signal a step that's verifying
# something actually got done (vs. scheduling/coordinating). Used both
# at propose-time (require last step matches) and at update-time
# (gate marking a confirmation step done on real external confirmation).
_OUTCOME_STATE_WORDS = (
    r"works?|working|worked|fixed|resolved|repair|repaired|complete|completed|completion|done|finished"
)
_OUTCOME_CONFIRM_STEP_RE = re.compile(
    rf"("
    rf"\bconfirm\b.*\b({_OUTCOME_STATE_WORDS})\b"
    rf"|"
    rf"\bverify\b.*\b({_OUTCOME_STATE_WORDS})\b"
    rf"|"
    rf"\bcheck\b.*\b({_OUTCOME_STATE_WORDS})\b"
    rf"|"
    rf"\bmake sure\b.*\b({_OUTCOME_STATE_WORDS})\b"
    rf")",
    re.I,
)
_AFFIRMATIVE_CONFIRM_RE = re.compile(
    r"\b(yes|works|working|fixed|resolved|all good|good now|looks good|confirmed|done|complete|completed)\b",
    re.I,
)
_NEGATIVE_CONFIRM_RE = re.compile(
    r"\b(still|not|isn't|isnt|doesn't|doesnt|won't|wont|broken|issue|problem|noise|leak|error|wrong|bad)\b",
    re.I,
)


def _has_user_confirmed_upload_request(task_id: str) -> bool:
    current_message = current_user_message.get()
    if current_message and any(pattern.search(current_message) for pattern in _SUGGESTION_CONFIRM_PATTERNS):
        return True
    for message in _recent_user_messages(task_id):
        if any(pattern.search(message) for pattern in _SUGGESTION_CONFIRM_PATTERNS):
            return True
    return False


def _is_confirmation_style_step(step: TaskProgressStep) -> bool:
    text = " ".join(s for s in (step.key, step.label, step.note or "") if s)
    return bool(_OUTCOME_CONFIRM_STEP_RE.search(text))


def _task_external_confirmation_received(db: Any, *, task_id: str) -> bool:
    from db.models import Message, ParticipantType, Task

    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        return False

    convo_ids: list[int] = []
    if getattr(task, "parent_conversation_id", None):
        convo_ids.append(int(task.parent_conversation_id))
    convo_ids.extend(
        int(convo.id)
        for convo in (getattr(task, "external_conversations", []) or [])
        if getattr(convo, "id", None) is not None and int(convo.id) not in convo_ids
    )
    if not convo_ids:
        return False

    inbound = (
        db.query(Message)
        .filter(
            Message.conversation_id.in_(convo_ids),
            Message.sender_type.in_([ParticipantType.TENANT, ParticipantType.EXTERNAL_CONTACT]),
        )
        .order_by(Message.sent_at.desc())
        .first()
    )
    if inbound is None:
        return False

    body = (inbound.body or "").strip()
    if not body:
        return False
    if _NEGATIVE_CONFIRM_RE.search(body):
        return False
    return bool(_AFFIRMATIVE_CONFIRM_RE.search(body))


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


def _is_in_task_manager_approval_request(
    *,
    task_id: str,
    title: str,
    body: str,
    action_payload: dict[str, Any],
) -> bool:
    if not task_id:
        return False
    if action_payload.get("action") == "request_file_upload":
        return False

    combined = "\n".join(
        part for part in [
            str(title or ""),
            str(body or ""),
            json.dumps(action_payload or {}, sort_keys=True),
        ]
        if part
    )
    return any(pattern.search(combined) for pattern in _IN_TASK_PM_APPROVAL_PATTERNS)


def _in_task_manager_approval_block_message() -> str:
    return (
        "This is a PM approval/decision blocker inside the current task. "
        "Do not create a suggestion for that. Use `ask_manager` in the task AI conversation instead."
    )


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

        steps = [TaskProgressStep.model_validate(s) for s in (task.steps or [])]
        blocked_note = f"Blocked until {requested_file_label} is uploaded."
        updated = False
        for step in steps:
            if step.key == "upload_requested_file" or requested_file_label.lower() in step.label.lower():
                step.status = TaskStepStatus.PENDING
                step.note = blocked_note
                updated = True
                break
        if not updated:
            steps.append(TaskProgressStep(
                key="upload_requested_file",
                label=f"Upload {requested_file_label}",
                status=TaskStepStatus.PENDING,
                note=blocked_note,
            ))

        task.steps = dump_task_steps(steps)
        flag_modified(task, "steps")
        task.task_mode = TaskMode.WAITING_APPROVAL
        task.updated_at = datetime.now(UTC)

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


def _trace_retrieval_items(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(context, dict):
        return []
    candidates = [
        context.get("retrieval"),
        (context.get("context") or {}).get("retrieval") if isinstance(context.get("context"), dict) else None,
    ]
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("items"), list):
            items.extend(item for item in candidate["items"] if isinstance(item, dict))
    return items


def _first_context_id(context: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(context, dict):
        return None
    for candidate in [
        context,
        context.get("request"),
        context.get("retrieval"),
        (context.get("retrieval") or {}).get("request") if isinstance(context.get("retrieval"), dict) else None,
        (context.get("context") or {}).get("retrieval", {}).get("request")
        if isinstance(context.get("context"), dict)
        and isinstance((context.get("context") or {}).get("retrieval"), dict)
        else None,
    ]:
        if isinstance(candidate, dict):
            value = (candidate.get(key) or "").strip() if isinstance(candidate.get(key), str) else candidate.get(key)
            if value:
                return str(value)
    return None


def _infer_task_location_from_request_context() -> tuple[str | None, str | None]:
    """Infer property/unit from server-side retrieval metadata when the LLM omits IDs."""
    context = current_request_context.get()
    property_id = _first_context_id(context, "property_id")
    unit_id = _first_context_id(context, "unit_id")
    if property_id or unit_id:
        return property_id, unit_id

    property_ids: list[str] = []
    unit_ids: list[str] = []
    for item in _trace_retrieval_items(context):
        if item.get("source_type") not in {"lease", "property", "unit", "tenant"}:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        item_property_id = metadata.get("property_id")
        item_unit_id = metadata.get("unit_id")
        if item_property_id:
            property_ids.append(str(item_property_id))
        if item_unit_id:
            unit_ids.append(str(item_unit_id))

    unique_property_ids = list(dict.fromkeys(property_ids))
    unique_unit_ids = list(dict.fromkeys(unit_ids))
    return (
        unique_property_ids[0] if len(unique_property_ids) == 1 else None,
        unique_unit_ids[0] if len(unique_unit_ids) == 1 else None,
    )


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


_VALID_LIST_TASKS_STATUS = {"active", "resolved", "all"}
_DEFAULT_LIST_TASKS_LIMIT = 50
_MAX_LIST_TASKS_LIMIT = 200


def _enum_value(value: Any) -> Any:
    if value is None:
        return None
    return getattr(value, "name", None) or getattr(value, "value", None) or str(value)


class ListTasksTool(Tool):
    """List/search existing tasks scoped to the current account."""

    mode = ToolMode.READ_ONLY

    @property
    def name(self) -> str:
        return "list_tasks"

    @property
    def description(self) -> str:
        return (
            "List existing tasks for the current account. Use before creating a "
            "new task to avoid duplicates, or to answer 'what's open right now?' "
            "questions. Returns each task's id, title, status, mode, urgency, "
            "priority, category, property/unit ids, goal, and timestamps. "
            "Defaults to active tasks, sorted by most recent activity."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "resolved", "all"],
                    "description": (
                        "Which tasks to include. 'active' (default) returns "
                        "tasks that are not resolved/dismissed; 'resolved' "
                        "returns finished tasks; 'all' returns everything."
                    ),
                },
                "property_id": {
                    "type": "string",
                    "description": "Filter to tasks attached to this property's UUID.",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Filter by urgency level.",
                },
                "query": {
                    "type": "string",
                    "description": "Case-insensitive partial match against task title and goal.",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Max tasks to return (default {_DEFAULT_LIST_TASKS_LIMIT}, "
                        f"hard cap {_MAX_LIST_TASKS_LIMIT})."
                    ),
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        err = _check_placeholder_ids(kwargs, [("property_id", "lookup_properties")])
        if err:
            return err

        from db.models import Task
        from db.session import SessionLocal

        status_filter = (kwargs.get("status") or "active").strip().lower()
        if status_filter not in _VALID_LIST_TASKS_STATUS:
            return json.dumps({
                "status": "error",
                "error": f"invalid status {status_filter!r}; expected one of {sorted(_VALID_LIST_TASKS_STATUS)}",
            })

        property_id = (kwargs.get("property_id") or "").strip() or None
        urgency_raw = (kwargs.get("urgency") or "").strip().lower() or None
        urgency_filter: Urgency | None = None
        if urgency_raw:
            try:
                urgency_filter = Urgency[urgency_raw.upper()]
            except KeyError:
                return json.dumps({
                    "status": "error",
                    "error": f"invalid urgency {urgency_raw!r}",
                })
        query = (kwargs.get("query") or "").strip().lower() or None

        limit_raw = kwargs.get("limit")
        try:
            limit = int(limit_raw) if limit_raw is not None else _DEFAULT_LIST_TASKS_LIMIT
        except (TypeError, ValueError):
            limit = _DEFAULT_LIST_TASKS_LIMIT
        if limit <= 0:
            limit = _DEFAULT_LIST_TASKS_LIMIT
        limit = min(limit, _MAX_LIST_TASKS_LIMIT)

        account_id = resolve_account_id()
        org_id = resolve_org_id()

        db = SessionLocal.session_factory()
        try:
            q = db.query(Task).filter(
                Task.creator_id == account_id,
                Task.org_id == org_id,
            )
            if status_filter == "active":
                q = q.filter(Task.task_status.in_([TaskStatus.SUGGESTED, TaskStatus.ACTIVE, TaskStatus.PAUSED]))
            elif status_filter == "resolved":
                q = q.filter(Task.task_status.in_([TaskStatus.RESOLVED, TaskStatus.DISMISSED]))
            if property_id:
                q = q.filter(Task.property_id == property_id)
            if urgency_filter is not None:
                q = q.filter(Task.urgency == urgency_filter)

            tasks = q.order_by(Task.updated_at.desc()).all()

            results: list[dict[str, Any]] = []
            for task in tasks:
                if query:
                    title_lc = (task.title or "").lower()
                    goal_lc = (task.goal or "").lower()
                    if query not in title_lc and query not in goal_lc:
                        continue
                results.append({
                    "id": task.id,
                    "title": task.title,
                    "status": _enum_value(task.task_status),
                    "mode": _enum_value(task.task_mode),
                    "urgency": _enum_value(task.urgency),
                    "priority": _enum_value(task.priority),
                    "category": _enum_value(task.category),
                    "property_id": task.property_id,
                    "unit_id": task.unit_id,
                    "lease_id": task.lease_id,
                    "goal": task.goal,
                    "last_message_at": task.last_message_at.isoformat() if task.last_message_at else None,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                })
                if len(results) >= limit:
                    break

            return json.dumps({"tasks": results, "count": len(results)})
        finally:
            db.close()


class ProposeTaskTool(Tool):
    """Create a task proposal for manager review."""

    @property
    def name(self) -> str:
        return "propose_task"

    @property
    def category(self):
        from llm.tools._common import ToolCategory
        return ToolCategory.REVIEW

    @property
    def description(self) -> str:
        return (
            "Propose a new task for a genuinely separate issue. "
            "Only use propose_task for a genuinely separate issue that needs its own task. "
            "Never use propose_task when the user asked you to draft a notice, letter, or document directly in chat. "
            "**Never use propose_task as a workaround for being blocked.** "
            "If you can't complete the request because you're missing info, "
            "lack a tool, or need a decision, call ``ask_manager`` instead — "
            "do not file a task asking the manager to do the thing themselves. "
            "You MUST provide a vendor_id external UUID — use lookup_vendors first. "
            "You MUST provide a goal — one sentence stating what 'done' looks like, outcome-flavored and specific. "
            "You MUST provide steps — an ordered list of 3–6 progress steps "
            "(each with key/label/status). Mark the first step `active`; the "
            "rest start `pending`. Tasks without steps render with an empty "
            "progress tracker and are rejected. "
            "**The LAST step MUST be an outcome-verification step** — phrasing "
            "like 'Confirm the faucet works', 'Verify the lock is fixed', "
            "'Make sure the leak is resolved'. The phrasing must include "
            "confirm/verify/check (or 'make sure') paired with "
            "works/fixed/repaired/resolved/completed. Without this terminal "
            "step the task can be marked 100% done before the real work "
            "happens; the propose call is rejected if the last step doesn't "
            "match this shape."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["title", "category", "vendor_id", "goal", "steps"],
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "goal": {
                    "type": "string",
                    "description": (
                        "One-sentence manager-facing intent statement — what 'done' looks like. "
                        "Outcome-flavored and concrete. Example: \"Fix the kitchen leak with a trusted plumber "
                        "this week and confirm the repair with Marcus.\" Do NOT restate the title."
                    ),
                },
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
                "property_id": {"type": "string", "description": "Property ID (if applicable). Use the resolved property ID from context or lookup_properties."},
                "unit_id": {"type": "string", "description": "Unit ID (if applicable). Use the resolved unit ID from context or lookup_properties."},
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
                                "enum": [s.value for s in TaskStepStatus],
                                "description": "Step status",
                            },
                            "note": {"type": "string", "description": "Optional context note"},
                        },
                    },
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        err = _check_placeholder_ids(kwargs, [
            ("vendor_id", "lookup_vendors"),
            ("property_id", "lookup_properties"),
            ("unit_id", "lookup_properties"),
        ])
        if err:
            return err

        task_id = str(kwargs.get("task_id") or _resolve_task_id_from_active_conversation() or "")
        if _current_message_requests_direct_draft():
            return json.dumps({"status": "error", "message": _direct_draft_block_message()})
        if task_id and _current_task_notice_service_reported(task_id):
            return json.dumps({"status": "error", "message": _same_task_handoff_block_message()})

        vendor_id = str(kwargs["vendor_id"])

        steps_raw = kwargs.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            return json.dumps({
                "status": "error",
                "message": (
                    "steps is required: provide 3–6 ordered progress steps "
                    "(each {key, label, status}). Mark the first step "
                    "`active`; the rest `pending`."
                ),
            })
        if not all(
            isinstance(step, dict) and step.get("label") and step.get("key")
            for step in steps_raw
        ):
            return json.dumps({
                "status": "error",
                "message": (
                    "Each step must be an object with both `key` and `label` "
                    "set; status defaults to `pending`."
                ),
            })

        from db.models import Property, Unit
        from db.session import SessionLocal
        db = SessionLocal.session_factory()
        try:
            vendor = _load_vendor_by_public_id(db, vendor_id)
            if not vendor:
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Vendor {vendor_id} not found. Use lookup_vendors first and pass "
                        "the returned vendor id; do not use placeholder vendor ids."
                    ),
                })
            vendor_name = vendor.name or vendor.company or "Vendor"
            recipient_error = _vendor_draft_recipient_error(
                draft_message=kwargs.get("draft_message"),
                vendor=vendor,
                vendor_name=vendor_name,
            )
            if recipient_error:
                return json.dumps({"status": "error", "message": recipient_error})

            # Reject fabricated property references — agents that fall back to
            # plausible-sounding property_ids ("the bothell house") would
            # otherwise create suggestions for properties that don't exist.
            property_id = (kwargs.get("property_id") or "").strip() or None
            unit_id = (kwargs.get("unit_id") or "").strip() or None
            inferred_property_id, inferred_unit_id = _infer_task_location_from_request_context()
            property_id = property_id or inferred_property_id
            unit_id = unit_id or inferred_unit_id
            if property_id:
                prop = db.query(Property).filter(
                    Property.id == property_id,
                    Property.org_id == resolve_org_id(),
                ).first()
                if not prop:
                    return json.dumps({
                        "status": "error",
                        "message": (
                            f"Property {property_id} not found. Call "
                            "`lookup_properties` with the user's reference "
                            "(name, nickname, or address) and use the returned "
                            "property_id — never invent one."
                        ),
                    })
            if unit_id:
                unit = db.query(Unit).filter(
                    Unit.id == unit_id,
                    Unit.org_id == resolve_org_id(),
                ).first()
                if not unit:
                    return json.dumps({
                        "status": "error",
                        "message": (
                            f"Unit {unit_id} not found. Call `lookup_properties` "
                            "and use a returned unit_id — never invent one."
                        ),
                    })
                if property_id and unit.property_id and str(unit.property_id) != str(property_id):
                    return json.dumps({
                        "status": "error",
                        "message": (
                            f"Unit {unit_id} belongs to property {unit.property_id}, "
                            f"not property {property_id}. Use matching property_id/unit_id values."
                        ),
                    })
                if not property_id and unit.property_id:
                    property_id = str(unit.property_id)
        finally:
            db.close()

        action_payload: dict = {
            "action": "send_and_create_task",
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
        }
        goal = (kwargs.get("goal") or "").strip()
        if goal:
            action_payload["goal"] = goal
        draft_message = kwargs.get("draft_message")
        placeholder_error = _placeholder_message_block_error(draft_message)
        if placeholder_error:
            return json.dumps({"status": "error", "message": placeholder_error})
        if draft_message:
            action_payload["draft_message"] = draft_message
        try:
            steps = [TaskProgressStep.model_validate(s) for s in (kwargs.get("steps") or [])]
        except Exception as exc:
            valid_statuses = sorted({s.value for s in TaskStepStatus})
            return json.dumps({
                "status": "error",
                "message": (
                    f"Step validation failed: {exc}. Each step must have key, "
                    f"label, and status — valid step statuses are {valid_statuses}. "
                    "Track waiting/blocked/needs-action state via record_task_review, "
                    "not step status."
                ),
            })
        # Require the LAST step to be an outcome-verification step (matches
        # ``confirm/verify/check ... works/fixed/repaired/completed``). Without
        # this, agents propose plans like "review issue / schedule vendor /
        # confirm access" and mark all three done before the actual repair
        # happens — the dashboard then shows 3/3 while the work is unfinished.
        # The same regex powers the runtime gate in
        # ``UpdateTaskProgressTool``, so a confirmation step also gets the
        # "external confirmation required" enforcement automatically.
        if steps and not _OUTCOME_CONFIRM_STEP_RE.search(
            f"{steps[-1].key} {steps[-1].label} {steps[-1].note or ''}"
        ):
            return json.dumps({
                "status": "error",
                "message": (
                    "The final step must be an outcome-verification step — "
                    "something like 'Confirm faucet repair completed' or "
                    "'Verify the lock works'. Phrasing must include "
                    "confirm/verify/check (or 'make sure') paired with "
                    "works/fixed/repaired/resolved/completed. Without a "
                    "verification step, the task can be marked 100% done "
                    "before the actual work happens."
                ),
            })
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
            property_id=property_id,
            unit_id=unit_id,
            risk_score=risk,
            suggestion_type=kwargs["category"],
        )
        return json.dumps({
            "status": "pending_approval",
            "task_id": None,
            "proposal_id": sid,
            "message": (
                f"Task proposal '{kwargs['title']}' with {vendor_name} is queued for manager review. "
                "The task does NOT exist yet — there is no task_id to act on. "
                "STOP this turn now: do not call message_person, update_task_progress, close_task, "
                "or any tool that takes a task_id. The manager will approve the proposal in a "
                "separate step; only then can dependent actions run."
            ),
        })


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
        err = _check_placeholder_ids(kwargs, [("task_id", "list_tasks")])
        if err:
            return err
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


class UpdateTaskProgressTool(Tool):
    """Update progress steps on an existing task."""

    @property
    def name(self) -> str:
        return "update_task_progress"

    @property
    def description(self) -> str:
        return (
            "Update one progress step on a task by marking it pending, active, or done. "
            "Use this whenever work advances so the task can be closed once all steps are done. "
            "Provide either step_key or step_label to identify the step. "
            "**Do NOT mark a step done before the underlying real-world work has actually happened.** "
            "Scheduling a vendor is not 'repair complete'. A tenant agreeing to an access window is "
            "not 'access confirmed for the appointment that already happened'. The final "
            "verification step (Confirm/Verify the work) is gated server-side: it can only be "
            "marked done after a tenant or vendor inbound message actually confirms the outcome."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "status"],
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to update"},
                "step_key": {"type": "string", "description": "Unique step key to update"},
                "step_label": {"type": "string", "description": "Step label to update when key is unknown"},
                "status": {
                    "type": "string",
                    "enum": [s.value for s in TaskStepStatus],
                    "description": "New step status",
                },
                "note": {"type": "string", "description": "Optional step note to store"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        err = _check_placeholder_ids(kwargs, [("task_id", "list_tasks")])
        if err:
            return err
        agent_supplied_task_id = kwargs.get("task_id")
        task_id = agent_supplied_task_id
        step_key = (kwargs.get("step_key") or "").strip()
        step_label = (kwargs.get("step_label") or "").strip()
        status = kwargs["status"]
        note = kwargs.get("note")

        # Reject unknown statuses up front with a tool-friendly error.
        # The JSON-schema enum is just a hint; agents sometimes pass review-
        # vocabulary words like "waiting" that aren't valid step statuses.
        valid_statuses = {s.value for s in TaskStepStatus}
        if status not in valid_statuses:
            return json.dumps({
                "status": "error",
                "message": (
                    f"Invalid step status {status!r}. Valid step statuses are: "
                    f"{sorted(valid_statuses)}. Use 'pending' for steps not yet "
                    "started, 'active' for the in-progress step, and 'done' "
                    "for completed steps. Track waiting/blocked/needs-action "
                    "state via record_task_review, not step status."
                ),
            })

        # Active conversation is ground truth: progress updates posted
        # from inside a task's AI conversation belong to that task.
        # Override any hallucinated task_id and rescue agents that
        # omit it entirely.
        active_task_id = _resolve_task_id_from_active_conversation()
        if active_task_id is not None:
            if (
                agent_supplied_task_id is not None
                and str(agent_supplied_task_id) != str(active_task_id)
            ):
                logger.warning(
                    "update_task_progress task_id override: agent passed "
                    "%s but active conversation belongs to task %s — "
                    "using active task",
                    agent_supplied_task_id, active_task_id,
                )
            task_id = active_task_id

        if not step_key and not step_label:
            return json.dumps({"status": "error", "message": "Provide step_key or step_label."})

        from datetime import datetime

        from sqlalchemy.orm.attributes import flag_modified

        from db.models import Task as TaskModel
        from llm.tools._common import tool_session

        with tool_session() as db:
            task = db.query(TaskModel).filter_by(id=task_id).first()
            if not task:
                return json.dumps({"status": "error", "message": f"Task {task_id} not found"})

            # Tolerate stored steps with statuses outside the current enum
            # (e.g. legacy 'waiting' rows). Coerce unknowns to 'pending' and
            # log so the operator can spot upstream bugs without locking the
            # agent out of progress updates.
            steps: list[TaskProgressStep] = []
            for raw in (task.steps or []):
                try:
                    steps.append(TaskProgressStep.model_validate(raw))
                except Exception:
                    coerced = dict(raw or {})
                    bad_status = coerced.get("status")
                    if bad_status not in valid_statuses:
                        logger.warning(
                            "update_task_progress: coercing invalid stored step status %r → 'pending' on task %s step %r",
                            bad_status, task_id, coerced.get("key"),
                        )
                        coerced["status"] = TaskStepStatus.PENDING.value
                    steps.append(TaskProgressStep.model_validate(coerced))
            if not steps:
                return json.dumps({"status": "error", "message": f"Task {task_id} has no progress steps"})

            updated_step: TaskProgressStep | None = None
            for step in steps:
                matches_key = step_key and step.key == step_key
                matches_label = step_label and step.label.strip().lower() == step_label.lower()
                if matches_key or matches_label:
                    step.status = status
                    if note is not None:
                        step.note = note
                    updated_step = step
                    break

            if updated_step is None:
                identifier = step_key or step_label
                return json.dumps({"status": "error", "message": f"Step '{identifier}' not found on task {task_id}"})

            if status == TaskStepStatus.DONE and _is_confirmation_style_step(updated_step):
                if not _task_external_confirmation_received(db, task_id=str(task_id)):
                    return json.dumps({
                        "status": "error",
                        "message": (
                            f"Cannot mark step '{updated_step.label or updated_step.key}' done "
                            "until an external tenant/vendor confirmation has actually been received."
                        ),
                    })

            # Keep only one active step. If a step was just completed, advance the next pending step.
            if status == TaskStepStatus.ACTIVE:
                for step in steps:
                    if step is updated_step:
                        continue
                    if step.status == TaskStepStatus.ACTIVE:
                        step.status = TaskStepStatus.PENDING
            elif status == TaskStepStatus.DONE:
                for step in steps:
                    if step is updated_step:
                        continue
                    if step.status == TaskStepStatus.ACTIVE:
                        step.status = TaskStepStatus.PENDING
                next_pending = next((s for s in steps if s.status == TaskStepStatus.PENDING), None)
                if next_pending is not None:
                    next_pending.status = TaskStepStatus.ACTIVE

            task.steps = dump_task_steps(steps)
            flag_modified(task, "steps")
            task.updated_at = datetime.now(UTC)

            return json.dumps({
                "status": "ok",
                "message": f"Updated step '{updated_step.label or updated_step.key}' to {status}.",
                "steps": task.steps,
            })


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
            "If the blocker is a PM approval or decision inside the current task, use `ask_manager` instead of create_suggestion. "
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
        err = _check_placeholder_ids(kwargs, [
            ("task_id", "list_tasks"),
            ("property_id", "lookup_properties"),
            ("unit_id", "lookup_properties"),
            ("document_id", None),
        ])
        if err:
            return err

        task_id = str(kwargs.get("task_id") or _resolve_task_id_from_active_conversation() or "")
        if _current_message_requests_direct_draft():
            from llm.tracing import log_trace

            message = _direct_draft_block_message()
            log_trace(
                "suggestion_blocked_direct_draft",
                "policy",
                message,
                detail={"title": kwargs["title"]},
            )
            return json.dumps({"status": "error", "message": message})
        if task_id and _current_task_notice_service_reported(task_id):
            from llm.tracing import log_trace

            log_trace(
                "suggestion_blocked_same_task",
                "policy",
                _same_task_handoff_block_message(),
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
        if _is_in_task_manager_approval_request(
            task_id=task_id,
            title=kwargs["title"],
            body=kwargs["body"],
            action_payload=action_payload,
        ):
            from llm.tracing import log_trace

            message = _in_task_manager_approval_block_message()
            log_trace(
                "suggestion_blocked_in_task_manager_approval",
                "policy",
                message,
                detail={
                    "title": kwargs["title"],
                    "action_payload": action_payload or None,
                },
            )
            return json.dumps({"status": "error", "message": message})
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


class CreateRoutineTool(Tool):
    """Create a recurring or one-shot routine."""

    @property
    def name(self) -> str:
        return "create_routine"

    @property
    def description(self) -> str:
        return (
            "Create a routine that runs the AI agent on a recurring schedule. "
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
                "name": {"type": "string", "description": "Human-friendly name for this routine"},
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
        err = _check_placeholder_ids(kwargs, [
            ("property_id", "lookup_properties"),
            ("unit_id", "lookup_properties"),
        ])
        if err:
            return err

        from db.models import Routine
        from handlers.routines import human_schedule, next_run, parse_schedule
        from llm.tools._common import tool_session

        name = kwargs["name"]
        prompt = kwargs["prompt"]
        raw_schedule = kwargs["schedule"]

        cron_expr = parse_schedule(raw_schedule)
        display = human_schedule(cron_expr)
        nxt = next_run(cron_expr)

        try:
            with tool_session() as db:
                from datetime import UTC, datetime

                from gql.services.number_allocator import NumberAllocator

                org_id = resolve_org_id()
                task = Routine(
                    id=NumberAllocator.allocate_next(db, entity_type="routine", org_id=org_id),
                    org_id=org_id,
                    creator_id=resolve_account_id(),
                    name=name,
                    prompt=prompt,
                    schedule=cron_expr,
                    schedule_display=display,
                    enabled=True,
                    state=RoutineState.SCHEDULED,
                    repeat=kwargs.get("repeat"),
                    next_run_at=nxt,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                db.add(task)
                task_id = task.id

            return json.dumps({
                "status": "ok",
                "routine_id": task_id,
                "schedule": display,
                "next_run": nxt.isoformat(),
                "message": f"Routine '{name}' created — {display}, next run {nxt.strftime('%b %d at %H:%M')}.",
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})


__all__ = [
    "ProposeTaskTool",
    "CloseTaskTool",
    "UpdateTaskProgressTool",
    "CreateSuggestionTool",
    "CreateRoutineTool",
]
