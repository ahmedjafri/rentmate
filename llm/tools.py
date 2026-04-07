"""RentMate agent tool classes.

Includes suggestion tools (propose_task, close_task, set_mode, attach_entity, message_person).

When a tool creates a suggestion during a chat, it queues a SUGGESTION message
via ``pending_suggestion_messages``.  The chat handler flushes these *after*
persisting the AI reply so they appear below the agent response in the
conversation timeline.  The conversation_id is communicated via the
``active_conversation_id`` context variable, set by the chat handler before
the agent runs.
"""
import contextvars
import json
from abc import ABC, abstractmethod
from typing import Any


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

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str: ...

from db.enums import AgentSource, SuggestionOption, TaskCategory, Urgency
from db.models import MessageType

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
    from db.models import Suggestion

    db = SessionLocal.session_factory()
    try:
        # Deduplicate: skip if a pending suggestion with the same action already exists for this task
        if task_id and action_payload and action_payload.get("action"):
            from sqlalchemy import select
            existing = db.execute(
                select(Suggestion).where(
                    Suggestion.task_id == task_id,
                    Suggestion.status == "pending",
                )
            ).scalars().all()
            for s in existing:
                if (s.action_payload or {}).get("action") == action_payload["action"]:
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
        )
        if task_id:
            suggestion.task_id = task_id

        # Queue a suggestion message to be flushed after the AI reply so it
        # appears below the agent response in the conversation timeline.
        conv_id = active_conversation_id.get()
        if conv_id:
            body_parts = [title]
            if action_payload:
                if action_payload.get("entity_name"):
                    body_parts.append(f"{(action_payload.get('entity_type') or 'entity').title()}: {action_payload['entity_name']}")
                elif action_payload.get("vendor_name"):
                    body_parts.append(f"Vendor: {action_payload['vendor_name']}")
                if action_payload.get("draft_message"):
                    body_parts.append(f"Draft: {action_payload['draft_message'][:200]}")
            pending = pending_suggestion_messages.get()
            if pending is None:
                pending = []
                pending_suggestion_messages.set(pending)
            pending.append({
                "conversation_id": conv_id,
                "body": "\n".join(body_parts),
                "message_type": MessageType.SUGGESTION,
                "sender_name": "RentMate",
                "is_ai": True,
                "draft_reply": action_payload.get("draft_message") if action_payload else None,
                "related_task_ids": {"suggestion_id": suggestion.id},
            })

        db.commit()

        # Trace suggestion creation
        from llm.tracing import log_trace
        log_trace(
            "suggestion_created", "agent", title,
            task_id=task_id,
            suggestion_id=suggestion.id,
            detail=action_payload,
        )

        return suggestion.id
    finally:
        db.close()


