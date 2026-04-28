"""Shared infrastructure for RentMate agent tools.

The `Tool` ABC, context variables used by the chat handler, and helpers that
are reused across more than one tool module live here.
"""
import contextvars
import json
import logging
import re
from abc import ABC, abstractmethod
from contextlib import contextmanager
from enum import Enum
from typing import Any

from db.enums import AgentSource, SuggestionOption, Urgency
from db.id_utils import normalize_optional_id
from db.models import MessageType

logger = logging.getLogger("rentmate.llm.tools")

_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{2,100}\]")


@contextmanager
def tool_session():
    """Yield a tool-scoped DB session wrapped in a nested SAVEPOINT.

    Each tool invocation runs inside its own savepoint, so a tool-level failure
    rolls back only the savepoint instead of the caller's surrounding
    transaction. That keeps eval tests (which use savepoint isolation to seed
    scenario data) intact when a tool raises, and in production still commits
    the tool's successful work to the database.

    Services called from inside the block may themselves call ``sess.commit()``
    (releasing our savepoint early); the helper tolerates that by checking
    ``sp.is_active`` before acting on it. On exception the savepoint is rolled
    back if still active and the exception is re-raised; the caller's session
    state is preserved.

    Imports ``SessionLocal`` from ``rentmate.app`` so the value picks up test
    patches consistently — the suite autouse fixture patches that name, and
    tests that swap a per-test engine (like the document chat regression test)
    patch it explicitly too.
    """
    from rentmate.app import SessionLocal

    sess = SessionLocal.session_factory()
    sp = sess.begin_nested()
    try:
        yield sess
    except Exception:
        if sp.is_active:
            sp.rollback()
        raise
    else:
        if sp.is_active:
            sp.commit()
        if sess.in_transaction():
            sess.commit()
    finally:
        sess.close()


class ToolMode(str, Enum):
    """Read vs write classification used by the simulation dispatcher.

    Read-only tools always run normally. Read-write tools have their
    inputs recorded and ``execute`` is skipped when a simulation context
    is active (see ``is_simulating``). Tools default to READ_WRITE — safe
    fallback when a new tool is added without thinking about simulation.
    """

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class ToolCategory(str, Enum):
    """How the tool surfaces to the manager. Used to group tools in the
    auto-generated SOUL.md tool list.

    - ``READ``: pure lookups, safe to run without consent.
    - ``IMMEDIATE``: writes that apply directly (saving notes, creating
      vendors, closing tasks).
    - ``REVIEW``: writes that go to the suggestion queue for manager
      approval (proposing tasks, sending external messages).
    """

    READ = "read"
    IMMEDIATE = "immediate"
    REVIEW = "review"


