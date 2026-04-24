"""Service for creating and acting on Suggestions."""
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from db.id_utils import normalize_optional_id
from db.enums import (
    AutomationSource,
    SuggestionOption,
    SuggestionSource,
    TaskCategory,
    Urgency,
    parse_urgency,
)
from db.models import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
    ParticipantType as PT,
    Suggestion,
)
from gql.services.number_allocator import NumberAllocator


class SuggestionOptionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    action: str
    variant: str


class SuggestionActionPayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str


class SendMsgAndCreateTaskPayload(SuggestionActionPayloadBase):
    action: Literal["send_and_create_task", "edit_message"]
    vendor_id: str | int
    vendor_name: str | None = None
    draft_message: str | None = None
    steps: list[dict] | None = None


class MessagePersonPayload(SuggestionActionPayloadBase):
    action: Literal["message_person"]
    entity_id: str | int
    entity_type: str
    entity_name: str | None = None
    entity_phone: str | None = None
    draft_message: str
    risk_level: Literal["low", "medium", "high", "critical"] | None = None
    # Populated for standalone (no-task) suggestions so the manager can
    # trace the suggestion back to its conversation.
    conversation_id: str | int | None = None


class RequestFileUploadPayload(SuggestionActionPayloadBase):
    action: Literal["request_file_upload"]
    requested_file_kind: str
    requested_file_label: str
    instructions: str
    target_task_id: str | int
    target_tenant_id: str | int | None = None
    property_id: str | None = None
    unit_id: str | None = None


SuggestionActionPayload = Annotated[
    SendMsgAndCreateTaskPayload | MessagePersonPayload | RequestFileUploadPayload,
    Field(discriminator="action"),
]
_ACTION_PAYLOAD_ADAPTER = TypeAdapter(SuggestionActionPayload)


def dump_suggestion_options(options: list[SuggestionOption | SuggestionOptionRecord] | None) -> list[dict] | None:
    if not options:
        return None
    records = [
        option if isinstance(option, SuggestionOptionRecord) else SuggestionOptionRecord.model_validate(option.__dict__)
        for option in options
    ]
    return [record.model_dump(exclude_none=True) for record in records]


def coerce_action_payload(action_payload: BaseModel | dict | None) -> dict | None:
    if action_payload is None:
        return None
    if isinstance(action_payload, BaseModel):
        return action_payload.model_dump(exclude_none=True)

    data = dict(action_payload)
    if "action" not in data:
        if "entity_type" in data and "entity_id" in data and data.get("draft_message"):
            data["action"] = "message_person"
        elif "vendor_id" in data:
            data["action"] = "send_and_create_task"
    return _ACTION_PAYLOAD_ADAPTER.validate_python(data).model_dump(exclude_none=True)


def _get_creator_id(sess: Session, property_id: str | None, unit_id: str | None) -> int:
    property_id = normalize_optional_id(property_id)
    unit_id = normalize_optional_id(unit_id)
    try:
        if property_id:
            res = sess.execute(text("SELECT creator_id FROM properties WHERE id = :id"), {"id": property_id}).fetchone()
            if res and res[0]:
                return res[0]
        if unit_id:
            res = sess.execute(text("SELECT creator_id FROM units WHERE id = :id"), {"id": unit_id}).fetchone()
            if res and res[0]:
                return res[0]
    except Exception:
        pass
    return resolve_account_id()


