"""RentMate agent tool classes.

Includes suggestion tools (propose_task, close_task, set_mode, attach_vendor).

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

        # Queue a suggestion message to be flushed after the AI reply so it
        # appears below the agent response in the conversation timeline.
        conv_id = active_conversation_id.get()
        if conv_id:
            body_parts = [title]
            if action_payload:
                if action_payload.get("vendor_name"):
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
        db = SessionLocal()
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


class AttachVendorTool(Tool):
    """Propose attaching a vendor conversation to a task, with an optional draft message."""

    @property
    def name(self) -> str:
        return "attach_vendor"

    @property
    def description(self) -> str:
        return (
            "Propose attaching a vendor/contractor conversation to an existing task. "
            "You MUST provide vendor_id — use lookup_vendors first to find the right "
            "vendor. An optional draft_message can be included for the manager to "
            "review and send to the vendor."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "vendor_id"],
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to attach a vendor conversation to"},
                "vendor_id": {"type": "string", "description": "ID of the vendor (use lookup_vendors to find this)"},
                "draft_message": {"type": "string", "description": "Optional draft message to send to the vendor on approval"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        vendor_id = kwargs["vendor_id"]
        draft_message = kwargs.get("draft_message")
        task_title = _get_task_title(task_id)

        from handlers.deps import SessionLocal
        from db.models import ExternalContact
        db = SessionLocal()
        try:
            vendor = db.query(ExternalContact).filter_by(id=vendor_id).first()
            vendor_name = vendor.name if vendor else "Vendor"
        finally:
            db.close()

        action_payload: dict = {
            "action": "attach_vendor",
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
        }
        if draft_message:
            action_payload["draft_message"] = draft_message

        if draft_message:
            options = [
                SuggestionOption(key="send", label="Send to Vendor", action="attach_vendor_send", variant="default"),
                SuggestionOption(key="edit", label="Edit Message", action="edit_draft", variant="outline"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]
            title = f"Contact {vendor_name} for: {task_title}"
            ai_context = f"The agent recommends contacting {vendor_name} about this task.\n\nDraft message:\n{draft_message}"
        else:
            options = [
                SuggestionOption(key="attach", label=f"Attach {vendor_name}", action="attach_vendor", variant="default"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]
            title = f"Attach {vendor_name} to: {task_title}"
            ai_context = f"The agent recommends assigning {vendor_name} to this task."

        sid = _create_suggestion(
            title=title,
            ai_context=ai_context,
            options=options,
            action_payload=action_payload,
            task_id=task_id,
        )
        return json.dumps({"status": "ok", "suggestion_id": sid, "message": f"Vendor suggestion for {vendor_name} created for manager review."})


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

        db = SessionLocal()
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

        db = SessionLocal()
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

        db = SessionLocal()
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
    """Save a context note for an entity (property, unit, tenant, vendor) or general."""

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save a context note to long-term memory. Notes persist across "
            "conversations. Attach notes to a specific entity (property, unit, "
            "tenant, vendor) or save as general memory. Use this to remember "
            "preferences, quirks, maintenance history, vendor reliability notes, "
            "or anything the property manager tells you to remember."
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
                "entity_type": {
                    "type": "string",
                    "enum": ["property", "unit", "tenant", "vendor", "general"],
                    "description": "What type of entity this note is about. Use 'general' for preferences and global notes.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "ID of the entity (property/unit/tenant/vendor). Required unless entity_type is 'general'.",
                },
                "entity_label": {
                    "type": "string",
                    "description": "Human-readable label (e.g. '16617 3rd Dr SE', 'Unit 3B', 'Handyman Rob'). Stored for display.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        content = kwargs["content"]
        entity_type = kwargs.get("entity_type", "general")
        entity_id = kwargs.get("entity_id", "")
        entity_label = kwargs.get("entity_label", "")

        from handlers.deps import SessionLocal
        from datetime import UTC, datetime

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

        db = SessionLocal()
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
        db = SessionLocal()
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
