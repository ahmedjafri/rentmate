"""Messaging tools: send messages to tenants or vendors on a task."""
import json
import logging
from datetime import UTC, datetime
from typing import Any

from db.enums import SuggestionOption

from llm.tools._common import (
    Tool,
    _auto_execute_suggestion,
    _create_suggestion,
    _load_tenant_by_public_id,
    _load_vendor_by_public_id,
    _placeholder_message_block_error,
    _resolve_task_id_from_active_conversation,
    _resolve_task_tenant,
    _sanitize_tenant_outbound_draft,
)

logger = logging.getLogger("rentmate.llm.message_person")


_VALID_RISK_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "critical"})

# Which risk levels route to a pending manager-review suggestion per the
# account's outbound-message policy. Everything NOT in this set auto-sends
# (and still writes an executed Suggestion row as an audit trail).
#
# This is the inverse of gql/services/settings_service.py::_MESSAGE_RISK_ALLOWLIST
# but written from the suggestion-side so the rule reads like the product spec:
#   - strict     → suggestion for medium/high/critical
#   - balanced   → suggestion for high/critical
#   - aggressive → suggestion for critical only
_SUGGESTION_REVIEW_RISKS: dict[str, frozenset[str]] = {
    "strict":     frozenset({"medium", "high", "critical"}),
    "balanced":   frozenset({"high", "critical"}),
    "aggressive": frozenset({"critical"}),
}


def _needs_manager_review(risk_level: str, outbound_policy: str) -> bool:
    return risk_level in _SUGGESTION_REVIEW_RISKS.get(
        outbound_policy, _SUGGESTION_REVIEW_RISKS["balanced"],
    )


_ENTITY_ID_PREFIXES = ("tenant ", "vendor ", "tenant:", "vendor:", "tenants/", "vendors/")


def _strip_entity_prefix(entity_id: str) -> str:
    """Agents sometimes copy the full context-line notation ("tenant <uuid>")
    into the tool call. Strip common prefixes so the UUID lookup succeeds
    instead of producing an unhelpful "not found" error.
    """
    text = (entity_id or "").strip()
    lowered = text.lower()
    for prefix in _ENTITY_ID_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix):].strip()
    return text