def create_suggestion(
    sess: Session,
    *,
    title: str,
    ai_context: str,
    category: TaskCategory | None = None,
    urgency: Urgency | None = None,
    source: SuggestionSource = AutomationSource(automation_key=""),
    options: list[SuggestionOption | SuggestionOptionRecord] | None = None,
    action_payload: BaseModel | dict | None = None,
    property_id: str | None = None,
    unit_id: str | None = None,
    risk_score: int | None = None,
    suggestion_type: str | None = None,
) -> Suggestion:
    """Create a Suggestion with its own AI conversation thread.

    Args:
        title: Short headline shown in the action desk (e.g. "Lease expiring: Jane – Unit 4B").
        ai_context: Longer context that seeds the suggestion's AI conversation thread.
        category: Task category used for autonomy-level lookup and routing.
        urgency: How urgent the proposed action is.
        source: What created this suggestion. Pass ``AutomationSource(automation_key=...)``
            for automation-generated suggestions or ``AgentSource()`` for AI agent ones.
            The automation key is used for per-check config lookup and deduplication.
        options: Action buttons rendered in the suggestion UI.
            Built by ``settings_service.build_suggestion_options()``.
        action_payload: Arbitrary context needed to execute the chosen action. Common
            keys: ``vendor_id``, ``vendor_name`` and ``draft_message`` for the
            send-and-create-task flow.
        property_id: Scoping FK — the property this suggestion relates to.
        unit_id: Scoping FK — the unit this suggestion relates to.
    """
    now = datetime.now(UTC)
    property_id = normalize_optional_id(property_id)
    unit_id = normalize_optional_id(unit_id)
    creator_id = _get_creator_id(sess, property_id, unit_id)

    # Decompose source union into DB columns
    if isinstance(source, AutomationSource):
        source_str = "automation"
        automation_key = source.automation_key or None
    else:
        source_str = "agent"
        automation_key = None

    # Serialize options to dicts for JSON storage
    options_dicts = dump_suggestion_options(options)

    # Create AI conversation for this suggestion
    ai_convo = Conversation(
        org_id=resolve_org_id(),
        subject=title,
        property_id=property_id,
        creator_id=resolve_account_id(),
        unit_id=unit_id,
        conversation_type=ConversationType.SUGGESTION_AI,
        is_group=False,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    sess.add(ai_convo)
    sess.flush()

    org_id = resolve_org_id()
    next_id = NumberAllocator.allocate_next(sess, entity_type="suggestion", org_id=org_id)
    suggestion = Suggestion(
        id=next_id,
        org_id=org_id,
        creator_id=creator_id,
        title=title,
        body=ai_context,
        category=category,
        urgency=parse_urgency(urgency),
        source=source_str,
        automation_key=automation_key,
        status="pending",
        options=options_dicts,
        action_payload=coerce_action_payload(action_payload),
        property_id=property_id,
        unit_id=unit_id,
        ai_conversation_id=ai_convo.id,
        suggestion_type=suggestion_type or category or "maintenance",
        risk_score=risk_score if risk_score is not None else 5,
        created_at=now,
        updated_at=now,
    )
    sess.add(suggestion)
    sess.flush()

    # Add context message to AI conversation
    if ai_context:
        sess.add(Message(
            org_id=resolve_org_id(),
            conversation_id=ai_convo.id,
            sender_type=PT.ACCOUNT_USER,
            body=ai_context,
            message_type=MessageType.CONTEXT,
            sender_name="RentMate",
            is_ai=True,
            is_system=False,
            sent_at=now,
        ))
        sess.flush()

    return suggestion


def act_on_suggestion(
    sess: Session,
    suggestion_id: str,
    action: str,
    *,
    task_id: str | None = None,
) -> Suggestion:
    """Mark a suggestion as accepted or dismissed.

    The caller (handler layer) is responsible for task creation,
    conversation wiring, and message sending before calling this.

    Args:
        suggestion_id: The suggestion to act on.
        action: The action key from the suggestion's options (e.g.
            "send_and_create_task", "edit_message", "reject_task", "close_task").
        task_id: Optional task ID to link to the suggestion on accept.
    """
    suggestion = sess.execute(
        select(Suggestion).where(
            Suggestion.id == suggestion_id,
            Suggestion.org_id == resolve_org_id(),
            Suggestion.creator_id == resolve_account_id(),
        )
    ).scalar_one_or_none()
    if not suggestion:
        raise ValueError(f"Suggestion {suggestion_id} not found")
    if suggestion.status != "pending":
        raise ValueError(f"Suggestion {suggestion_id} is already {suggestion.status}")

    now = datetime.now(UTC)

    if action in ("send_and_create_task", "edit_message", "close_task", "message_person_send"):
        suggestion.status = "accepted"
        if task_id:
            suggestion.task_id = int(task_id)
    elif action == "reject_task":
        suggestion.status = "dismissed"
    else:
        raise ValueError(f"Unknown action: {action}")

    suggestion.action_taken = action
    suggestion.acted_at = now
    suggestion.updated_at = now
    sess.flush()

    return suggestion


def get_suggestions(
    sess: Session,
    *, status: str | None = None,
    limit: int = 50,
) -> list[Suggestion]:
    """Fetch suggestions, optionally filtered by status."""
    q = select(Suggestion).order_by(Suggestion.created_at.desc()).limit(limit)
    q = q.where(
        Suggestion.org_id == resolve_org_id(),
        Suggestion.creator_id == resolve_account_id(),
    )
    if status:
        q = q.where(Suggestion.status == status)
    return list(sess.execute(q).scalars().all())