class Tool(ABC):
    """Base class for RentMate agent tools (standalone, no nanobot dependency)."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]: ...

    @property
    def mode(self) -> ToolMode:
        """Default classification. Override on subclasses that are read-only."""
        return ToolMode.READ_WRITE

    @property
    def category(self) -> ToolCategory:
        """SOUL.md grouping. Read-only tools default to READ; read-write
        defaults to IMMEDIATE. Tools that queue for human approval should
        override to REVIEW.
        """
        return ToolCategory.READ if self.mode == ToolMode.READ_ONLY else ToolCategory.IMMEDIATE

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str: ...


# Set by the chat handler before calling the agent so tools can link
# suggestions back to the originating conversation.
active_conversation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_conversation_id", default=None,
)

# Suggestion messages are collected here during tool execution and flushed
# by the chat handler *after* the AI reply is persisted, so they appear
# below the agent response in the conversation.
pending_suggestion_messages: contextvars.ContextVar[list[dict]] = contextvars.ContextVar(
    "pending_suggestion_messages", default=None,
)

current_user_message: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_message", default=None,
)

current_request_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "current_request_context", default=None,
)

# When set, tools classified as read-write short-circuit inside the
# dispatcher: the inputs are appended here and ``execute`` is skipped.
# Read-only tools still run normally. See ``ToolMode`` + ``is_simulating``.
simulation_actions: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "simulation_actions", default=None,
)

# Back-compat alias — pre-existing callers (handlers/routines.py, tests)
# imported this name. New code should prefer ``simulation_actions``.
simulation_suggestions = simulation_actions


def is_simulating() -> bool:
    """Return True when the current request is running inside a simulation."""
    return simulation_actions.get() is not None


def record_simulated_action(tool_name: str, args: dict[str, Any]) -> str:
    """Record a blackholed tool call during simulation. Returns the sim id.

    No-op (returns "") when called outside a simulation context — callers
    shouldn't rely on that path, but returning gracefully avoids surprises.
    """
    pending = simulation_actions.get()
    if pending is None:
        return ""
    sim_id = f"sim-{tool_name}-{len(pending) + 1}"
    pending.append({"id": sim_id, "tool": tool_name, "args": dict(args or {})})
    return sim_id


def _trace_tool_error(tool_name: str, summary: str, *, detail: dict[str, Any] | None = None) -> None:
    try:
        from llm.tracing import log_trace

        log_trace("error", "tool", summary, tool_name=tool_name, detail=detail)
    except Exception:
        pass


def _log_tool_error(tool_name: str, summary: str, *, detail: dict[str, Any] | None = None) -> None:
    safe_detail = detail or {}
    logger.error("%s: %s | detail=%s", tool_name, summary, json.dumps(safe_detail, default=str))
    _trace_tool_error(tool_name, summary, detail=safe_detail)


def _queue_simulation_suggestion(payload: dict[str, Any]) -> str | None:
    """Legacy shim — ``_create_suggestion`` records its simulation entry here
    directly (rather than going through the dispatcher gate) so the
    downstream suggestion-specific payload shape is preserved for the
    formatter. New tools should not call this; they're handled by the
    dispatcher via ``record_simulated_action``.
    """
    if not is_simulating():
        return None
    return record_simulated_action("create_suggestion", payload)


def _public_entity_id(entity: Any) -> str:
    external_id = getattr(entity, "external_id", None)
    return str(external_id or entity.id)


def _queue_chat_message(
    *,
    body: str,
    message_type: MessageType,
    related_task_ids: dict[str, Any] | None = None,
    draft_reply: str | None = None,
    action_card: dict[str, Any] | None = None,
) -> None:
    conv_id = active_conversation_id.get()
    if not conv_id:
        return
    pending = pending_suggestion_messages.get()
    if pending is None:
        pending = []
        pending_suggestion_messages.set(pending)
    pending.append({
        "conversation_id": conv_id,
        "type": "chat_message",
        "body": body,
        "message_type": message_type,
        "sender_name": "RentMate",
        "is_ai": True,
        "draft_reply": draft_reply,
        "related_task_ids": related_task_ids,
        "meta": {"action_card": action_card} if action_card else None,
    })


def _load_vendor_by_public_id(db: Any, vendor_id: str):
    from db.models import User

    vendor_id = normalize_optional_id(vendor_id)
    if vendor_id is None:
        return None
    return (
        db.query(User)
        .filter_by(external_id=str(vendor_id), user_type="vendor")
        .first()
    )


def _load_tenant_by_public_id(db: Any, tenant_id: str):
    from db.models import Tenant, User

    tenant_id = normalize_optional_id(tenant_id)
    if tenant_id is None:
        return None
    tenant = db.query(Tenant).filter_by(external_id=str(tenant_id)).first()
    if tenant:
        return tenant
    user = db.query(User).filter_by(external_id=str(tenant_id)).first()
    if user:
        return db.query(Tenant).filter_by(user_id=user.id).first()
    return None


def _resolve_task_tenant(db: Any, task_id: str):
    from db.models import Lease, Task, Tenant, Unit

    task = db.query(Task).filter_by(id=str(task_id)).first()
    if not task:
        return None
    if getattr(task, "unit_id", None):
        unit = db.query(Unit).filter_by(id=task.unit_id).first()
        if unit and getattr(unit, "tenant_id", None):
            tenant = db.query(Tenant).filter_by(id=unit.tenant_id).first()
            if tenant:
                return tenant
        lease = db.query(Lease).filter_by(unit_id=task.unit_id).order_by(Lease.start_date.desc()).first()
        if lease:
            return db.query(Tenant).filter_by(id=lease.tenant_id).first()
    if getattr(task, "property_id", None):
        lease = db.query(Lease).filter_by(property_id=task.property_id).order_by(Lease.start_date.desc()).first()
        if lease:
            return db.query(Tenant).filter_by(id=lease.tenant_id).first()
    return None


def _load_entity_by_public_id(db: Any, entity_type: str, entity_id: str):
    import db.models as models

    model_map = {
        "property": models.Property,
        "unit": models.Unit,
        "tenant": models.Tenant,
        "vendor": models.User,
        "document": models.Document,
    }
    model_cls = model_map.get(entity_type)
    if not model_cls:
        return None
    entity_id = normalize_optional_id(entity_id)
    if entity_id is None:
        return None

    filters = {"external_id": str(entity_id)} if hasattr(model_cls, "external_id") else {"id": entity_id}
    if entity_type == "vendor":
        filters["user_type"] = "vendor"
    return db.query(model_cls).filter_by(**filters).first()


def _sanitize_tenant_outbound_draft(db: Any, *, task_id: str, draft_message: str) -> str:
    from db.models import ParticipantType, Task, User

    draft = str(draft_message or "")
    if not draft:
        return draft

    task = db.query(Task).filter_by(id=str(task_id)).first()
    if not task:
        return draft
    ext_conv = task.latest_external_conversation
    if not ext_conv:
        return draft

    vendor_names: set[str] = set()
    assigned_vendor_name = ((ext_conv.extra or {}).get("assigned_vendor_name") or "").strip()
    if assigned_vendor_name:
        vendor_names.add(assigned_vendor_name)
    assigned_vendor_id = (ext_conv.extra or {}).get("assigned_vendor_id")
    if assigned_vendor_id:
        vendor = db.query(User).filter_by(id=assigned_vendor_id, user_type="vendor").first()
        if vendor and vendor.name:
            vendor_names.add(vendor.name.strip())
        if vendor and vendor.phone:
            draft = draft.replace(vendor.phone, "").strip()

    for message in ext_conv.messages or []:
        if getattr(message, "sender_type", None) == ParticipantType.EXTERNAL_CONTACT:
            sender_name = (message.sender_name or "").strip()
            if sender_name:
                vendor_names.add(sender_name)

    sanitized = draft
    for name in sorted(vendor_names, key=len, reverse=True):
        sanitized = re.sub(rf"\b{re.escape(name)}\b", "a contractor", sanitized)

    sanitized = re.sub(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b", "", sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized).strip()
    sanitized = re.sub(r"\ba contractor contractor\b", "a contractor", sanitized, flags=re.I)
    return sanitized


def _sanitize_vendor_outbound_draft(db: Any, *, task_id: str, draft_message: str) -> str:
    from db.models import Task

    draft = str(draft_message or "")
    if not draft:
        return draft

    tenant = _resolve_task_tenant(db, str(task_id))
    if not tenant or not getattr(tenant, "user", None):
        return draft

    sanitized = draft
    user = tenant.user
    for value in (user.name, user.phone, user.email):
        text = str(value or "").strip()
        if text:
            sanitized = re.sub(rf"\b{re.escape(text)}\b", "", sanitized, flags=re.I)
    sanitized = re.sub(r"\bthe tenant is\s*[.!,;:]?", "", sanitized, flags=re.I)
    sanitized = re.sub(r"\s+([.,;:!?])", r"\1", sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized).strip()
    if db.query(Task).filter_by(id=str(task_id)).first() is None:
        return draft
    return sanitized


def _find_unresolved_placeholders(*content_blocks: str | None) -> list[str]:
    placeholders: list[str] = []
    for block in content_blocks:
        if not block:
            continue
        placeholders.extend(match.group(0) for match in _UNRESOLVED_PLACEHOLDER_RE.finditer(block))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in placeholders:
        if item in seen:
            continue
        deduped.append(item)
        seen.add(item)
    return deduped


def _placeholder_message_block_error(*content_blocks: str | None) -> str | None:
    placeholders = _find_unresolved_placeholders(*content_blocks)
    if not placeholders:
        return None
    formatted = ", ".join(placeholders)
    return (
        "Do not send or stage outbound messages with placeholders. "
        f"The draft still contains unresolved placeholders: {formatted}. "
        "If a required concrete detail is missing, ask the PM with `ask_manager` instead of guessing."
    )


def _action_card_field(label: str, value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return {"label": label, "value": text}


def _enum_label(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, str):
        return value.replace("_", " ").title()
    try:
        return str(Urgency(value).name).replace("_", " ").title()
    except Exception:
        return str(value)


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
    unit_id: str | None = None,
    document_id: str | None = None,
    risk_score: int | None = None,
    suggestion_type: str | None = None,
) -> str:
    """Write a Suggestion row and return its ID.

    If ``active_conversation_id`` is set, also adds an APPROVAL message to
    that conversation so the suggestion appears inline in the chat.
    """
    property_id = normalize_optional_id(property_id)
    unit_id = normalize_optional_id(unit_id)
    document_id = normalize_optional_id(document_id)
    simulated_id = _queue_simulation_suggestion({
        "title": title,
        "body": ai_context,
        "category": category,
        "urgency": urgency,
        "action_payload": action_payload,
        "task_id": task_id,
        "property_id": property_id,
        "unit_id": unit_id,
        "document_id": document_id,
        "risk_score": risk_score,
        "suggestion_type": suggestion_type,
    })
    if simulated_id is not None:
        return simulated_id

    from db.models import Suggestion
    from gql.services import suggestion_service

    with tool_session() as db:
        # Deduplicate: skip if an equivalent pending suggestion already exists for this task
        if task_id and action_payload and action_payload.get("action"):
            from sqlalchemy import select
            existing = db.execute(
                select(Suggestion).where(
                    Suggestion.task_id == task_id,
                    Suggestion.status == "pending",
                )
            ).scalars().all()
            for s in existing:
                existing_payload = s.action_payload or {}
                if existing_payload.get("action") != action_payload["action"]:
                    continue
                if action_payload["action"] == "request_file_upload":
                    if (
                        existing_payload.get("requested_file_kind") == action_payload.get("requested_file_kind")
                        and str(existing_payload.get("target_task_id") or "") == str(action_payload.get("target_task_id") or task_id)
                        and str(existing_payload.get("target_tenant_id") or "") == str(action_payload.get("target_tenant_id") or "")
                    ):
                        return s.id
                else:
                    return s.id  # reuse existing suggestion

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
            unit_id=unit_id,
        )
        if task_id:
            suggestion.task_id = task_id
        if document_id:
            suggestion.document_id = document_id
        if risk_score is not None:
            suggestion.risk_score = risk_score
        if suggestion_type:
            suggestion.suggestion_type = suggestion_type
        suggestion_id = suggestion.id

        fields = [
                field
                for field in [
                _action_card_field("Category", _enum_label(category)),
                _action_card_field("Urgency", _enum_label(urgency)),
                _action_card_field("Vendor", action_payload.get("vendor_name") if action_payload else None),
                _action_card_field("Entity", action_payload.get("entity_name") if action_payload else None),
                _action_card_field("Requested File", action_payload.get("requested_file_label") if action_payload else None),
            ]
            if field
        ]
        links = [{
            "label": "Open suggestion",
            "entity_type": "suggestion",
            "entity_id": str(suggestion_id),
        }]
        if property_id:
            links.append({
                "label": "Property",
                "entity_type": "property",
                "entity_id": str(property_id),
            })
        _queue_chat_message(
            body=title,
            message_type=MessageType.ACTION,
            related_task_ids={"suggestion_id": suggestion_id},
            draft_reply=action_payload.get("draft_message") if action_payload else None,
            action_card={
                "kind": "suggestion",
                "title": title,
                "summary": ai_context,
                "fields": fields,
                "links": links,
                "units": [],
            },
        )

    # Trace suggestion creation (outside the tool session so its own commit
    # failure can't destroy the suggestion we just wrote).
    from llm.tracing import log_trace
    log_trace(
        "suggestion_created", "agent", title,
        suggestion_id=suggestion_id,
        detail=action_payload,
    )

    return suggestion_id


def _get_task_title(task_id: str) -> str:
    """Look up a task's title for use in suggestion headlines."""
    from db.models import Task
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        return task.title if task else task_id
    finally:
        db.close()


