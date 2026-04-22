"""Memory tools: save, recall, edit entity and task notes."""
import json
from typing import Any

from backends.local_auth import resolve_account_id

from llm.tools._common import Tool, _load_entity_by_public_id, _public_entity_id


class SaveMemoryTool(Tool):
    """Save a note — either task-scoped or permanent entity context."""

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save a note. Use scope='task' for task-specific observations, "
            "scope='entity' for permanent entity knowledge. "
            "For entity notes, set visibility: 'private' (default) for account-specific "
            "observations/assessments only your account can see; 'shared' for objective "
            "facts visible to all accounts (lease terms, property features, extraction data). "
            "When unsure, use private. When processing documents, save factual summaries "
            "as shared and your assessments as private."
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
                "visibility": {
                    "type": "string",
                    "enum": ["private", "shared"],
                    "description": "For entity scope: 'private' (default) = only this account sees it; 'shared' = all accounts see it.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (required when scope='task'). Use the Task ID from context.",
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["property", "unit", "tenant", "vendor", "document", "general"],
                    "description": "Entity type (required when scope='entity').",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Entity external UUID when available (required when scope='entity').",
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

        from datetime import UTC, datetime

        from db.session import SessionLocal

        from llm.tools._common import tool_session

        # Task-scoped notes
        if scope == "task":
            if not task_id:
                return json.dumps({"status": "error", "message": "task_id is required for scope='task'"})
            with tool_session() as db:
                from db.models import Task as TaskModel
                task = db.query(TaskModel).filter_by(id=task_id).first()
                if not task:
                    return json.dumps({"status": "error", "message": f"Task {task_id} not found"})
                now = datetime.now(UTC).strftime("%Y-%m-%d")
                entry = f"[{now}] {content}"
                existing = task.context or ""
                task.context = f"{existing}\n{entry}".strip()
            return json.dumps({"status": "ok", "message": "Task note saved."})

        if entity_type == "general" or not entity_id:
            # General notes go to agent_memory table
            from llm.memory_store import DbMemoryStore
            store = DbMemoryStore(str(resolve_account_id()))
            store.add_note(content=content, entity_type="general", entity_id="", entity_label="")
            return json.dumps({"status": "ok", "message": "General note saved."})

        visibility = kwargs.get("visibility", "private")

        _VALID_ENTITY_TYPES = {"property", "unit", "tenant", "vendor", "document"}
        if entity_type not in _VALID_ENTITY_TYPES:
            return json.dumps({"status": "error", "message": f"Unknown entity type: {entity_type}"})

        with tool_session() as db:
            now = datetime.now(UTC)
            now_str = now.strftime("%Y-%m-%d")
            entry = f"[{now_str}] {content}"
            label = entity_label or entity_type

            if visibility == "shared":
                # Write to entity.context (visible to all accounts)
                _MODEL_MAP = {
                    "property": "Property",
                    "unit": "Unit",
                    "tenant": "Tenant",
                    "vendor": "User",
                    "document": "Document",
                }
                entity = _load_entity_by_public_id(db, entity_type, entity_id)
                if not entity:
                    return json.dumps({"status": "error", "message": f"{entity_type} {entity_id} not found"})
                existing = entity.context or ""
                entity.context = f"{existing}\n{entry}".strip()
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(entity, "context")
                return json.dumps({"status": "ok", "message": f"Shared context saved for {label}."})
            else:
                # Write to EntityNote (private to this account)
                from db.models import EntityNote
                creator_id = resolve_account_id()
                note_entity_id = str(entity_id)
                note = db.query(EntityNote).filter_by(
                    creator_id=creator_id, entity_type=entity_type, entity_id=note_entity_id,
                ).first()
                if note:
                    existing = note.content or ""
                    note.content = f"{existing}\n{entry}".strip()
                    note.updated_at = now
                else:
                    note = EntityNote(
                        creator_id=creator_id,
                        entity_type=entity_type,
                        entity_id=note_entity_id,
                        content=entry,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(note)
                return json.dumps({"status": "ok", "message": f"Private note saved for {label}."})


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
                    "enum": ["property", "unit", "tenant", "vendor", "document", "general"],
                    "description": "Filter by entity type. Omit to get all notes.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Filter by specific entity external UUID when available. Omit to get all notes of the given type.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        entity_type = kwargs.get("entity_type")
        entity_id = kwargs.get("entity_id")

        if entity_type == "general" or (not entity_type and not entity_id):
            from llm.memory_store import DbMemoryStore
            store = DbMemoryStore(str(resolve_account_id()))
            notes = store.get_notes(entity_type="general")
            if not notes:
                return json.dumps({"notes": [], "message": "No general notes found."})
            return json.dumps({"notes": notes, "count": len(notes)})

        _MODEL_MAP = {
            "property": "Property",
            "unit": "Unit",
            "tenant": "Tenant",
            "vendor": "User",
            "document": "Document",
        }
        model_name = _MODEL_MAP.get(entity_type or "")
        if not model_name:
            return json.dumps({"notes": [], "message": f"Unknown entity type: {entity_type}"})

        import db.models as models
        from db.models import EntityNote
        from db.session import SessionLocal
        db = SessionLocal.session_factory()
        try:
            model_cls = getattr(models, model_name)
            creator_id = resolve_account_id()

            if entity_id:
                entity = _load_entity_by_public_id(db, entity_type, entity_id)
                entities = [entity] if entity else []
            else:
                entities = db.query(model_cls).all()

            results = []
            for e in entities:
                if not e:
                    continue
                label = getattr(e, "name", None) or getattr(e, "label", None) or str(e.id)[:8]
                shared = e.context or ""
                # Get private notes for this creator
                public_entity_id = _public_entity_id(e)
                private_note = db.query(EntityNote).filter_by(
                    creator_id=creator_id, entity_type=entity_type, entity_id=public_entity_id,
                ).first()
                private = private_note.content if private_note else ""
                if shared or private:
                    results.append({
                        "entity_type": entity_type,
                        "entity_id": public_entity_id,
                        "label": label,
                        "shared_context": shared,
                        "private_notes": private,
                    })
            if not results:
                return json.dumps({"notes": [], "message": f"No {entity_type} context found."})
            return json.dumps({"notes": results, "count": len(results)})
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
                    "enum": ["property", "unit", "tenant", "vendor", "document"],
                    "description": "Type of entity whose context to replace.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "External UUID of the entity when available.",
                },
                "new_context": {
                    "type": "string",
                    "description": "The full replacement context text. Pass empty string to clear.",
                },
                "visibility": {
                    "type": "string",
                    "enum": ["private", "shared"],
                    "description": "'private' (default) edits your account's notes; 'shared' edits the shared context visible to all.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        entity_type = kwargs["entity_type"]
        entity_id = kwargs["entity_id"]
        new_context = kwargs["new_context"]
        visibility = kwargs.get("visibility", "private")

        _VALID = {"property", "unit", "tenant", "vendor", "document"}
        if entity_type not in _VALID:
            return json.dumps({"status": "error", "message": f"Unknown entity type: {entity_type}"})

        from llm.tools._common import tool_session
        with tool_session() as db:
            if visibility == "shared":
                _MODEL_MAP = {
                    "property": "Property",
                    "unit": "Unit",
                    "tenant": "Tenant",
                    "vendor": "User",
                    "document": "Document",
                }
                entity = _load_entity_by_public_id(db, entity_type, entity_id)
                if not entity:
                    return json.dumps({"status": "error", "message": f"{entity_type} {entity_id} not found"})
                entity.context = new_context.strip() or None
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(entity, "context")
                label = getattr(entity, "name", None) or getattr(entity, "label", None) or entity_type
                action = "cleared" if not new_context.strip() else "updated"
                return json.dumps({"status": "ok", "message": f"Shared context {action} for {label}."})
            else:
                from datetime import UTC, datetime

                from db.models import EntityNote
                creator_id = resolve_account_id()
                note = db.query(EntityNote).filter_by(
                    creator_id=creator_id, entity_type=entity_type, entity_id=entity_id,
                ).first()
                if new_context.strip():
                    if note:
                        note.content = new_context.strip()
                        note.updated_at = datetime.now(UTC)
                    else:
                        db.add(EntityNote(
                            creator_id=creator_id,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            content=new_context.strip(),
                            created_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                        ))
                elif note:
                    db.delete(note)
                action = "cleared" if not new_context.strip() else "updated"
                return json.dumps({"status": "ok", "message": f"Private notes {action}."})


__all__ = ["SaveMemoryTool", "RecallMemoryTool", "EditMemoryTool"]
