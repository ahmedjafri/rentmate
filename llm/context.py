from sqlalchemy.orm import Session

from db.models import Conversation, MessageType, Task
from llm.retrieval import RetrievalRequest, compose_prompt_context, retrieve_context


def load_account_context(db: Session, query: str | None = None) -> str:
    bundle = retrieve_context(db, RetrievalRequest(
        surface="chat",
        intent="account_overview",
        query=query or "account overview active leases properties tenants vendors important notes",
        limit=12,
    ))
    return compose_prompt_context(bundle, title="Account overview")


def _resolve_task(db: Session, task_or_conversation_id: str) -> Task | None:
    task = db.query(Task).filter_by(id=task_or_conversation_id).first()
    if task:
        return task
    conversation = db.query(Conversation).filter_by(id=task_or_conversation_id).first()
    if conversation:
        return (
            db.query(Task)
            .filter(
                (Task.ai_conversation_id == conversation.id)
                | (Task.parent_conversation_id == conversation.id)
                | (Task.external_conversation_id == conversation.id)
            )
            .order_by(Task.id.asc())
            .first()
        )
    return None


def build_task_context(db: Session, task_id: str, query: str | None = None) -> str:
    """Build a retrieval-driven context string for a task or linked conversation."""
    task = _resolve_task(db, task_id)
    if not task:
        return load_account_context(db, query=query)

    bundle = retrieve_context(db, RetrievalRequest(
        surface="task",
        intent="task_context",
        query=query or task.title or f"task {task.id}",
        task_id=str(task.id),
        property_id=str(task.property_id) if task.property_id else None,
        unit_id=str(task.unit_id) if task.unit_id else None,
        limit=14,
    ))

    lines = [
        f"Task ID: {task.id}",
        f"Task: {task.title}",
        f"Category: {task.category or 'general'}",
        f"Urgency: {task.urgency or 'normal'}",
        f"Status: {task.task_status or 'active'}",
        f"Mode: {task.task_mode or 'manual'}",
    ]

    ai_convo = task.ai_conversation
    all_msgs = list(ai_convo.messages) if ai_convo else []
    context_msgs = [m for m in all_msgs if m.message_type == MessageType.CONTEXT]
    if context_msgs:
        lines.append(f"Description: {context_msgs[0].body}")

    if task.context:
        lines.append("")
        lines.append("Task notes:")
        lines.append(task.context)

    from db.models import Suggestion
    pending_suggestions = db.query(Suggestion).filter(
        Suggestion.task_id == task.id,
        Suggestion.status == "pending",
    ).all()
    if pending_suggestions:
        lines.append("")
        lines.append("Pending suggestions (already queued, do NOT duplicate):")
        for s in pending_suggestions:
            action = (s.action_payload or {}).get("action", "unknown")
            draft = (s.action_payload or {}).get("draft_message", "")
            entry = f"  - [{action}] {s.title or 'untitled'}"
            if draft:
                entry += f" — draft: {draft[:80]}"
            lines.append(entry)

    ranked_block = compose_prompt_context(bundle, title="Ranked context")
    if ranked_block:
        lines.append("")
        lines.append(ranked_block)

    return "\n".join(lines)


def build_vendor_safe_context(db: Session, task_id: str) -> str:
    """Build context for vendor-facing communications with tenant PII stripped.

    Includes only: property address, unit label, task details, category, urgency.
    Excludes: tenant names, emails, phones, lease dates, rent, payment status.
    """
    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        return ""

    lines = [
        f"Task: {task.title}",
        f"Category: {task.category or 'general'}",
        f"Urgency: {task.urgency or 'normal'}",
    ]

    ai_convo = task.ai_conversation
    all_msgs = list(ai_convo.messages) if ai_convo else []
    context_msgs = [m for m in all_msgs if m.message_type == MessageType.CONTEXT]
    if context_msgs:
        lines.append(f"Description: {context_msgs[0].body}")

    if task.property_id:
        prop = db.query(Property).filter_by(id=task.property_id).first()
        if prop:
            parts = [prop.address_line1, prop.city, prop.state, prop.postal_code]
            addr = ", ".join(p for p in parts if p)
            lines.append(f"Property: {addr}")

    if task.unit_id:
        unit = db.query(Unit).filter_by(id=task.unit_id).first()
        if unit:
            lines.append(f"Unit: {unit.label}")

    return "\n".join(lines)