def _resolve_task_id_from_active_conversation() -> str | None:
    conv_id = active_conversation_id.get()
    if not conv_id:
        return None
    try:
        conv_lookup = int(conv_id)
    except (TypeError, ValueError):
        return None

    from db.models import Task
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(ai_conversation_id=conv_lookup).first()
        return str(task.id) if task else None
    finally:
        db.close()


def _recent_user_messages(task_id: str, *, limit: int = 6) -> list[str]:
    from db.models import Message, Task
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task or not task.ai_conversation_id:
            return []
        rows = (
            db.query(Message)
            .filter_by(conversation_id=task.ai_conversation_id)
            .order_by(Message.sent_at.desc())
            .limit(limit)
            .all()
        )
        return [str(m.body or "") for m in rows if not getattr(m, "is_ai", False)]
    finally:
        db.close()


def _get_task_category(task_id: str) -> str | None:
    """Look up a task's category for autonomy checks."""
    from db.models import Task
    from db.session import SessionLocal
    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        return task.category if task else None
    finally:
        db.close()


def _auto_execute_suggestion(suggestion_id: str, action: str) -> str | None:
    """Auto-execute a suggestion when in autonomous mode.

    Returns None on success, or an error message string on failure.
    """
    from gql.services.task_suggestions import SuggestionExecutor
    try:
        with tool_session() as db:
            executor = SuggestionExecutor.for_suggestion(db, suggestion_id)
            executor.execute(suggestion_id, action)
        return None
    except Exception as e:
        print(f"[auto-execute] Failed suggestion {suggestion_id} action={action}: {e}")
        import traceback
        traceback.print_exc()
        return str(e)