def _get_task_title(task_id: str) -> str:
    """Look up a task's title for use in suggestion headlines."""
    from handlers.deps import SessionLocal
    from db.models import Task

    db = SessionLocal.session_factory()
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
            "The proposal appears in the action desk for approval. "
            "You MUST provide a vendor_id — use lookup_vendors first to find "
            "a suitable vendor for the task. Include a draft_message for the "
            "vendor if appropriate. Always include steps — the ordered plan "
            "for completing this task (3-6 steps)."
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
                "vendor_id": {"type": "string", "description": "ID of the vendor to assign (use lookup_vendors to find this)"},
                "draft_message": {"type": "string", "description": "Draft message to send to the vendor on approval"},
                "property_id": {"type": "string", "description": "Property ID (if applicable)"},
                "task_id": {"type": "string", "description": "Originating task ID (if applicable)"},
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
        vendor_id = kwargs["vendor_id"]

        from handlers.deps import SessionLocal
        from db.models import ExternalContact
        db = SessionLocal.session_factory()
        try:
            vendor = db.query(ExternalContact).filter_by(id=vendor_id).first()
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
            action_payload["steps"] = steps

        if draft_message:
            options = [
                SuggestionOption(key="send", label=f"Create & Send to {vendor_name}", action="approve_draft", variant="default"),
                SuggestionOption(key="accept", label="Create Without Sending", action="accept_task", variant="outline"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]
        else:
            options = [
                SuggestionOption(key="accept", label=f"Create & Assign {vendor_name}", action="accept_task", variant="default"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]

        sid = _create_suggestion(
            title=kwargs["title"],
            ai_context=kwargs.get("description") or kwargs["title"],
            category=kwargs["category"],
            urgency=kwargs.get("urgency", Urgency.MEDIUM.value),
            action_payload=action_payload,
            options=options,
            task_id=kwargs.get("task_id"),
            property_id=kwargs.get("property_id"),
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

        from handlers.deps import SessionLocal
        from db.models import Task as TaskModel
        from datetime import UTC, datetime
        db = SessionLocal.session_factory()
        try:
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

            task.task_status = "resolved"
            if not task.resolved_at:
                task.resolved_at = datetime.now(UTC)
            db.commit()
            return json.dumps({"status": "ok", "message": "Task resolved."})
        finally:
            db.close()


class SetModeTool(Tool):
    """Change a task's operating mode directly."""

    @property
    def name(self) -> str:
        return "set_mode"

    @property
    def description(self) -> str:
        return (
            "Change a task's operating mode (autonomous, manual, "
            "or waiting_approval). Takes effect immediately."
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

        from handlers.deps import SessionLocal
        from db.models import Task as TaskModel
        db = SessionLocal.session_factory()
        try:
            task = db.query(TaskModel).filter_by(id=task_id).first()
            if not task:
                return json.dumps({"status": "error", "message": f"Task {task_id} not found"})
            task.task_mode = mode
            db.commit()
            return json.dumps({"status": "ok", "message": f"Task mode changed to {mode}."})
        finally:
            db.close()


def _get_task_category(task_id: str) -> str | None:
    """Look up a task's category for autonomy checks."""
    from handlers.deps import SessionLocal
    from db.models import Task
    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        return task.category if task else None
    finally:
        db.close()


def _auto_execute_suggestion(suggestion_id: str, action: str) -> str | None:
    """Auto-execute a suggestion when in autonomous mode.

    Returns None on success, or an error message string on failure.
    The executor's ``send_autonomous_message`` commits internally, so we
    don't add our own commit — just close the session when done.
    """
    from handlers.deps import SessionLocal
    from handlers.task_suggestions import SuggestionExecutor
    db = SessionLocal.session_factory()
    try:
        executor = SuggestionExecutor.for_suggestion(db, suggestion_id)
        executor.execute(suggestion_id, action)
        # Flush any remaining changes (task FK updates, suggestion status)
        # that weren't committed by inner service calls.
        if db.new or db.dirty:
            db.commit()
        return None
    except Exception as e:
        print(f"[auto-execute] Failed suggestion {suggestion_id} action={action}: {e}")
        import traceback
        traceback.print_exc()
        try:
            db.rollback()
        except Exception:
            pass
        return str(e)
    finally:
        db.close()


class AttachEntityToTaskTool(Tool):
    """Attach any entity (vendor, tenant, property, unit) to a task."""

    @property
    def name(self) -> str:
        return "attach_entity"

    @property
    def description(self) -> str:
        return (
            "Attach an entity to an existing task. For vendors and tenants, this also "
            "creates/links a conversation. For properties and units, sets the FK on the task. "
            "Use lookup_vendors first if you need to find a vendor ID."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "entity_id", "entity_type"],
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task"},
                "entity_id": {"type": "string", "description": "ID of the entity to attach"},
                "entity_type": {
                    "type": "string",
                    "enum": ["vendor", "tenant", "property", "unit"],
                    "description": "Type of entity",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        entity_id = kwargs["entity_id"]
        entity_type = kwargs["entity_type"]
        task_title = _get_task_title(task_id)

        from handlers.deps import SessionLocal
        from db.models import ExternalContact, Tenant, Property, Unit
        db = SessionLocal.session_factory()
        try:
            if entity_type == "vendor":
                entity = db.query(ExternalContact).filter_by(id=entity_id).first()
                entity_name = entity.name if entity else "Vendor"
            elif entity_type == "tenant":
                entity = db.query(Tenant).filter_by(id=entity_id).first()
                entity_name = f"{entity.first_name} {entity.last_name}".strip() if entity else "Tenant"
            elif entity_type == "property":
                entity = db.query(Property).filter_by(id=entity_id).first()
                entity_name = entity.name or entity.address_line1 if entity else "Property"
            elif entity_type == "unit":
                entity = db.query(Unit).filter_by(id=entity_id).first()
                entity_name = entity.label if entity else "Unit"
            else:
                return json.dumps({"status": "error", "message": f"Unknown entity type: {entity_type}"})

            if not entity:
                return json.dumps({"status": "error", "message": f"{entity_type.title()} {entity_id} not found"})
        finally:
            db.close()

        action_payload = {
            "action": "attach_entity",
            "entity_id": entity_id,
            "entity_type": entity_type,
            "entity_name": entity_name,
        }
        options = [
            SuggestionOption(key="attach", label=f"Attach {entity_name}", action="attach_entity", variant="default"),
            SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
        ]

        sid = _create_suggestion(
            title=f"Attach {entity_type}: {entity_name} to {task_title}",
            ai_context=f"The agent recommends attaching {entity_type} '{entity_name}' to this task.",
            options=options,
            action_payload=action_payload,
            task_id=task_id,
        )

        # Auto-execute in autonomous mode
        from gql.services import settings_service
        category = _get_task_category(task_id)
        if settings_service.get_autonomy_for_category(category) == "autonomous":
            err = _auto_execute_suggestion(sid, "attach_entity")
            if err:
                return json.dumps({"status": "error", "suggestion_id": sid, "message": f"Failed to attach {entity_name}: {err}. Suggestion saved for manual review."})
            return json.dumps({"status": "ok", "suggestion_id": sid, "message": f"{entity_type.title()} '{entity_name}' attached to task (auto-approved)."})

        return json.dumps({"status": "ok", "suggestion_id": sid, "message": f"Suggestion to attach {entity_name} created for manager review."})


class MessageExternalPersonTool(Tool):
    """Send a message to an external person (tenant or vendor) on a task."""

    @property
    def name(self) -> str:
        return "message_person"

    @property
    def description(self) -> str:
        return (
            "Send a message to a tenant or vendor on a task. Use the Tenant ID or Vendor ID "
            "from the task context — you already have them, do not ask for contact info. "
            "In autonomous mode, sends immediately via SMS + portal link. "
            "If the person is not yet linked to the task, a conversation will be created."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "entity_id", "entity_type", "draft_message"],
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task"},
                "entity_id": {"type": "string", "description": "ID of the tenant or vendor"},
                "entity_type": {
                    "type": "string",
                    "enum": ["tenant", "vendor"],
                    "description": "Type of person to message",
                },
                "draft_message": {"type": "string", "description": "The message to send"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        entity_id = kwargs["entity_id"]
        entity_type = kwargs["entity_type"]
        draft_message = kwargs["draft_message"]
        task_title = _get_task_title(task_id)

        from handlers.deps import SessionLocal
        from db.models import ExternalContact, Tenant
        db = SessionLocal.session_factory()
        try:
            if entity_type == "vendor":
                entity = db.query(ExternalContact).filter_by(id=entity_id).first()
                entity_name = entity.name if entity else "Vendor"
                entity_phone = entity.phone if entity else None
            elif entity_type == "tenant":
                entity = db.query(Tenant).filter_by(id=entity_id).first()
                entity_name = f"{entity.first_name} {entity.last_name}".strip() if entity else "Tenant"
                entity_phone = entity.phone if entity else None
            else:
                return json.dumps({"status": "error", "message": f"Can only message tenants or vendors, not {entity_type}"})

            if not entity:
                return json.dumps({"status": "error", "message": f"{entity_type.title()} {entity_id} not found"})
        finally:
            db.close()

        action_payload = {
            "action": "message_person",
            "entity_id": entity_id,
            "entity_type": entity_type,
            "entity_name": entity_name,
            "entity_phone": entity_phone,
            "draft_message": draft_message,
        }
        options = [
            SuggestionOption(key="send", label=f"Send to {entity_name}", action="message_person_send", variant="default"),
            SuggestionOption(key="edit", label="Edit Message", action="edit_draft", variant="outline"),
            SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
        ]

        sid = _create_suggestion(
            title=f"Message {entity_name}: {task_title}",
            ai_context=f"The agent wants to send a message to {entity_name} ({entity_type}).\n\nDraft message:\n{draft_message}",
            options=options,
            action_payload=action_payload,
            task_id=task_id,
        )

        # Auto-execute in autonomous mode
        from gql.services import settings_service
        category = _get_task_category(task_id)
        if settings_service.get_autonomy_for_category(category) == "autonomous":
            err = _auto_execute_suggestion(sid, "message_person_send")
            if err:
                return json.dumps({"status": "error", "suggestion_id": sid, "message": f"Failed to send message to {entity_name}: {err}. Suggestion saved for manual review."})
            note = f"Message sent to {entity_name} (auto-approved)."
            if not entity_phone:
                note += " Note: no phone number on file, message saved but not delivered via SMS."
            return json.dumps({"status": "ok", "suggestion_id": sid, "message": note})

        return json.dumps({"status": "ok", "suggestion_id": sid, "message": f"Message suggestion for {entity_name} created for manager review."})


class LookupVendorsTool(Tool):
    """Look up vendors in the system, optionally filtered by type."""

    @property
    def name(self) -> str:
        return "lookup_vendors"

    @property
    def description(self) -> str:
        return (
            "Search for vendors/contractors in the system. "
            "Returns a list of vendors with their name, company, type, phone, and email. "
            "Optionally filter by vendor_type (e.g. 'Plumber', 'Electrician', 'Landscaper')."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "vendor_type": {
                    "type": "string",
                    "description": "Filter by vendor type (e.g. 'Plumber', 'Electrician'). Omit to list all vendors.",
                },
                "query": {
                    "type": "string",
                    "description": "Search by name or company (case-insensitive partial match). Omit to list all.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from handlers.deps import SessionLocal
        from db.models import ExternalContact

        vendor_type = kwargs.get("vendor_type")
        query = (kwargs.get("query") or "").strip().lower()

        db = SessionLocal.session_factory()
        try:
            vendors = db.query(ExternalContact).all()
            results = []
            for v in vendors:
                if vendor_type and (v.role_label or "").lower() != vendor_type.lower():
                    continue
                if query and query not in (v.name or "").lower() and query not in (v.company or "").lower():
                    continue
                results.append({
                    "id": str(v.id),
                    "name": v.name,
                    "company": v.company,
                    "vendor_type": v.role_label,
                    "phone": v.phone,
                    "email": v.email,
                })
            if not results:
                return json.dumps({"vendors": [], "message": "No vendors found matching the criteria."})
            return json.dumps({"vendors": results, "count": len(results)})
        finally:
            db.close()


class UpdateStepsTool(Tool):
    """Update the ordered progress steps for a task."""

    @property
    def name(self) -> str:
        return "update_steps"

    @property
    def description(self) -> str:
        return (
            "Set or update the ordered progress steps for a task. Each step "
            "has a key, label, status (pending/active/done), and optional note. "
            "Pass the full list of steps — it replaces the current list. "
            "Use this when you have enough context to lay out a plan, or when "
            "a conversation indicates a step has been completed. "
            "This is a read-write tool that updates immediately (no approval needed)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "steps"],
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task"},
                "steps": {
                    "type": "array",
                    "description": "Ordered list of progress steps",
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
                            "note": {"type": "string", "description": "Optional note (e.g. 'Vendor confirmed Thursday 2pm')"},
                        },
                    },
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        steps = kwargs["steps"]

        from handlers.deps import SessionLocal
        from db.models import Task
        from sqlalchemy.orm.attributes import flag_modified

        db = SessionLocal.session_factory()
        try:
            task = db.query(Task).filter_by(id=task_id).first()
            if not task:
                return json.dumps({"status": "error", "message": f"Task {task_id} not found"})
            task.steps = steps
            flag_modified(task, "steps")
            db.commit()
            step_summary = ", ".join(s["label"] for s in steps)
            return json.dumps({"status": "ok", "message": f"Steps updated: {step_summary}"})
        finally:
            db.close()


class CreateVendorTool(Tool):
    """Create a new vendor/contractor in the system."""

    @property
    def name(self) -> str:
        return "create_vendor"

    @property
    def description(self) -> str:
        return (
            "Create a new vendor or contractor. Requires a name and phone number. "
            "Optionally include company, vendor_type (e.g. 'Plumber', 'Electrician', "
            "'HVAC', 'General Contractor', 'Handyman', 'Landscaper', 'Locksmith', "
            "'Roofer', 'Painter', 'Inspector'), and email."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["name", "phone", "vendor_type"],
            "properties": {
                "name": {"type": "string", "description": "Vendor's full name"},
                "phone": {"type": "string", "description": "Vendor's phone number"},
                "vendor_type": {"type": "string", "description": "Type of vendor (e.g. 'Plumber', 'Electrician', 'HVAC', 'General Contractor', 'Handyman', 'Landscaper', 'Locksmith', 'Roofer', 'Painter', 'Inspector')"},
                "company": {"type": "string", "description": "Company name (optional)"},
                "email": {"type": "string", "description": "Vendor's email address (optional)"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from handlers.deps import SessionLocal
        from gql.services.vendor_service import VendorService
        from gql.types import CreateVendorInput

        db = SessionLocal.session_factory()
        try:
            vendor = VendorService.create_vendor(db, CreateVendorInput(
                name=kwargs["name"],
                phone=kwargs["phone"],
                company=kwargs.get("company"),
                vendor_type=kwargs.get("vendor_type"),
                email=kwargs.get("email"),
            ))
            return json.dumps({
                "status": "ok",
                "vendor_id": str(vendor.id),
                "name": vendor.name,
                "message": f"Vendor '{vendor.name}' created successfully.",
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()


class SaveMemoryTool(Tool):
    """Save a note — either task-scoped or permanent entity context."""

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save a note. Use scope='task' (default) for task-specific observations "
            "like quotes, scheduling details, assessment findings. Use scope='entity' "
            "for permanent knowledge about an entity that applies across all tasks "
            "(vendor specialties, tenant preferences, property recurring issues)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The note to save (concise, one topic per note).",
                },
                "scope": {
                    "type": "string",
                    "enum": ["task", "entity"],
                    "description": "Where to save: 'task' for this task only (default), 'entity' for permanent entity knowledge.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (required when scope='task'). Use the Task ID from context.",
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["property", "unit", "tenant", "vendor", "general"],
                    "description": "Entity type (required when scope='entity').",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Entity ID (required when scope='entity').",
                },
                "entity_label": {
                    "type": "string",
                    "description": "Human-readable label for display.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        content = kwargs["content"]
        scope = kwargs.get("scope", "task")
        entity_type = kwargs.get("entity_type", "general")
        entity_id = kwargs.get("entity_id", "")
        entity_label = kwargs.get("entity_label", "")
        task_id = kwargs.get("task_id", "")

        from handlers.deps import SessionLocal
        from datetime import UTC, datetime

        # Task-scoped notes
        if scope == "task":
            if not task_id:
                return json.dumps({"status": "error", "message": "task_id is required for scope='task'"})
            db = SessionLocal.session_factory()
            try:
                from db.models import Task as TaskModel
                task = db.query(TaskModel).filter_by(id=task_id).first()
                if not task:
                    return json.dumps({"status": "error", "message": f"Task {task_id} not found"})
                now = datetime.now(UTC).strftime("%Y-%m-%d")
                entry = f"[{now}] {content}"
                existing = task.notes or ""
                task.notes = f"{existing}\n{entry}".strip()
                db.commit()
                return json.dumps({"status": "ok", "message": "Task note saved."})
            finally:
                db.close()

        if entity_type == "general" or not entity_id:
            # General notes go to agent_memory table
            from llm.memory_store import DbMemoryStore
            from backends.local_auth import DEFAULT_USER_ID
            store = DbMemoryStore(DEFAULT_USER_ID)
            store.add_note(content=content, entity_type="general", entity_id="", entity_label="")
            return json.dumps({"status": "ok", "message": "General note saved."})

        # Entity-scoped notes go directly to the entity's context column
        _MODEL_MAP = {
            "property": "Property",
            "unit": "Unit",
            "tenant": "Tenant",
            "vendor": "ExternalContact",
        }
        model_name = _MODEL_MAP.get(entity_type)
        if not model_name:
            return json.dumps({"status": "error", "message": f"Unknown entity type: {entity_type}"})

        db = SessionLocal.session_factory()
        try:
            import db.models as models
            model_cls = getattr(models, model_name)
            entity = db.query(model_cls).filter_by(id=entity_id).first()
            if not entity:
                return json.dumps({"status": "error", "message": f"{entity_type} {entity_id} not found"})

            # Append to existing context with timestamp
            now = datetime.now(UTC).strftime("%Y-%m-%d")
            entry = f"[{now}] {content}"
            existing = entity.context or ""
            entity.context = f"{existing}\n{entry}".strip()
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(entity, "context")
            db.commit()

            label = entity_label or entity_type
            return json.dumps({"status": "ok", "message": f"Context saved for {label}."})
        finally:
            db.close()


class RecallMemoryTool(Tool):
    """Read back stored context notes, optionally filtered by entity."""

    @property
    def name(self) -> str:
        return "recall_memory"

    @property
    def description(self) -> str:
        return (
            "Read your long-term memory notes. Optionally filter by entity "
            "type or specific entity ID. Returns all notes if no filter given."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["property", "unit", "tenant", "vendor", "general"],
                    "description": "Filter by entity type. Omit to get all notes.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Filter by specific entity ID. Omit to get all notes of the given type.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        entity_type = kwargs.get("entity_type")
        entity_id = kwargs.get("entity_id")

        if entity_type == "general" or (not entity_type and not entity_id):
            # Read general notes from agent_memory
            from llm.memory_store import DbMemoryStore
            from backends.local_auth import DEFAULT_USER_ID
            store = DbMemoryStore(DEFAULT_USER_ID)
            notes = store.get_notes(entity_type="general")
            if not notes:
                return json.dumps({"notes": [], "message": "No general notes found."})
            return json.dumps({"notes": notes, "count": len(notes)})

        # Read from entity context column
        _MODEL_MAP = {
            "property": "Property",
            "unit": "Unit",
            "tenant": "Tenant",
            "vendor": "ExternalContact",
        }
        model_name = _MODEL_MAP.get(entity_type or "")
        if not model_name:
            return json.dumps({"notes": [], "message": f"Unknown entity type: {entity_type}"})

        from handlers.deps import SessionLocal
        import db.models as models
        db = SessionLocal.session_factory()
        try:
            model_cls = getattr(models, model_name)
            if entity_id:
                entity = db.query(model_cls).filter_by(id=entity_id).first()
                entities = [entity] if entity else []
            else:
                entities = db.query(model_cls).filter(model_cls.context.isnot(None)).all()

            notes = []
            for e in entities:
                if e and e.context:
                    label = getattr(e, "name", None) or getattr(e, "label", None) or str(e.id)[:8]
                    notes.append({
                        "entity_type": entity_type,
                        "entity_id": str(e.id),
                        "label": label,
                        "context": e.context,
                    })
            if not notes:
                return json.dumps({"notes": [], "message": f"No {entity_type} context found."})
            return json.dumps({"notes": notes, "count": len(notes)})
        finally:
            db.close()


class EditMemoryTool(Tool):
    """Replace the entire context for an entity — use to compact, correct, or clean up notes."""

    @property
    def name(self) -> str:
        return "edit_memory"

    @property
    def description(self) -> str:
        return (
            "Replace the full context notes for an entity. Use this to remove stale "
            "entries, compact verbose notes, or correct mistakes. First call recall_memory "
            "to read the current notes, then call edit_memory with the cleaned-up version. "
            "Pass an empty string to clear all notes for an entity."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["entity_type", "entity_id", "new_context"],
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["property", "unit", "tenant", "vendor"],
                    "description": "Type of entity whose context to replace.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "ID of the entity.",
                },
                "new_context": {
                    "type": "string",
                    "description": "The full replacement context text. Pass empty string to clear.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        entity_type = kwargs["entity_type"]
        entity_id = kwargs["entity_id"]
        new_context = kwargs["new_context"]

        _MODEL_MAP = {
            "property": "Property",
            "unit": "Unit",
            "tenant": "Tenant",
            "vendor": "ExternalContact",
        }
        model_name = _MODEL_MAP.get(entity_type)
        if not model_name:
            return json.dumps({"status": "error", "message": f"Unknown entity type: {entity_type}"})

        from handlers.deps import SessionLocal
        db = SessionLocal.session_factory()
        try:
            import db.models as models
            model_cls = getattr(models, model_name)
            entity = db.query(model_cls).filter_by(id=entity_id).first()
            if not entity:
                return json.dumps({"status": "error", "message": f"{entity_type} {entity_id} not found"})

            entity.context = new_context.strip() or None
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(entity, "context")
            db.commit()

            label = getattr(entity, "name", None) or getattr(entity, "label", None) or entity_type
            action = "cleared" if not new_context.strip() else "updated"
            return json.dumps({"status": "ok", "message": f"Context {action} for {label}."})
        finally:
            db.close()
