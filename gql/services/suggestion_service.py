"""Service for creating and acting on Suggestions."""
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Suggestion, Task, Conversation, ConversationType,
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
    body: str,
    category: str | None = None,
    urgency: str | None = None,
    source: str = "automation",
    automation_key: str | None = None,
    options: list[dict] | None = None,
    action_payload: dict | None = None,
    property_id: str | None = None,
    unit_id: str | None = None,
) -> Suggestion:
    """Create a Suggestion with its own AI conversation thread."""
    now = datetime.now(UTC)
    account_id = _get_account_id(sess, property_id, unit_id)

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
        body=body,
        category=category,
        urgency=urgency,
        source=source,
        automation_key=automation_key,
        status="pending",
        options=options,
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
    if body:
        sess.add(Message(
            conversation_id=ai_convo.id,
            sender_type=PT.ACCOUNT_USER,
            body=body,
            message_type=MessageType.CONTEXT,
            sender_name="RentMate",
            is_ai=True,
            is_system=False,
            sent_at=now,
        ))
        sess.flush()

    return suggestion


def add_message(
    sess: Session,
    suggestion_id: str,
    body: str,
    message_type: str = MessageType.MESSAGE,
    sender_name: str = "RentMate",
    is_ai: bool = True,
    draft_reply: str | None = None,
) -> Message:
    """Add a message to a suggestion's AI conversation."""
    suggestion = sess.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    ).scalar_one_or_none()
    if not suggestion:
        raise ValueError(f"Suggestion {suggestion_id} not found")
    if not suggestion.ai_conversation_id:
        raise ValueError(f"No AI conversation for suggestion {suggestion_id}")

    now = datetime.now(UTC)
    msg = Message(
        conversation_id=suggestion.ai_conversation_id,
        sender_type=PT.ACCOUNT_USER,
        body=body,
        message_type=message_type,
        sender_name=sender_name,
        is_ai=is_ai,
        is_system=False,
        draft_reply=draft_reply,
        sent_at=now,
    )
    sess.add(msg)
    sess.flush()
    return msg


def act_on_suggestion(
    sess: Session,
    suggestion_id: str,
    action: str,
    edited_body: str | None = None,
) -> tuple[Suggestion, Optional[Task]]:
    """Execute the chosen action on a suggestion.

    Returns the updated Suggestion and optionally the created Task.
    """
    from gql.services.task_service import TaskService
    from gql.services import chat_service
    from gql.types import CreateTaskInput

    suggestion = sess.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    ).scalar_one_or_none()
    if not suggestion:
        raise ValueError(f"Suggestion {suggestion_id} not found")
    if suggestion.status != "pending":
        raise ValueError(f"Suggestion {suggestion_id} is already {suggestion.status}")

    now = datetime.now(UTC)
    payload = suggestion.action_payload or {}
    task = None

    if action in ("accept_task", "approve_draft"):
        # Create a real Task from this suggestion
        task = TaskService.create_task(sess, CreateTaskInput(
            title=suggestion.title or "",
            source=suggestion.source or "automation",
            task_status="active",
            task_mode="manual",
            category=suggestion.category,
            urgency=suggestion.urgency,
            priority="routine",
            property_id=suggestion.property_id,
            unit_id=suggestion.unit_id,
        ))

        # Reassign the AI conversation from suggestion to task
        if suggestion.ai_conversation_id:
            ai_convo = sess.get(Conversation, suggestion.ai_conversation_id)
            if ai_convo:
                ai_convo.conversation_type = ConversationType.TASK_AI
            task.ai_conversation_id = suggestion.ai_conversation_id
            suggestion.ai_conversation_id = None

        # Wire up vendor conversation if applicable
        vendor_id = payload.get("vendor_id")
        if vendor_id:
            ext_convo = chat_service.get_or_create_external_conversation(
                sess,
                conversation_type=ConversationType.VENDOR,
                subject=suggestion.title or "",
                property_id=suggestion.property_id,
                unit_id=suggestion.unit_id,
                vendor_id=vendor_id,
            )
            task.external_conversation_id = ext_convo.id
            TaskService.assign_vendor_to_task(sess, task.id, vendor_id)

        # Handle draft message actions
        draft = edited_body or payload.get("draft_message")
        if action == "approve_draft" and draft and task.external_conversation_id:
            chat_service.send_autonomous_message(
                sess, task.external_conversation_id, draft, task_id=task.id,
            )

        suggestion.task_id = task.id
        suggestion.status = "accepted"

    elif action == "reject_task":
        suggestion.status = "dismissed"

    else:
        raise ValueError(f"Unknown action: {action}")

    suggestion.action_taken = action
    suggestion.acted_at = now
    suggestion.updated_at = now
    sess.flush()

    return suggestion, task


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
