from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from db.models import Conversation, ConversationType, Lease, MessageType, Property, Suggestion, Task, Tenant, Unit
from llm.retrieval import RetrievalRequest, compose_prompt_context, retrieve_context


def _serialize_retrieval_bundle(bundle) -> dict[str, Any]:
    return {
        "request": {
            "surface": bundle.request.surface,
            "intent": bundle.request.intent,
            "query": bundle.request.query,
            "task_id": bundle.request.task_id,
            "property_id": bundle.request.property_id,
            "unit_id": bundle.request.unit_id,
            "tenant_id": bundle.request.tenant_id,
            "vendor_id": bundle.request.vendor_id,
            "limit": bundle.request.limit,
        },
        "items": [
            {
                "memory_item_id": item.memory_item_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "title": item.title,
                "content": item.content,
                "metadata": item.metadata,
                "heuristic_score": item.heuristic_score,
                "vector_score": item.vector_score,
                "final_score": item.final_score,
                "reasons": item.reasons,
            }
            for item in bundle.items
        ],
    }


def load_account_context_data(db: Session, query: str | None = None) -> dict[str, Any]:
    bundle = retrieve_context(db, RetrievalRequest(
        surface="chat",
        intent="account_overview",
        query=query or "account overview active leases properties tenants vendors important notes",
        limit=12,
    ))
    rendered = compose_prompt_context(bundle, title="Account overview")
    return {
        "scope": "account",
        "query": query,
        "text": rendered,
        "sections": [
            {
                "section_type": "retrieval",
                "title": "Account overview",
                "content": rendered,
                "origin": {"kind": "retrieval"},
                "retrieval": _serialize_retrieval_bundle(bundle),
            }
        ] if rendered else [],
        "retrieval": _serialize_retrieval_bundle(bundle),
    }


def load_account_context(db: Session, query: str | None = None) -> str:
    return load_account_context_data(db, query=query)["text"]


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
                | (Task.id == conversation.parent_task_id)
            )
            .order_by(Task.id.asc())
            .first()
        )
    return None


def _label_task_conversation(conv: Conversation) -> str:
    convo_type = getattr(conv, "conversation_type", None)
    if convo_type == ConversationType.TASK_AI:
        return "AI conversation"
    if convo_type == ConversationType.SUGGESTION_AI:
        return "Suggestion AI conversation"
    if convo_type == ConversationType.TENANT:
        return "Tenant conversation"
    if convo_type == ConversationType.VENDOR:
        return "Vendor conversation"
    if convo_type == ConversationType.USER_AI:
        return "AI conversation"
    return "Linked conversation"


def build_task_conversation_transcripts(task: Task, *, limit: int = 20) -> tuple[str, list[dict[str, Any]]]:
    """Render recent linked-conversation transcripts for a task.

    Includes the task AI thread plus any tenant/vendor coordination threads.
    Returns ``(rendered_text, sections)`` where sections carry per-conversation
    provenance for trace/debug surfaces.
    """
    conversations: list[Conversation] = []
    seen: set[int] = set()

    def _add(conversation: Conversation | None) -> None:
        if not conversation or getattr(conversation, "id", None) is None:
            return
        cid = int(conversation.id)
        if cid in seen:
            return
        seen.add(cid)
        conversations.append(conversation)

    _add(getattr(task, "ai_conversation", None))
    _add(getattr(task, "parent_conversation", None))
    for convo in list(getattr(task, "external_conversations", []) or []):
        _add(convo)

    rendered_blocks: list[str] = []
    sections: list[dict[str, Any]] = []
    for convo in conversations:
        convo_msgs = sorted(
            [
                m for m in (getattr(convo, "messages", []) or [])
                if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)
            ],
            key=lambda m: m.sent_at,
        )[-limit:]
        if not convo_msgs:
            continue
        label = _label_task_conversation(convo)
        lines = [f"[{m.sender_name or 'Unknown'}]: {m.body or ''}" for m in convo_msgs]
        rendered_blocks.append(f"{label}:\n" + "\n".join(lines))
        sections.append({
            "section_type": "conversation_transcript",
            "title": label,
            "content": "\n".join(lines),
            "origin": {
                "kind": "conversation",
                "conversation_id": convo.id,
                "conversation_type": getattr(convo, "conversation_type", None),
                "task_id": task.id,
            },
        })

    if not rendered_blocks:
        return "", []
    return "\n\n".join(rendered_blocks), sections


