"""Service for creating and acting on Suggestions."""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.enums import (
    TaskCategory, Urgency,
    AutomationSource, AgentSource, SuggestionSource, SuggestionOption,
)
from db.models import (
    Suggestion, Conversation, ConversationType,
    Message, MessageType, ParticipantType as PT,
)


def _get_account_id(sess: Session, property_id: str | None, unit_id: str | None) -> str:
    from sqlalchemy import text
    try:
        if property_id:
            res = sess.execute(text("SELECT account_id FROM properties WHERE id = :id"), {"id": property_id}).fetchone()
            if res and res[0]:
                return res[0]
        if unit_id:
            res = sess.execute(text("SELECT account_id FROM units WHERE id = :id"), {"id": unit_id}).fetchone()
            if res and res[0]:
                return res[0]
    except Exception:
        pass
    return "00000000-0000-0000-0000-000000000001"


def create_suggestion(
    sess: Session,
    *,
    title: str,
    ai_context: str,
    category: TaskCategory | None = None,
    urgency: Urgency | None = None,
    source: SuggestionSource = AutomationSource(automation_key=""),
    options: list[SuggestionOption] | None = None,
    action_payload: dict | None = None,
    property_id: str | None = None,
    unit_id: str | None = None,
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
        action_payload: Arbitrary context needed to execute the chosen action.  Common
            keys: ``vendor_id``, ``vendor_name`` (to wire up a vendor conversation on
            accept), ``draft_message`` (pre-written outreach text for approve_draft).
        property_id: Scoping FK — the property this suggestion relates to.
        unit_id: Scoping FK — the unit this suggestion relates to.
    """
    now = datetime.now(UTC)
    account_id = _get_account_id(sess, property_id, unit_id)

    # Decompose source union into DB columns
    if isinstance(source, AutomationSource):
        source_str = "automation"
        automation_key = source.automation_key or None
    else:
        source_str = "agent"
        automation_key = None

    # Serialize options to dicts for JSON storage
    options_dicts = [
        {"key": o.key, "label": o.label, "action": o.action, "variant": o.variant}
        for o in options
    ] if options else None

    # Create AI conversation for this suggestion
    ai_convo = Conversation(
        subject=title,
        property_id=property_id,
        unit_id=unit_id,
        conversation_type=ConversationType.SUGGESTION_AI,
        is_group=False,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    sess.add(ai_convo)
    sess.flush()

    suggestion = Suggestion(
        account_id=account_id,
        title=title,
        body=ai_context,
        category=category,
        urgency=urgency,
        source=source_str,
        automation_key=automation_key,
        status="pending",
        options=options_dicts,
        action_payload=action_payload,
        property_id=property_id,
        unit_id=unit_id,
        ai_conversation_id=ai_convo.id,
        created_at=now,
        updated_at=now,
    )
    sess.add(suggestion)
    sess.flush()

    # Add context message to AI conversation
    if ai_context:
        sess.add(Message(
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
        action: One of "accept_task", "approve_draft", "reject_task".
        task_id: Optional task ID to link to the suggestion on accept.
    """
    suggestion = sess.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    ).scalar_one_or_none()
    if not suggestion:
        raise ValueError(f"Suggestion {suggestion_id} not found")
    if suggestion.status != "pending":
        raise ValueError(f"Suggestion {suggestion_id} is already {suggestion.status}")

    now = datetime.now(UTC)

    if action in ("accept_task", "approve_draft"):
        suggestion.status = "accepted"
        if task_id:
            suggestion.task_id = task_id
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
    status: str | None = None,
    limit: int = 50,
) -> list[Suggestion]:
    """Fetch suggestions, optionally filtered by status."""
    q = select(Suggestion).order_by(Suggestion.created_at.desc()).limit(limit)
    if status:
        q = q.where(Suggestion.status == status)
    return list(sess.execute(q).scalars().all())
