"""Messaging tools: send messages to tenants or vendors on a task."""
import json
from typing import Any

from db.enums import SuggestionOption

from llm.tools._common import (
    Tool,
    _auto_execute_suggestion,
    _create_suggestion,
    _get_task_title,
    _load_tenant_by_public_id,
    _load_vendor_by_public_id,
    _resolve_task_tenant,
    _sanitize_tenant_outbound_draft,
)


class MessageExternalPersonTool(Tool):
    """Send a message to an external person (tenant or vendor) on a task."""

    @property
    def name(self) -> str:
        return "message_person"

    @property
    def description(self) -> str:
        return (
            "Send a message to a tenant or vendor on a task. Use the Tenant ID or Vendor ID external UUID "
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
                "entity_id": {"type": "string", "description": "External UUID of the tenant or vendor"},
                "entity_type": {
                    "type": "string",
                    "enum": ["tenant", "vendor"],
                    "description": "Type of person to message",
                },
                "draft_message": {"type": "string", "description": "The message to send"},
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Estimated outbound-message risk level. Default: medium.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        entity_id = str(kwargs["entity_id"])
        entity_type = kwargs["entity_type"]
        draft_message = kwargs["draft_message"]
        risk_level = kwargs.get("risk_level", "medium")
        task_title = _get_task_title(task_id)

        from db.session import SessionLocal
        db = SessionLocal.session_factory()
        try:
            if entity_type == "vendor":
                entity = _load_vendor_by_public_id(db, entity_id)
                entity_name = entity.name if entity else "Vendor"
                entity_phone = entity.phone if entity else None
            elif entity_type == "tenant":
                entity = _load_tenant_by_public_id(db, entity_id)
                if not entity:
                    entity = _resolve_task_tenant(db, task_id)
                entity_name = entity.user.name if entity and entity.user else "Tenant"
                entity_phone = entity.user.phone if entity and entity.user else None
            else:
                return json.dumps({"status": "error", "message": f"Can only message tenants or vendors, not {entity_type}"})

            if not entity:
                return json.dumps({"status": "error", "message": f"{entity_type.title()} {entity_id} not found"})
            if entity_type == "tenant":
                draft_message = _sanitize_tenant_outbound_draft(
                    db,
                    task_id=task_id,
                    draft_message=draft_message,
                )

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
                SuggestionOption(key="edit", label="Edit Message", action="edit_message", variant="outline"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]

            sid = _create_suggestion(
                title=f"Message {entity_name}: {task_title}",
                ai_context=f"The agent wants to send a message to {entity_name} ({entity_type}).\n\nDraft message:\n{draft_message}",
                options=options,
                action_payload=action_payload,
                task_id=task_id,
            )

            # Auto-execute when outbound-message policy allows the requested risk level.
            from llm.action_policy import ActionCandidate, evaluate_action_candidate

            decision = evaluate_action_candidate(ActionCandidate(
                action_class="outbound_message",
                action_name="message_person_send",
                risk_level=risk_level,
            ))
            if decision.allowed:
                err = _auto_execute_suggestion(sid, "message_person_send")
                if err:
                    return json.dumps({"status": "error", "suggestion_id": sid, "message": f"Failed to send message to {entity_name}: {err}. Suggestion saved for manual review."})
                note = f"Message sent to {entity_name} (auto-approved)."
                if not entity_phone:
                    note += " Note: no phone number on file, message saved but not delivered via SMS."
                return json.dumps({"status": "ok", "suggestion_id": sid, "message": note})

            return json.dumps({
                "status": "ok",
                "suggestion_id": sid,
                "message": f"Message suggestion for {entity_name} created for manager review.",
                "policy_reason": decision.reason,
            })
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            raise
        finally:
            db.close()


__all__ = ["MessageExternalPersonTool"]