def build_task_context_data(db: Session, task_id: str, query: str | None = None) -> dict[str, Any]:
    """Build retrieval-driven task context with a structured provenance breakdown."""
    task = _resolve_task(db, task_id)
    if not task:
        return load_account_context_data(db, query=query)

    bundle = retrieve_context(db, RetrievalRequest(
        surface="task",
        intent="task_context",
        query=query or task.title or f"task {task.id}",
        task_id=str(task.id),
        property_id=str(task.property_id) if task.property_id else None,
        unit_id=str(task.unit_id) if task.unit_id else None,
        limit=14,
    ))

    sections: list[dict[str, Any]] = []

    lines = [
        f"Task ID: {task.id}",
        f"Task: {task.title}",
        f"Category: {task.category or 'general'}",
        f"Urgency: {task.urgency or 'normal'}",
        f"Status: {task.task_status or 'active'}",
        f"Mode: {task.task_mode or 'manual'}",
        "",
        "Task execution rules:",
        "- Stay inside this task unless you discover a genuinely separate issue.",
        "- Do not create a new task for sub-steps or approvals related to this same issue.",
        "- If you need the user to provide a file or approval, explain the blocker first and ask before creating a suggestion.",
        "- If the user says a requested notice/file was uploaded or served, do not create a follow-up task or suggestion. Acknowledge it, tell them to document service date/method, and continue the same task.",
    ]
    sections.append({
        "section_type": "task_core",
        "title": "Task core",
        "content": "\n".join(lines),
        "origin": {
            "kind": "task",
            "task_id": task.id,
            "property_id": str(task.property_id) if task.property_id else None,
            "unit_id": str(task.unit_id) if task.unit_id else None,
            "source": task.source,
        },
        "metadata": {
            "task_title": task.title,
            "category": task.category,
            "urgency": task.urgency,
            "status": task.task_status,
            "mode": task.task_mode,
        },
    })

    ai_convo = task.ai_conversation
    all_msgs = list(ai_convo.messages) if ai_convo else []
    context_msgs = [m for m in all_msgs if m.message_type == MessageType.CONTEXT]
    if context_msgs:
        description_line = f"Description: {context_msgs[0].body}"
        lines.append(description_line)
        sections.append({
            "section_type": "task_description",
            "title": "Task description",
            "content": description_line,
            "origin": {
                "kind": "conversation_context",
                "conversation_id": ai_convo.id if ai_convo else None,
                "message_type": MessageType.CONTEXT.value if hasattr(MessageType.CONTEXT, "value") else str(MessageType.CONTEXT),
            },
        })

    if task.context:
        lines.append("")
        lines.append("Task notes:")
        lines.append(task.context)
        sections.append({
            "section_type": "task_notes",
            "title": "Task notes",
            "content": task.context,
            "origin": {"kind": "task", "field": "context", "task_id": task.id},
        })

    pending_suggestions = db.query(Suggestion).filter(
        Suggestion.task_id == task.id,
        Suggestion.status == "pending",
    ).all()
    if pending_suggestions:
        lines.append("")
        lines.append("Pending suggestions (already queued, do NOT duplicate):")
        for s in pending_suggestions:
            action = (s.action_payload or {}).get("action", "unknown")
            if action == "request_file_upload":
                requested = (s.action_payload or {}).get("requested_file_label", "requested file")
                entry = f"  - [request_file_upload] {s.title or 'untitled'} — task is blocked until {requested} is uploaded"
                lines.append(entry)
                continue
            draft = (s.action_payload or {}).get("draft_message", "")
            entry = f"  - [{action}] {s.title or 'untitled'}"
            if draft:
                entry += f" — draft: {draft[:80]}"
            lines.append(entry)
        sections.append({
            "section_type": "pending_suggestions",
            "title": "Pending suggestions",
            "content": "\n".join(lines[-(len(pending_suggestions) + 1):]),
            "origin": {"kind": "suggestions", "task_id": task.id},
            "metadata": {
                "suggestions": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "status": s.status,
                        "action": (s.action_payload or {}).get("action"),
                        "suggestion_type": s.suggestion_type,
                    }
                    for s in pending_suggestions
                ]
            },
        })

    prop = db.query(Property).filter_by(id=task.property_id).first() if task.property_id else None
    unit = db.query(Unit).filter_by(id=task.unit_id).first() if task.unit_id else None
    tenant = None
    lease = None

    if unit and getattr(unit, "tenant_id", None):
        tenant = db.query(Tenant).filter_by(id=unit.tenant_id).first()

    lease_query = db.query(Lease)
    if task.unit_id:
        lease = (
            lease_query.filter_by(unit_id=task.unit_id)
            .order_by(Lease.start_date.desc())
            .first()
        )
    elif task.property_id:
        lease = (
            lease_query.filter_by(property_id=task.property_id)
            .order_by(Lease.start_date.desc())
            .first()
        )

    if lease and not tenant and lease.tenant_id:
        tenant = db.query(Tenant).filter_by(id=lease.tenant_id).first()

    factual_lines: list[str] = []
    if prop:
        address_bits = [prop.address_line1, prop.city, prop.state, prop.postal_code]
        factual_lines.append(f"Property: {', '.join(bit for bit in address_bits if bit)}")
        if prop.name:
            factual_lines.append(f"Property name: {prop.name}")
    if unit:
        factual_lines.append(f"Unit: {unit.label}")
    if tenant:
        factual_lines.append(f"Tenant ID: {tenant.external_id}")
        if tenant.user:
            tenant_name = tenant.user.name or tenant.user.email or "Unknown tenant"
            factual_lines.append(f"Tenant: {tenant_name}")
            if tenant.user.phone:
                factual_lines.append(f"Tenant phone: {tenant.user.phone}")
            if tenant.user.email:
                factual_lines.append(f"Tenant email: {tenant.user.email}")
    if lease:
        factual_lines.append(f"Lease rent amount: ${lease.rent_amount:,.0f}" if float(lease.rent_amount).is_integer() else f"Lease rent amount: ${lease.rent_amount:,.2f}")
        if lease.payment_status:
            factual_lines.append(f"Lease payment status: {lease.payment_status}")
        factual_lines.append(f"Lease term: {lease.start_date.isoformat()} to {lease.end_date.isoformat()}")

    if factual_lines:
        lines.append("")
        lines.append("Current task facts:")
        lines.extend(factual_lines)
        sections.append({
            "section_type": "task_facts",
            "title": "Current task facts",
            "content": "\n".join(factual_lines),
            "origin": {"kind": "task_entities", "task_id": task.id},
        })

    transcript_block, transcript_sections = build_task_conversation_transcripts(task)
    if transcript_block:
        lines.append("")
        lines.append("Linked conversation transcripts:")
        lines.append(transcript_block)
        sections.extend(transcript_sections)

    ranked_block = compose_prompt_context(bundle, title="Ranked context")
    if ranked_block:
        lines.append("")
        lines.append(ranked_block)
        sections.append({
            "section_type": "retrieval",
            "title": "Ranked context",
            "content": ranked_block,
            "origin": {"kind": "retrieval", "task_id": task.id},
            "retrieval": _serialize_retrieval_bundle(bundle),
        })

    property_summary: dict[str, Any] | None = None
    if prop:
        property_summary = {
            "id": str(prop.id),
            "address": prop.address_line1,
            "city": prop.city,
            "state": prop.state,
            "postal_code": prop.postal_code,
            "type": prop.property_type,
        }

    unit_summary: dict[str, Any] | None = None
    if unit:
        unit_summary = {
            "id": str(unit.id),
            "label": unit.label,
        }

    return {
        "scope": "task",
        "task_id": str(task.id),
        "query": query,
        "text": "\n".join(lines),
        "sections": sections,
        "retrieval": _serialize_retrieval_bundle(bundle),
        "task": {
            "id": str(task.id),
            "title": task.title,
            "category": task.category,
            "urgency": task.urgency,
            "status": task.task_status,
            "mode": task.task_mode,
            "source": task.source,
            "property": property_summary,
            "unit": unit_summary,
        },
    }


def build_task_context(db: Session, task_id: str, query: str | None = None) -> str:
    return build_task_context_data(db, task_id, query=query)["text"]


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