class MessageExternalPersonTool(Tool):
    """Send a message to an external person (tenant or vendor) on a task."""

    @property
    def name(self) -> str:
        return "message_person"

    @property
    def description(self) -> str:
        return (
            "Send a message to a tenant or vendor. Use the Tenant ID or Vendor ID external UUID "
            "from the context — you already have them, do not ask for contact info. "
            "You must classify risk_level yourself (low / medium / high / critical) based on the "
            "content and context of the message. Risky messages route to a manager-review suggestion "
            "(pending approval); safe ones auto-send under the account's outbound-message policy. "
            "Pass task_id when the message is about a specific task so the message threads into the "
            "task's coordination conversation. Omit task_id for standalone outreach (e.g. routine "
            "check-ins) — a new conversation with the person will be created."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["entity_id", "entity_type", "draft_message", "risk_level"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "ID of the task this message should be associated with. Optional — omit for "
                        "standalone outreach (creates a new conversation with the "
                        "recipient that isn't attached to any task)."
                    ),
                },
                "entity_id": {
                    "type": "string",
                    "description": (
                        "External UUID of the tenant or vendor — the bare UUID only, "
                        "no 'tenant ' or 'vendor ' prefix. Copy the part after "
                        "'Entity: tenant ' / 'Entity: vendor ' in the context."
                    ),
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["tenant", "vendor"],
                    "description": "Type of person to message",
                },
                "draft_message": {"type": "string", "description": "The message to send"},
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": (
                        "Classify the outbound message's risk. low = routine check-in, no consequences "
                        "if misread; medium = ordinary operational update; high = sensitive or "
                        "consequential (payment, maintenance access, dispute); critical = legally "
                        "binding or irreversible. Always required — the routing gate depends on this."
                    ),
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        agent_supplied_task_id = kwargs.get("task_id") or None
        task_id = agent_supplied_task_id
        entity_id = _strip_entity_prefix(str(kwargs["entity_id"]))
        entity_type = kwargs["entity_type"]
        draft_message = kwargs["draft_message"]
        risk_level = (kwargs.get("risk_level") or "").strip().lower()
        if risk_level not in _VALID_RISK_LEVELS:
            return json.dumps({
                "status": "error",
                "message": (
                    f"risk_level is required and must be one of "
                    f"{sorted(_VALID_RISK_LEVELS)}; got {kwargs.get('risk_level')!r}."
                ),
            })

        # Active conversation is ground truth: if the agent is responding
        # inside a task's AI conversation, the message belongs to that
        # task — override any agent-supplied task_id (it's frequently a
        # hallucination from a context window with multiple task ids) and
        # rescue forgetful agents that omit task_id entirely.
        active_task_id = _resolve_task_id_from_active_conversation()
        if active_task_id is not None:
            if (
                agent_supplied_task_id is not None
                and str(agent_supplied_task_id) != str(active_task_id)
            ):
                logger.warning(
                    "message_person task_id override: agent passed %s but "
                    "active conversation belongs to task %s — using active task",
                    agent_supplied_task_id, active_task_id,
                )
            task_id = active_task_id

        if task_id is not None:
            # Task.id is an integer PK; reject obvious placeholder values
            # ("current", "latest", etc.) up front so the query below doesn't
            # raise InvalidTextRepresentation and leave the session broken.
            try:
                int(str(task_id))
            except (TypeError, ValueError):
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"task_id must be the numeric id of a real task; got {task_id!r}. "
                        "Use the exact Task ID from the context you were given — "
                        "do not pass placeholders like 'current' or 'latest'. Omit "
                        "task_id entirely for standalone outreach."
                    ),
                })

        from db.models import ConversationType, Task
        from db.session import SessionLocal
        from gql.services import chat_service
        from gql.services.settings_service import get_action_policy_settings
        db = SessionLocal.session_factory()
        try:
            task = None
            if task_id is not None:
                task = db.query(Task).filter_by(id=str(task_id)).first()
                if not task:
                    return json.dumps({
                        "status": "error",
                        "message": f"Task {task_id} not found.",
                    })
            task_title = task.title if task else None

            if entity_type == "vendor":
                entity = _load_vendor_by_public_id(db, entity_id)
                entity_name = entity.name if entity else "Vendor"
                entity_phone = entity.phone if entity else None
            elif entity_type == "tenant":
                entity = _load_tenant_by_public_id(db, entity_id)
                if not entity and task_id is not None:
                    entity = _resolve_task_tenant(db, task_id)
                entity_name = entity.user.name if entity and entity.user else "Tenant"
                entity_phone = entity.user.phone if entity and entity.user else None
            else:
                return json.dumps({"status": "error", "message": f"Can only message tenants or vendors, not {entity_type}"})

            if not entity:
                return json.dumps({"status": "error", "message": f"{entity_type.title()} {entity_id} not found"})

            if entity_type == "tenant" and task_id is not None:
                draft_message = _sanitize_tenant_outbound_draft(
                    db,
                    task_id=task_id,
                    draft_message=draft_message,
                )
            placeholder_error = _placeholder_message_block_error(draft_message)
            if placeholder_error:
                return json.dumps({"status": "error", "message": placeholder_error})

            action_payload: dict[str, Any] = {
                "action": "message_person",
                "entity_id": entity_id,
                "entity_type": entity_type,
                "entity_name": entity_name,
                "entity_phone": entity_phone,
                "draft_message": draft_message,
                "risk_level": risk_level,
            }
            options = [
                SuggestionOption(key="send", label=f"Send to {entity_name}", action="message_person_send", variant="default"),
                SuggestionOption(key="edit", label="Edit Message", action="edit_message", variant="outline"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]

            outbound_policy = get_action_policy_settings()["outbound_messages"]
            needs_review = _needs_manager_review(risk_level, outbound_policy)
            suggestion_title = (
                f"Message {entity_name}: {task_title}" if task_title
                else f"Message {entity_name}"
            )

            # ── task-scoped path ─────────────────────────────────────────────
            if task_id is not None:
                sid = _create_suggestion(
                    title=suggestion_title,
                    ai_context=f"The agent wants to send a message to {entity_name} ({entity_type}).\n\nDraft message:\n{draft_message}",
                    options=options,
                    action_payload=action_payload,
                    task_id=task_id,
                )

                if needs_review:
                    return json.dumps({
                        "status": "ok",
                        "suggestion_id": sid,
                        "message": (
                            f"Message for {entity_name} staged for manager review "
                            f"(risk={risk_level} under '{outbound_policy}' policy)."
                        ),
                        "policy_reason": (
                            f"risk {risk_level} requires review under {outbound_policy} policy"
                        ),
                    })

                # Safe to auto-send. The Suggestion row we just created becomes
                # the executed audit trail once SuggestionExecutor finishes.
                err = _auto_execute_suggestion(sid, "message_person_send")
                if err:
                    return json.dumps({
                        "status": "error",
                        "suggestion_id": sid,
                        "message": (
                            f"Auto-send failed for {entity_name}: {err}. "
                            "Suggestion left pending for manual review."
                        ),
                    })
                note = (
                    f"Message sent to {entity_name} "
                    f"(auto-approved, risk={risk_level}, policy={outbound_policy})."
                )
                if not entity_phone:
                    note += " Note: no phone number on file, message saved but not delivered via SMS."
                return json.dumps({"status": "ok", "suggestion_id": sid, "message": note})

            # ── standalone path (no task_id) ────────────────────────────────
            if needs_review:
                # Review-only path: stage the Suggestion but do NOT create
                # the conversation yet. The conversation is materialised at
                # approval time (see MessagePersonSuggestionExecutor) so a
                # dismissed draft leaves zero orphaned state behind.
                sid = _create_suggestion(
                    title=suggestion_title,
                    ai_context=f"The agent wants to send a message to {entity_name} ({entity_type}).\n\nDraft message:\n{draft_message}",
                    options=options,
                    action_payload=action_payload,
                    task_id=None,
                )
                return json.dumps({
                    "status": "ok",
                    "suggestion_id": sid,
                    "message": (
                        f"Message for {entity_name} staged for manager review "
                        f"(risk={risk_level} under '{outbound_policy}' policy). "
                        "The conversation will be created if/when the manager "
                        "approves the draft."
                    ),
                    "policy_reason": (
                        f"risk {risk_level} requires review under {outbound_policy} policy"
                    ),
                })

            # Safe auto-send. Materialise the standalone conversation and
            # drop the message into it immediately — the message itself is
            # the audit trail, no Suggestion row needed on this branch.
            conv_type = (
                ConversationType.TENANT if entity_type == "tenant"
                else ConversationType.VENDOR
            )
            participant_kwargs = (
                {"tenant_id": entity.id} if entity_type == "tenant"
                else {"vendor_id": entity.id}
            )
            convo = chat_service.get_or_create_external_conversation(
                db,
                conversation_type=conv_type,
                subject=suggestion_title,
                **participant_kwargs,
            )
            chat_service.send_autonomous_message(
                db,
                conversation_id=convo.id,
                body=draft_message,
            )
            db.commit()
            note = (
                f"Message sent to {entity_name} in a new standalone "
                f"conversation (auto-approved, risk={risk_level}, "
                f"policy={outbound_policy})."
            )
            if not entity_phone:
                note += " Note: no phone number on file, message saved but not delivered via SMS."
            return json.dumps({
                "status": "ok",
                "conversation_id": str(convo.id),
                "message": note,
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
