"""RentMate agent tool classes.

Includes suggestion tools (propose_task, close_task, message_person).

When a tool creates a visible entity/action during a chat, it queues a chat
message via ``pending_suggestion_messages``.  The chat handler flushes these
*after* persisting the AI reply so they appear below the agent response in the
conversation timeline.  The conversation_id is communicated via the
``active_conversation_id`` context variable, set by the chat handler before
the agent runs.
"""
import contextvars
import hashlib
import json
import logging
import re
import traceback
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from backends.local_auth import resolve_account_id, resolve_org_id
from db.enums import AgentSource, SuggestionOption, TaskCategory, Urgency
from db.models import MessageType
from gql.services.task_service import dump_task_steps

logger = logging.getLogger("rentmate.llm.tools")


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

current_user_message: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_message", default=None,
)

# When set, suggestion-producing tools run in dry-run mode and append the
# suggestion payloads here instead of writing Suggestion rows to the DB.
simulation_suggestions: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "simulation_suggestions", default=None,
)


_DIRECT_DRAFT_NEGATIVE_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bdon't create (a )?suggestion\b",
        r"\bdont create (a )?suggestion\b",
        r"\bdo not create (a )?suggestion\b",
        r"\bdon't create (a )?task\b",
        r"\bdont create (a )?task\b",
        r"\bdo not create (a )?task\b",
        r"\bnot a suggestion\b",
        r"\bnot a task\b",
    ]
]

_DIRECT_DRAFT_REQUEST_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bcreate (the )?draft\b",
        r"\bdraft (the )?(notice|letter|document)\b",
        r"\bdraft it directly\b",
        r"\bcreate it directly\b",
        r"\bdirectly\b.*\bdraft\b",
    ]
]


def _trace_tool_error(tool_name: str, summary: str, *, detail: dict[str, Any] | None = None) -> None:
    try:
        from llm.tracing import log_trace

        log_trace("error", "tool", summary, tool_name=tool_name, detail=detail)
    except Exception:
        pass


def _log_tool_error(tool_name: str, summary: str, *, detail: dict[str, Any] | None = None) -> None:
    safe_detail = detail or {}
    logger.error("%s: %s | detail=%s", tool_name, summary, json.dumps(safe_detail, default=str))
    _trace_tool_error(tool_name, summary, detail=safe_detail)


def _queue_simulation_suggestion(payload: dict[str, Any]) -> str | None:
    pending = simulation_suggestions.get()
    if pending is None:
        return None
    suggestion_id = f"sim-{len(pending) + 1}"
    pending.append({
        "id": suggestion_id,
        **payload,
    })
    return suggestion_id


def _public_entity_id(entity: Any) -> str:
    external_id = getattr(entity, "external_id", None)
    return str(external_id or entity.id)


def _queue_chat_message(
    *,
    body: str,
    message_type: MessageType,
    related_task_ids: dict[str, Any] | None = None,
    draft_reply: str | None = None,
    action_card: dict[str, Any] | None = None,
) -> None:
    conv_id = active_conversation_id.get()
    if not conv_id:
        return
    pending = pending_suggestion_messages.get()
    if pending is None:
        pending = []
        pending_suggestion_messages.set(pending)
    pending.append({
        "conversation_id": conv_id,
        "type": "chat_message",
        "body": body,
        "message_type": message_type,
        "sender_name": "RentMate",
        "is_ai": True,
        "draft_reply": draft_reply,
        "related_task_ids": related_task_ids,
        "meta": {"action_card": action_card} if action_card else None,
    })


def _load_vendor_by_public_id(db: Any, vendor_id: str):
    from db.models import User

    return (
        db.query(User)
        .filter_by(external_id=str(vendor_id), user_type="vendor")
        .first()
    )


def _load_tenant_by_public_id(db: Any, tenant_id: str):
    from db.models import Tenant

    return db.query(Tenant).filter_by(external_id=str(tenant_id)).first()


def _load_entity_by_public_id(db: Any, entity_type: str, entity_id: str):
    import db.models as models

    model_map = {
        "property": models.Property,
        "unit": models.Unit,
        "tenant": models.Tenant,
        "vendor": models.User,
        "document": models.Document,
    }
    model_cls = model_map.get(entity_type)
    if not model_cls:
        return None

    filters = {"external_id": str(entity_id)} if hasattr(model_cls, "external_id") else {"id": entity_id}
    if entity_type == "vendor":
        filters["user_type"] = "vendor"
    return db.query(model_cls).filter_by(**filters).first()


def _action_card_field(label: str, value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return {"label": label, "value": text}


def _enum_label(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, str):
        return value.replace("_", " ").title()
    try:
        return str(Urgency(value).name).replace("_", " ").title()
    except Exception:
        return str(value)


def _sanitize_filename_component(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip()).strip("-.")
    return cleaned or "document"


def _ensure_pdf_filename(title: str, filename: str | None = None) -> str:
    candidate = _sanitize_filename_component(filename or title)
    if not candidate.lower().endswith(".pdf"):
        candidate += ".pdf"
    return candidate


def _ensure_unique_document_filename(db: Any, filename: str) -> str:
    from db.models import Document

    existing_names = {
        row[0]
        for row in db.query(Document.filename).filter(
            Document.org_id == resolve_org_id(),
            Document.creator_id == resolve_account_id(),
            Document.filename.isnot(None),
        ).all()
        if row[0]
    }
    if filename not in existing_names:
        return filename

    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = filename, ""

    index = 1
    while True:
        candidate = f"{stem}-{index}{ext}"
        if candidate not in existing_names:
            return candidate
        index += 1


_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{2,100}\]")


def _extract_legal_field_names(items: list[Any] | None) -> list[str]:
    names: list[str] = []
    for item in items or []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("field") or item.get("name") or item.get("label") or "").strip()
        else:
            text = str(item).strip()
        if text:
            names.append(text)
    return names


def _format_field_list(field_names: list[str]) -> str:
    cleaned = [name.strip() for name in field_names if name and name.strip()]
    if not cleaned:
        return "the required notice fields"
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _find_unresolved_placeholders(*content_blocks: str | None) -> list[str]:
    placeholders: list[str] = []
    for block in content_blocks:
        if not block:
            continue
        placeholders.extend(match.group(0) for match in _UNRESOLVED_PLACEHOLDER_RE.finditer(block))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in placeholders:
        if item in seen:
            continue
        deduped.append(item)
        seen.add(item)
    return deduped


def _is_legal_or_compliance_document(kwargs: dict[str, Any]) -> bool:
    document_type = str(kwargs.get("document_type") or "").strip().lower()
    document_category = str(kwargs.get("document_category") or "").strip().lower()
    summary = str(kwargs.get("summary") or "")
    title = str(kwargs.get("title") or "")
    html = str(kwargs.get("html") or "")
    content = str(kwargs.get("content") or "")
    risk_score = kwargs.get("risk_score")

    if document_type == "notice":
        return True
    if document_category in {"legal", "compliance"}:
        return True
    try:
        if risk_score is not None and float(risk_score) >= 7:
            return True
    except (TypeError, ValueError):
        pass

    combined = " ".join([title, summary, html, content]).lower()
    return any(
        token in combined
        for token in [
            "pay or vacate",
            "eviction",
            "unlawful detainer",
            "compliance",
            "legal notice",
            "statutory",
            "cure or quit",
        ]
    )


def _legal_notice_block_message(
    *,
    missing_fields: list[str] | None = None,
    citation: str | None = None,
    jurisdiction: str | None = None,
    reason: str | None = None,
) -> str:
    parts: list[str] = []
    field_text = _format_field_list(missing_fields or [])
    jurisdiction_text = f" for {jurisdiction.strip()}" if isinstance(jurisdiction, str) and jurisdiction.strip() else ""
    parts.append(
        f"I need {field_text} before I can create this legal notice{jurisdiction_text}. "
        "I can't infer legally required notice details."
    )
    if citation:
        parts.append(f"The governing law I relied on is {citation.strip()}.")
    if reason:
        parts.append(reason.strip())
    parts.append("Please provide that information and I can generate the document.")
    return " ".join(parts)


def _create_document_tags(
    db: Any,
    *,
    document_id: str,
    property_id: str | None = None,
    unit_id: str | None = None,
    tenant_id: str | None = None,
) -> None:
    from db.models import DocumentTag

    if property_id:
        property_row = _load_entity_by_public_id(db, "property", property_id)
        if property_row:
            db.add(DocumentTag(
                id=str(uuid.uuid4()),
                org_id=resolve_org_id(),
                document_id=document_id,
                tag_type="property",
                property_id=str(property_row.id),
            ))
    if unit_id:
        unit_row = _load_entity_by_public_id(db, "unit", unit_id)
        if unit_row:
            db.add(DocumentTag(
                id=str(uuid.uuid4()),
                org_id=resolve_org_id(),
                document_id=document_id,
                tag_type="unit",
                property_id=str(unit_row.property_id) if getattr(unit_row, "property_id", None) else None,
                unit_id=str(unit_row.id),
            ))
    if tenant_id:
        tenant_row = _load_tenant_by_public_id(db, tenant_id)
        if tenant_row:
            db.add(DocumentTag(
                id=str(uuid.uuid4()),
                org_id=resolve_org_id(),
                document_id=document_id,
                tag_type="tenant",
                tenant_id=tenant_row.id,
            ))


async def _create_generated_document(
    *,
    title: str,
    html_content: str | None = None,
    text_content: str | None = None,
    filename: str | None = None,
    document_type: str = "other",
    summary: str | None = None,
    property_id: str | None = None,
    unit_id: str | None = None,
    tenant_id: str | None = None,
    task_id: str | None = None,
) -> tuple[str, str]:
    from backends.wire import storage_backend
    from db.models import Document
    from db.session import SessionLocal
    from gql.services.document_service import dump_document_extraction_meta
    from llm.generated_documents import render_document_async

    db = SessionLocal.session_factory()
    try:
        now = datetime.now(UTC)
        resolved_filename = _ensure_unique_document_filename(db, _ensure_pdf_filename(title, filename))
        rendered = await render_document_async(title=title, html_content=html_content, text_content=text_content)
        doc_id = str(uuid.uuid4())
        storage_path = f"generated-documents/{doc_id}/{resolved_filename}"
        html_storage_path = f"generated-documents/{doc_id}/source.html"
        checksum = hashlib.sha256(rendered.pdf_bytes).hexdigest()

        await storage_backend.upload(storage_path, data=rendered.pdf_bytes, content_type="application/pdf")
        await storage_backend.upload(html_storage_path, data=rendered.html.encode("utf-8"), content_type="text/html")

        doc = Document(
            id=doc_id,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            filename=resolved_filename,
            content_type="application/pdf",
            storage_path=storage_path,
            document_type=document_type,
            status="done",
            progress="generated",
            raw_text=text_content or html_content,
            context=summary or title,
            sha256_checksum=checksum,
            created_at=now,
            processed_at=now,
            extraction_meta=dump_document_extraction_meta(
                task_id=task_id,
                source="agent_generated",
                generated_by_tool="create_document",
                generated_html_storage_path=html_storage_path,
                generated_html_content_type="text/html",
                generated_pdf_renderer=rendered.renderer,
            ),
        )
        db.add(doc)
        _create_document_tags(
            db,
            document_id=doc_id,
            property_id=property_id,
            unit_id=unit_id,
            tenant_id=tenant_id,
        )
        db.commit()
        return doc_id, resolved_filename
    finally:
        db.close()


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
    unit_id: str | None = None,
    document_id: str | None = None,
    risk_score: int | None = None,
    suggestion_type: str | None = None,
) -> str:
    """Write a Suggestion row and return its ID.

    If ``active_conversation_id`` is set, also adds an APPROVAL message to
    that conversation so the suggestion appears inline in the chat.
    """
    simulated_id = _queue_simulation_suggestion({
        "title": title,
        "body": ai_context,
        "category": category,
        "urgency": urgency,
        "action_payload": action_payload,
        "task_id": task_id,
        "property_id": property_id,
        "unit_id": unit_id,
        "document_id": document_id,
        "risk_score": risk_score,
        "suggestion_type": suggestion_type,
    })
    if simulated_id is not None:
        return simulated_id

    from db.models import Suggestion
    from db.session import SessionLocal
    from gql.services import suggestion_service

    db = SessionLocal.session_factory()
    try:
        # Deduplicate: skip if an equivalent pending suggestion already exists for this task
        if task_id and action_payload and action_payload.get("action"):
            from sqlalchemy import select
            existing = db.execute(
                select(Suggestion).where(
                    Suggestion.task_id == task_id,
                    Suggestion.status == "pending",
                )
            ).scalars().all()
            for s in existing:
                existing_payload = s.action_payload or {}
                if existing_payload.get("action") != action_payload["action"]:
                    continue
                if action_payload["action"] == "request_file_upload":
                    if (
                        existing_payload.get("requested_file_kind") == action_payload.get("requested_file_kind")
                        and str(existing_payload.get("target_task_id") or "") == str(action_payload.get("target_task_id") or task_id)
                        and str(existing_payload.get("target_tenant_id") or "") == str(action_payload.get("target_tenant_id") or "")
                    ):
                        return s.id
                else:
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
            unit_id=unit_id,
        )
        if task_id:
            suggestion.task_id = task_id
        if document_id:
            suggestion.document_id = document_id
        if risk_score is not None:
            suggestion.risk_score = risk_score
        if suggestion_type:
            suggestion.suggestion_type = suggestion_type
        suggestion_id = suggestion.id

        fields = [
                field
                for field in [
                _action_card_field("Category", _enum_label(category)),
                _action_card_field("Urgency", _enum_label(urgency)),
                _action_card_field("Vendor", action_payload.get("vendor_name") if action_payload else None),
                _action_card_field("Entity", action_payload.get("entity_name") if action_payload else None),
                _action_card_field("Requested File", action_payload.get("requested_file_label") if action_payload else None),
            ]
            if field
        ]
        links = [{
            "label": "Open suggestion",
            "entity_type": "suggestion",
            "entity_id": str(suggestion_id),
        }]
        if property_id:
            links.append({
                "label": "Property",
                "entity_type": "property",
                "entity_id": str(property_id),
            })
        _queue_chat_message(
            body=title,
            message_type=MessageType.ACTION,
            related_task_ids={"suggestion_id": suggestion_id},
            draft_reply=action_payload.get("draft_message") if action_payload else None,
            action_card={
                "kind": "suggestion",
                "title": title,
                "summary": ai_context,
                "fields": fields,
                "links": links,
                "units": [],
            },
        )

        db.commit()

        # Trace suggestion creation
        from llm.tracing import log_trace
        log_trace(
            "suggestion_created", "agent", title,
            task_id=task_id,
            suggestion_id=suggestion_id,
            detail=action_payload,
        )

        return suggestion_id
    finally:
        db.close()


def _get_task_title(task_id: str) -> str:
    """Look up a task's title for use in suggestion headlines."""
    from db.models import Task
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        return task.title if task else task_id
    finally:
        db.close()


def _resolve_task_id_from_active_conversation() -> str | None:
    conv_id = active_conversation_id.get()
    if not conv_id:
        return None
    try:
        conv_lookup = int(conv_id)
    except (TypeError, ValueError):
        conv_lookup = conv_id

    from db.models import Task
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(ai_conversation_id=conv_lookup).first()
        return str(task.id) if task else None
    finally:
        db.close()


def _recent_user_messages(task_id: str, *, limit: int = 6) -> list[str]:
    from db.models import Message, Task
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task or not task.ai_conversation_id:
            return []
        rows = (
            db.query(Message)
            .filter_by(conversation_id=task.ai_conversation_id)
            .order_by(Message.sent_at.desc())
            .limit(limit)
            .all()
        )
        return [str(m.body or "") for m in rows if not getattr(m, "is_ai", False)]
    finally:
        db.close()


_SUGGESTION_CONFIRM_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bcreate (a )?suggestion\b",
        r"\bgo ahead\b",
        r"\byes\b",
        r"\bdo that\b",
        r"\badd it\b",
        r"\baction desk\b",
        r"\bupload\b",
        r"\bnotice\b",
    ]
]


def _has_user_confirmed_upload_request(task_id: str) -> bool:
    current_message = current_user_message.get()
    if current_message and any(pattern.search(current_message) for pattern in _SUGGESTION_CONFIRM_PATTERNS):
        return True
    for message in _recent_user_messages(task_id):
        if any(pattern.search(message) for pattern in _SUGGESTION_CONFIRM_PATTERNS):
            return True
    return False


def _normalize_current_task_suggestion_payload(
    *,
    task_id: str,
    title: str,
    body: str,
    action_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(action_payload or {})
    if payload.get("action"):
        return payload

    combined = f"{title}\n{body}".lower()
    if not any(keyword in combined for keyword in ["upload", "notice", "signed document", "draft 14-day", "pay or vacate"]):
        return payload

    requested_file_label = title.strip()
    for prefix in ("Draft ", "Upload "):
        if requested_file_label.startswith(prefix):
            requested_file_label = requested_file_label[len(prefix):].strip()

    requested_file_kind = "notice" if "notice" in combined else "document"
    instructions = (
        f"Upload the completed {requested_file_label} so the current task can continue."
    )
    if requested_file_kind == "notice" and "14-day" in combined:
        instructions = (
            f"Upload the completed {requested_file_label} for service on the tenant so the current task can continue."
        )

    payload.update({
        "action": "request_file_upload",
        "requested_file_kind": requested_file_kind,
        "requested_file_label": requested_file_label,
        "instructions": instructions,
        "target_task_id": task_id,
    })
    return payload


def _mark_task_waiting_on_upload_request(
    *,
    task_id: str,
    requested_file_label: str,
    instructions: str,
) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from db.enums import TaskMode
    from db.models import Task
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task:
            return

        steps = list(task.steps or [])
        blocked_note = f"Blocked until {requested_file_label} is uploaded."
        updated = False
        for step in steps:
            key = str(step.get("key") or "")
            label = str(step.get("label") or "")
            if key == "upload_requested_file" or requested_file_label.lower() in label.lower():
                step["status"] = "pending"
                step["note"] = blocked_note
                updated = True
                break
        if not updated:
            steps.append({
                "key": "upload_requested_file",
                "label": f"Upload {requested_file_label}",
                "status": "pending",
                "note": blocked_note,
            })

        task.steps = dump_task_steps(steps)
        flag_modified(task, "steps")
        task.task_mode = TaskMode.WAITING_APPROVAL

        context_parts = [part for part in [task.context, f"Blocked on user deliverable: {instructions}"] if part]
        task.context = "\n\n".join(dict.fromkeys(context_parts))
        db.commit()
    finally:
        db.close()


def _current_task_notice_service_reported(task_id: str) -> bool:
    current_message = (current_user_message.get() or "").lower()
    if not current_message:
        return False
    mentions_notice = any(term in current_message for term in ["14-day", "pay or vacate", "notice"])
    mentions_completion = any(term in current_message for term in ["uploaded", "served", "mailed", "posted", "certified mail"])
    if not (mentions_notice and mentions_completion):
        return False

    from db.models import Suggestion
    from db.session import SessionLocal

    db = SessionLocal.session_factory()
    try:
        pending = (
            db.query(Suggestion)
            .filter(
                Suggestion.task_id == task_id,
                Suggestion.status == "pending",
            )
            .all()
        )
        return any((s.action_payload or {}).get("action") == "request_file_upload" for s in pending)
    finally:
        db.close()


def _same_task_handoff_block_message() -> str:
    return (
        "Do not create a new suggestion or task for this. Stay in the current task, "
        "acknowledge that the notice was uploaded and served, tell the manager to document "
        "the service date and method, and explain that the next step is to wait out the 14-day notice period."
    )


def _current_message_requests_direct_draft() -> bool:
    current_message = (current_user_message.get() or "").strip()
    if not current_message:
        return False
    has_negative = any(pattern.search(current_message) for pattern in _DIRECT_DRAFT_NEGATIVE_PATTERNS)
    wants_draft = any(pattern.search(current_message) for pattern in _DIRECT_DRAFT_REQUEST_PATTERNS)
    return has_negative and wants_draft


def _direct_draft_block_message() -> str:
    return (
        "Do not create a suggestion or task for this. The user asked for the draft itself. "
        "Draft the requested notice or document directly in the chat response."
    )


class ProposeTaskTool(Tool):
    """Create a task proposal for manager review."""

    @property
    def name(self) -> str:
        return "propose_task"

    @property
    def description(self) -> str:
        return (
            "Propose a new task for a genuinely separate issue. "
            "Only use propose_task for a genuinely separate issue that needs its own task. "
            "Never use propose_task when the user asked you to draft a notice, letter, or document directly in chat. "
            "You MUST provide a vendor_id external UUID — use lookup_vendors first. "
            "Include a draft_message and steps (3-6 steps)."
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
                "vendor_id": {"type": "string", "description": "External UUID of the vendor to assign (use lookup_vendors to find this)"},
                "draft_message": {"type": "string", "description": "Draft message to send to the vendor on approval"},
                "property_id": {"type": "string", "description": "Property ID (if applicable)"},
                "task_id": {"type": "string", "description": "Originating task ID (if applicable)"},
                "risk_score": {
                    "type": "integer",
                    "description": "0-10: risk of auto-approving. 0=safe, 10=must review. Default 5.",
                },
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
        task_id = str(kwargs.get("task_id") or _resolve_task_id_from_active_conversation() or "")
        if _current_message_requests_direct_draft():
            return json.dumps({"status": "error", "message": _direct_draft_block_message()})
        if task_id and _current_task_notice_service_reported(task_id):
            return json.dumps({"status": "error", "message": _same_task_handoff_block_message()})

        vendor_id = str(kwargs["vendor_id"])

        from db.session import SessionLocal
        db = SessionLocal.session_factory()
        try:
            vendor = _load_vendor_by_public_id(db, vendor_id)
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
            action_payload["steps"] = dump_task_steps(steps)

        if draft_message:
            options = [
                SuggestionOption(key="send", label=f"Send to {vendor_name}", action="send_and_create_task", variant="default"),
                SuggestionOption(key="edit", label="Edit Message", action="edit_message", variant="outline"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]
        else:
            options = [
                SuggestionOption(key="send", label=f"Assign {vendor_name}", action="send_and_create_task", variant="default"),
                SuggestionOption(key="reject", label="Dismiss", action="reject_task", variant="ghost"),
            ]

        # External contact messages are at least risk 4
        risk = kwargs.get("risk_score", 3)
        if draft_message and risk < 4:
            risk = 4

        sid = _create_suggestion(
            title=kwargs["title"],
            ai_context=kwargs.get("description") or kwargs["title"],
            category=kwargs["category"],
            urgency=kwargs.get("urgency", Urgency.MEDIUM.value),
            action_payload=action_payload,
            options=options,
            task_id=kwargs.get("task_id") or task_id or None,
            property_id=kwargs.get("property_id"),
            risk_score=risk,
            suggestion_type=kwargs["category"],
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

        from datetime import UTC, datetime

        from db.models import Task as TaskModel
        from db.session import SessionLocal
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


def _get_task_category(task_id: str) -> str | None:
    """Look up a task's category for autonomy checks."""
    from db.models import Task
    from db.session import SessionLocal
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
    from db.session import SessionLocal
    from gql.services.task_suggestions import SuggestionExecutor
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
                entity_name = entity.user.name if entity and entity.user else "Tenant"
                entity_phone = entity.user.phone if entity and entity.user else None
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


class LookupVendorsTool(Tool):
    """Look up vendors in the system, optionally filtered by type."""

    @property
    def name(self) -> str:
        return "lookup_vendors"

    @property
    def description(self) -> str:
        return (
            "Search for vendors/contractors in the system. "
            "Returns a list of vendors with their external UUID, name, company, type, phone, and email. "
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
        from db.models import User
        from db.session import SessionLocal

        vendor_type = kwargs.get("vendor_type")
        query = (kwargs.get("query") or "").strip().lower()

        db = SessionLocal.session_factory()
        try:
            vendors = db.query(User).filter_by(user_type="vendor").all()
            results = []
            for v in vendors:
                if vendor_type and (v.role_label or "").lower() != vendor_type.lower():
                    continue
                if query and query not in (v.name or "").lower() and query not in (v.company or "").lower():
                    continue
                results.append({
                    "id": str(v.external_id),
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
        from db.session import SessionLocal
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
                "vendor_id": str(vendor.external_id),
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
                existing = task.context or ""
                task.context = f"{existing}\n{entry}".strip()
                db.commit()
                return json.dumps({"status": "ok", "message": "Task note saved."})
            finally:
                db.close()

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

        db = SessionLocal.session_factory()
        try:
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
                db.commit()
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
                db.commit()
                return json.dumps({"status": "ok", "message": f"Private note saved for {label}."})
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

        from db.session import SessionLocal
        db = SessionLocal.session_factory()
        try:
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
                db.commit()
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
                db.commit()
                action = "cleared" if not new_context.strip() else "updated"
                return json.dumps({"status": "ok", "message": f"Private notes {action}."})
        finally:
            db.close()


class CreatePropertyTool(Tool):
    """Create a new property with optional units — used during onboarding or manual setup."""

    @property
    def name(self) -> str:
        return "create_property"

    @property
    def description(self) -> str:
        return (
            "Create a new property with optional units. Provide at minimum an address. "
            "Optionally specify property name, city, state, postal_code, property_type "
            "(single_family or multi_family), and unit_labels (list of strings like "
            "['1A', '1B', '2A']). If unit_count is provided instead of labels, units "
            "will be auto-labeled (Unit 1, Unit 2, etc.)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["address"],
            "properties": {
                "address": {"type": "string", "description": "Street address of the property"},
                "name": {"type": "string", "description": "Optional display name for the property"},
                "city": {"type": "string", "description": "City"},
                "state": {"type": "string", "description": "State abbreviation (e.g. WA, CA)"},
                "postal_code": {"type": "string", "description": "ZIP/postal code"},
                "property_type": {
                    "type": "string",
                    "enum": ["single_family", "multi_family"],
                    "description": "Property type (default: multi_family)",
                },
                "unit_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit unit labels like ['1A', '1B', '2A']",
                },
                "unit_count": {
                    "type": "integer",
                    "description": "Number of units (auto-labeled). Ignored if unit_labels provided.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.session import SessionLocal
        from gql.services.property_service import PropertyService
        from gql.services.settings_service import (
            get_onboarding_state,
            set_onboarding_path,
            update_onboarding_step,
        )
        from llm.tracing import log_trace

        address = kwargs["address"]
        unit_labels = kwargs.get("unit_labels")
        unit_count = kwargs.get("unit_count")

        # Auto-generate labels from count if no explicit labels
        if not unit_labels and unit_count and unit_count > 0:
            unit_labels = [f"Unit {i}" for i in range(1, unit_count + 1)]

        # Infer property type: single unit (or none specified) → single_family
        property_type = kwargs.get("property_type")
        if not property_type:
            effective_units = len(unit_labels) if unit_labels else (unit_count or 1)
            property_type = "single_family" if effective_units <= 1 else "multi_family"

        db = SessionLocal.session_factory()
        try:
            prop, units = PropertyService.create_property(
                db,
                address=address,
                property_type=property_type,
                name=kwargs.get("name"),
                city=kwargs.get("city"),
                state=kwargs.get("state"),
                postal_code=kwargs.get("postal_code"),
                unit_labels=unit_labels,
            )

            # Update onboarding progress if active
            onboarding = get_onboarding_state(db)
            if onboarding and onboarding.get("status") == "active":
                update_onboarding_step(db, step="add_property")
                if not onboarding.get("path_picked"):
                    set_onboarding_path(db, path="manual")
                log_trace(
                    "onboarding", "tool", "First property created",
                    tool_name="create_property",
                    detail={"property_id": prop.id, "address": address},
                )

            db.commit()

            fields = [
                field
                for field in [
                    _action_card_field("Address", prop.address_line1),
                    _action_card_field("Type", "Single family" if property_type == "single_family" else "Multi-family"),
                    _action_card_field("Created units", len(units)),
                ]
                if field
            ]
            _queue_chat_message(
                body=f"Created property {prop.name or prop.address_line1}",
                message_type=MessageType.ACTION,
                action_card={
                    "kind": "property",
                    "title": prop.name or prop.address_line1,
                    "summary": f"Created property at {prop.address_line1}" if not prop.name else f"Created property at {prop.address_line1}.",
                    "fields": fields,
                    "links": [{
                        "label": "Open property",
                        "entity_type": "property",
                        "entity_id": str(prop.id),
                    }],
                    "units": [
                        {"uid": str(u.id), "label": u.label, "property_id": str(prop.id)}
                        for u in units
                    ],
                },
            )

            unit_str = ", ".join(u.label for u in units) if units else "none"
            return json.dumps({
                "status": "ok",
                "property_id": str(prop.id),
                "address": prop.address_line1,
                "name": prop.name,
                "units": [{"id": str(u.id), "label": u.label} for u in units],
                "message": f"Created property '{prop.name or prop.address_line1}' with {len(units)} unit(s): {unit_str}",
            })
        except Exception as e:
            db.rollback()
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()


class CreateTenantTool(Tool):
    """Create a tenant and optionally a lease for them."""

    @property
    def name(self) -> str:
        return "create_tenant"

    @property
    def description(self) -> str:
        return (
            "Create a new tenant. Pass whatever information you have — only "
            "first_name and last_name are required. If property_id and unit_id "
            "are provided the tenant is linked to that unit. If lease dates and "
            "rent are also provided a full lease record is created. Any extra "
            "context (e.g. pet policy, move-in notes, partial lease details) "
            "can be passed in the 'notes' field and saved to the tenant's "
            "permanent context."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["first_name", "last_name"],
            "properties": {
                "first_name": {"type": "string", "description": "Tenant first name"},
                "last_name": {"type": "string", "description": "Tenant last name"},
                "email": {"type": "string", "description": "Email address"},
                "phone": {"type": "string", "description": "Phone number"},
                "property_id": {"type": "string", "description": "Property ID to attach lease to"},
                "unit_id": {"type": "string", "description": "Unit ID within the property"},
                "lease_start": {"type": "string", "description": "Lease start date (YYYY-MM-DD)"},
                "lease_end": {"type": "string", "description": "Lease end date (YYYY-MM-DD)"},
                "rent_amount": {"type": "number", "description": "Monthly rent amount"},
                "notes": {"type": "string", "description": "Context or notes about this tenant to save permanently"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.models import Tenant as SqlTenant, User
        from db.models.account import create_shadow_user
        from db.session import SessionLocal

        first_name = kwargs["first_name"]
        last_name = kwargs["last_name"]

        db = SessionLocal.session_factory()
        try:
            import uuid
            from datetime import UTC, datetime

            # Check for existing tenant by name
            from sqlalchemy import func
            existing = (
                db.query(SqlTenant)
                .join(User, SqlTenant.user_id == User.id)
                .filter(
                    func.lower(User.first_name) == first_name.lower(),
                    func.lower(User.last_name) == last_name.lower(),
                )
                .first()
            )
            if existing:
                return json.dumps({
                    "status": "already_exists",
                    "tenant_id": str(existing.external_id),
                    "message": f"Tenant {first_name} {last_name} already exists.",
                })

            # Always create the tenant first
            shadow_user = create_shadow_user(
                db,
                org_id=1,
                creator_id=resolve_account_id(),
                first_name=first_name,
                last_name=last_name,
                email=kwargs.get("email"),
                phone=kwargs.get("phone"),
                user_type="tenant",
            )
            tenant = SqlTenant(
                creator_id=resolve_account_id(),
                user_id=shadow_user.id,
                created_at=datetime.now(UTC),
            )
            db.add(tenant)
            db.flush()

            result: dict[str, Any] = {
                "status": "ok",
                "tenant_id": str(tenant.external_id),
                "message": f"Created tenant {first_name} {last_name}.",
            }

            # Link tenant to unit if property_id + unit_id provided
            property_row = None
            unit = None
            if kwargs.get("property_id") and kwargs.get("unit_id"):
                from sqlalchemy import select

                from db.models import Property as SqlProperty, Unit as SqlUnit
                property_row = db.execute(
                    select(SqlProperty).where(SqlProperty.id == kwargs["property_id"])
                ).scalar_one_or_none()
                unit = db.execute(
                    select(SqlUnit).where(
                        SqlUnit.id == kwargs["unit_id"],
                        SqlUnit.property_id == kwargs["property_id"],
                    )
                ).scalar_one_or_none()
                if unit:
                    unit.tenant_id = tenant.id
                    result["unit_label"] = unit.label
                    result["message"] = f"Created tenant {first_name} {last_name} on {unit.label}."

            # Create lease if we have enough detail (dates required)
            if unit and kwargs.get("lease_start") and kwargs.get("lease_end"):
                from datetime import date as _date

                from db.models import Lease as SqlLease
                lease = SqlLease(
                    id=str(uuid.uuid4()),
                    creator_id=resolve_account_id(),
                    tenant_id=tenant.id,
                    unit_id=unit.id,
                    property_id=kwargs["property_id"],
                    start_date=_date.fromisoformat(kwargs["lease_start"]),
                    end_date=_date.fromisoformat(kwargs["lease_end"]),
                    rent_amount=kwargs.get("rent_amount", 0),
                    payment_status="current",
                    created_at=datetime.now(UTC),
                )
                db.add(lease)
                result["lease_id"] = str(lease.id)
                result["message"] = f"Created tenant {first_name} {last_name} with lease on {unit.label}."

            # Capture any partial lease info that didn't make it into a record
            partial_bits = []
            if kwargs.get("rent_amount") and "lease_id" not in result:
                partial_bits.append(f"rent ${kwargs['rent_amount']}/mo")
            if kwargs.get("lease_start") and "lease_id" not in result:
                partial_bits.append(f"start {kwargs['lease_start']}")
            if kwargs.get("lease_end") and "lease_id" not in result:
                partial_bits.append(f"end {kwargs['lease_end']}")

            # Combine explicit notes + partial lease info into tenant context
            context_parts = []
            notes = kwargs.get("notes", "").strip()
            if notes:
                context_parts.append(notes)
            if partial_bits:
                context_parts.append(f"Lease info (partial): {', '.join(partial_bits)}")

            if context_parts:
                from sqlalchemy.orm.attributes import flag_modified
                new_ctx = "\n".join(context_parts)
                existing_ctx = tenant.context or ""
                tenant.context = (existing_ctx + "\n" + new_ctx).strip() if existing_ctx else new_ctx
                flag_modified(tenant, "context")

            db.commit()
            fields = [
                field
                for field in [
                    _action_card_field("Email", shadow_user.email),
                    _action_card_field("Phone", shadow_user.phone),
                    _action_card_field("Property", property_row.name or property_row.address_line1 if property_row else None),
                    _action_card_field("Unit", unit.label if unit else None),
                    _action_card_field("Lease start", kwargs.get("lease_start")),
                    _action_card_field("Lease end", kwargs.get("lease_end")),
                    _action_card_field("Rent", f"${kwargs['rent_amount']}/mo" if kwargs.get("rent_amount") else None),
                ]
                if field
            ]
            links = [{
                "label": "Open tenant",
                "entity_type": "tenant",
                "entity_id": str(tenant.external_id),
            }]
            if property_row:
                links.append({
                    "label": "Property",
                    "entity_type": "property",
                    "entity_id": str(property_row.id),
                })
            if unit:
                links.append({
                    "label": "Unit",
                    "entity_type": "unit",
                    "entity_id": str(unit.id),
                    "property_id": str(unit.property_id),
                })
            _queue_chat_message(
                body=result["message"],
                message_type=MessageType.ACTION,
                action_card={
                    "kind": "tenant",
                    "title": f"{first_name} {last_name}",
                    "summary": result["message"],
                    "fields": fields,
                    "links": links,
                    "units": [],
                },
            )
            return json.dumps(result)
        except Exception as e:
            db.rollback()
            import traceback
            print(f"[create_tenant] ERROR: {e}")
            traceback.print_exc()
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()


class CreateDocumentTool(Tool):
    """Generate a PDF document and save it as an account-owned document."""

    @property
    def name(self) -> str:
        return "create_document"

    @property
    def description(self) -> str:
        return (
            "Create a PDF document directly for the user and store it in the Documents area. "
            "Use this when the user asked for a draft notice, letter, or other document deliverable."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "description": "Human-readable document title"},
                "html": {"type": "string", "description": "Raw HTML fragment to render inside the document shell"},
                "content": {"type": "string", "description": "Legacy plain-text body; used only as fallback when html is omitted"},
                "summary": {"type": "string", "description": "Short summary shown on the chat card"},
                "filename": {"type": "string", "description": "Optional output filename; .pdf will be added if missing"},
                "document_type": {
                    "type": "string",
                    "enum": ["lease", "invoice", "notice", "inspection", "insurance", "other"],
                    "description": "Document category for the Documents area",
                },
                "document_category": {
                    "type": "string",
                    "enum": ["general", "legal", "compliance"],
                    "description": (
                        "Broader drafting category. Use 'legal' or 'compliance' when the document "
                        "depends on governing law or statutory requirements."
                    ),
                },
                "risk_score": {
                    "type": "number",
                    "description": (
                        "Risk score for the document request. Legal/compliance documents should "
                        "usually be 7 or higher."
                    ),
                },
                "property_id": {"type": "string", "description": "Property to tag on the generated document"},
                "unit_id": {"type": "string", "description": "Unit to tag on the generated document"},
                "tenant_id": {"type": "string", "description": "Tenant external UUID to tag on the generated document"},
                "task_id": {"type": "string", "description": "Related task ID, if this document is part of task work"},
                "legal_requirements": {
                    "type": "object",
                    "description": (
                        "Required for legal/compliance documents. Summarize the governing law, "
                        "required fields, and any stale or low-confidence fields before generating "
                        "the document. Never infer missing landlord/manager or pay-to details."
                    ),
                    "properties": {
                        "jurisdiction": {
                            "type": "string",
                            "description": "Jurisdiction governing the notice, for example 'Washington, USA'",
                        },
                        "citation": {
                            "type": "string",
                            "description": "Legal citation or statutory form reference used for this notice",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Short explanation of why the listed fields are legally required",
                        },
                        "required_fields": {
                            "type": "array",
                            "description": "Fields the governing law requires in the notice",
                            "items": {
                                "oneOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "field": {"type": "string"},
                                            "required": {"type": "boolean"},
                                        },
                                    },
                                ],
                            },
                        },
                        "missing_fields": {
                            "type": "array",
                            "description": "Required fields still missing from current context",
                            "items": {
                                "oneOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "field": {"type": "string"},
                                        },
                                    },
                                ],
                            },
                        },
                        "low_confidence_fields": {
                            "type": "array",
                            "description": (
                                "Required fields that are only supported by stale or low-confidence "
                                "sources and must be confirmed with the property manager first"
                            ),
                            "items": {
                                "oneOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "field": {"type": "string"},
                                            "source": {"type": "string"},
                                            "confidence": {"type": "string"},
                                        },
                                    },
                                ],
                            },
                        },
                    },
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        legal_requirements = kwargs.get("legal_requirements") or {}
        if _is_legal_or_compliance_document(kwargs):
            citation = (
                legal_requirements.get("citation")
                if isinstance(legal_requirements, dict)
                else None
            )
            jurisdiction = (
                legal_requirements.get("jurisdiction")
                if isinstance(legal_requirements, dict)
                else None
            )
            reason = (
                legal_requirements.get("reason")
                if isinstance(legal_requirements, dict)
                else None
            )
            missing_fields = _extract_legal_field_names(
                legal_requirements.get("missing_fields") if isinstance(legal_requirements, dict) else None
            )
            low_confidence_fields = _extract_legal_field_names(
                legal_requirements.get("low_confidence_fields") if isinstance(legal_requirements, dict) else None
            )
            required_fields = _extract_legal_field_names(
                legal_requirements.get("required_fields") if isinstance(legal_requirements, dict) else None
            )
            unresolved_placeholders = _find_unresolved_placeholders(
                kwargs.get("html"),
                kwargs.get("content"),
            )
            if not isinstance(legal_requirements, dict) or not citation or not required_fields:
                return json.dumps({
                    "status": "error",
                    "message": (
                        "Before creating this legal or compliance document, I need to confirm the "
                        "governing law and which fields it requires. Please research the applicable "
                        "law first and ask the property manager for any required details that are still missing."
                    ),
                })
            if missing_fields:
                return json.dumps({
                    "status": "error",
                    "message": _legal_notice_block_message(
                        missing_fields=missing_fields,
                        citation=citation,
                        jurisdiction=jurisdiction,
                        reason=reason,
                    ),
                })
            if low_confidence_fields:
                return json.dumps({
                    "status": "error",
                    "message": _legal_notice_block_message(
                        missing_fields=low_confidence_fields,
                        citation=citation,
                        jurisdiction=jurisdiction,
                        reason=(
                            "The available support for these fields is stale or low-confidence, so "
                            "I need the property manager to confirm the current information first."
                        ),
                    ),
                })
            if unresolved_placeholders:
                placeholder_fields = [item.strip("[]") for item in unresolved_placeholders]
                return json.dumps({
                    "status": "error",
                    "message": _legal_notice_block_message(
                        missing_fields=placeholder_fields,
                        citation=citation,
                        jurisdiction=jurisdiction,
                        reason=(
                            "The draft still contains unresolved placeholders, so the legally required "
                            "details have not been provided yet."
                        ),
                    ),
                })
        summary = kwargs.get("summary") or f"Generated {kwargs.get('document_type', 'document')} PDF."
        document_id, filename = await _create_generated_document(
            title=kwargs["title"],
            html_content=kwargs.get("html"),
            text_content=kwargs.get("content"),
            filename=kwargs.get("filename"),
            document_type=kwargs.get("document_type", "other"),
            summary=summary,
            property_id=kwargs.get("property_id"),
            unit_id=kwargs.get("unit_id"),
            tenant_id=kwargs.get("tenant_id"),
            task_id=kwargs.get("task_id"),
        )

        fields = [
            field
            for field in [
                _action_card_field("Type", kwargs.get("document_type", "other")),
                _action_card_field("Format", "PDF"),
            ]
            if field
        ]
        _queue_chat_message(
            body=f"Created document: {filename}",
            message_type=MessageType.ACTION,
            action_card={
                "kind": "document",
                "title": filename,
                "summary": summary,
                "fields": fields,
                "links": [
                    {
                        "label": "Download PDF",
                        "entity_type": "document",
                        "entity_id": document_id,
                    },
                    {
                        "label": "Open document",
                        "entity_type": "document",
                        "entity_id": document_id,
                    },
                ],
                "units": [],
            },
        )
        return json.dumps({
            "status": "ok",
            "document_id": document_id,
            "filename": filename,
            "message": f"Document created: {filename}",
        })


class ReadDocumentTool(Tool):
    """Read uploaded document content, search document text, or list recent documents."""

    @property
    def name(self) -> str:
        return "read_document"

    @property
    def description(self) -> str:
        return (
            "Access uploaded documents. Use document_id to read a specific document's "
            "extracted data and raw text. Use query to search across all document text. "
            "Use list_recent to see what documents exist."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "Look up a specific document by ID",
                },
                "query": {
                    "type": "string",
                    "description": "Search document text for relevant content (keyword search)",
                },
                "list_recent": {
                    "type": "boolean",
                    "description": "List the most recent uploaded documents",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.models import Document
        from db.session import SessionLocal

        db = SessionLocal.session_factory()
        try:
            request_detail = {
                "document_id": kwargs.get("document_id"),
                "query": (kwargs.get("query") or "")[:200],
                "list_recent": bool(kwargs.get("list_recent")),
            }
            # --- Read specific document ---
            if kwargs.get("document_id"):
                doc = db.query(Document).filter_by(id=kwargs["document_id"]).first()
                if not doc:
                    detail = {**request_detail, "reason": "not_found"}
                    _log_tool_error("read_document", "document not found", detail=detail)
                    return json.dumps({"status": "error", "message": "Document not found", "detail": detail})
                if doc.status == "error":
                    detail = {
                        **request_detail,
                        "filename": doc.filename,
                        "document_status": doc.status,
                        "document_type": doc.document_type,
                        "progress": doc.progress,
                        "error_message": doc.error_message,
                    }
                    _log_tool_error("read_document", f"document in error state for {doc.filename}", detail=detail)
                    return json.dumps({
                        "status": "error",
                        "message": doc.error_message or "Document processing failed",
                        "detail": detail,
                        "hint": "Retry analyze_document after fixing the processing issue.",
                    })
                # Hint when document hasn't been analyzed yet
                if doc.status == "pending" and not doc.raw_text:
                    return json.dumps({
                        "status": "ok",
                        "document": {
                            "id": doc.id,
                            "filename": doc.filename,
                            "document_type": doc.document_type,
                            "status": doc.status,
                            "hint": "This document has not been analyzed yet. Use analyze_document to extract its contents.",
                        },
                    })
                raw_preview = (doc.raw_text or "")[:3000]
                return json.dumps({
                    "status": "ok",
                    "document": {
                        "id": doc.id,
                        "filename": doc.filename,
                        "document_type": doc.document_type,
                        "status": doc.status,
                        "extracted_data": doc.extracted_data,
                        "extraction_meta": doc.extraction_meta,
                        "context": doc.context,
                        "raw_text_preview": raw_preview,
                        "raw_text_chars": len(doc.raw_text or ""),
                    },
                })

            # --- Search document text ---
            if kwargs.get("query"):
                query_lower = kwargs["query"].lower()
                docs = db.query(Document).filter(Document.raw_text.isnot(None)).all()
                matches = []
                for d in docs:
                    if query_lower in (d.raw_text or "").lower():
                        matches.append({
                            "id": d.id,
                            "filename": d.filename,
                            "status": d.status,
                            "preview": (d.raw_text or "")[:500],
                        })
                    if len(matches) >= 5:
                        break
                return json.dumps({"status": "ok", "matches": matches})

            # --- List recent documents ---
            if kwargs.get("list_recent"):
                docs = (
                    db.query(Document)
                    .order_by(Document.created_at.desc())
                    .limit(10)
                    .all()
                )
                items = []
                for doc in docs:
                    extracted = doc.extracted_data or {}
                    leases = extracted.get("leases", []) if isinstance(extracted, dict) else []
                    items.append({
                        "id": doc.id,
                        "filename": doc.filename,
                        "status": doc.status,
                        "document_type": doc.document_type,
                        "leases_found": len(leases),
                        "created_at": doc.created_at.isoformat() if doc.created_at else None,
                    })
                return json.dumps({"status": "ok", "documents": items})

            return json.dumps({"status": "error", "message": "Provide document_id, query, or list_recent"})
        except Exception as e:
            detail = {
                "document_id": kwargs.get("document_id"),
                "query": (kwargs.get("query") or "")[:200],
                "list_recent": bool(kwargs.get("list_recent")),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(limit=8),
            }
            _log_tool_error("read_document", f"crashed: {type(e).__name__}", detail=detail)
            return json.dumps({"status": "error", "message": str(e), "detail": detail})
        finally:
            db.close()


class AnalyzeDocumentTool(Tool):
    """Trigger text extraction and AI analysis on an unprocessed document."""

    @property
    def name(self) -> str:
        return "analyze_document"

    @property
    def description(self) -> str:
        return (
            "Trigger text extraction and AI analysis on a document that hasn't been "
            "processed yet (status='pending'). Use this when a user attaches a document "
            "in chat and asks about its contents. Returns the analysis result once complete."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["document_id"],
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The ID of the document to analyze",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.models import Document
        from db.session import SessionLocal

        db = SessionLocal.session_factory()
        try:
            doc = db.query(Document).filter_by(id=kwargs["document_id"]).first()
            if not doc:
                detail = {"document_id": kwargs["document_id"], "reason": "not_found"}
                _log_tool_error("analyze_document", "document not found", detail=detail)
                return json.dumps({"status": "error", "message": "Document not found", "detail": detail})
            if doc.status == "done":
                return json.dumps({"status": "already_done", "message": "Document already analyzed"})
            if doc.status == "processing":
                return json.dumps({"status": "in_progress", "message": "Document is currently being analyzed"})

            from llm.document_processor import process_document
            await process_document(doc.id)

            db.refresh(doc)
            return json.dumps({
                "status": "ok",
                "message": "Document analysis complete",
                "document_status": doc.status,
                "filename": doc.filename,
            })
        except Exception as e:
            detail = {
                "document_id": kwargs.get("document_id"),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(limit=8),
            }
            _log_tool_error("analyze_document", f"crashed: {type(e).__name__}", detail=detail)
            return json.dumps({"status": "error", "message": str(e), "detail": detail})
        finally:
            db.close()


class CreateSuggestionTool(Tool):
    """Create a suggestion for the property manager to review."""

    @property
    def name(self) -> str:
        return "create_suggestion"

    @property
    def description(self) -> str:
        return (
            "Create a suggestion for the property manager to review and approve. "
            "Use this for actions that benefit from human review — creating entities "
            "from documents, proposing lease changes, compliance actions, etc. "
            "Never use create_suggestion when the user explicitly asked for the draft itself in chat instead of a suggestion or task. "
            "When the blocker is a user deliverable inside the current task, ask the user first "
            "whether they want a suggestion created. "
            "Set risk_score: 0 = safe to auto-approve, 10 = must have human review. "
            "Low-risk routine actions (creating a property from a clear document) can "
            "be 1-3. High-risk actions (legal notices, deposit deductions) should be 7-10."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["title", "body", "suggestion_type", "risk_score"],
            "properties": {
                "title": {"type": "string", "description": "Short title for the suggestion"},
                "body": {"type": "string", "description": "Detailed context and reasoning"},
                "suggestion_type": {
                    "type": "string",
                    "enum": ["rent", "maintenance", "leasing", "compliance"],
                    "description": "Category — maps to autonomy level for approval routing",
                },
                "risk_score": {
                    "type": "integer",
                    "description": "0-10: risk of auto-approving. 0=safe, 10=must review",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "How urgent is this suggestion (default: medium)",
                },
                "property_id": {"type": "string", "description": "Link to a property"},
                "unit_id": {"type": "string", "description": "Link to a unit"},
                "document_id": {"type": "string", "description": "Link to a source document"},
                "task_id": {"type": "string", "description": "Link to the current task when this suggestion belongs to an existing task"},
                "action_payload": {
                    "type": "object",
                    "description": "Data needed to execute this suggestion when accepted",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = str(kwargs.get("task_id") or _resolve_task_id_from_active_conversation() or "")
        if _current_message_requests_direct_draft():
            from llm.tracing import log_trace

            message = _direct_draft_block_message()
            log_trace(
                "suggestion_blocked_direct_draft",
                "policy",
                message,
                task_id=task_id or None,
                detail={"title": kwargs["title"]},
            )
            return json.dumps({"status": "error", "message": message})
        if task_id and _current_task_notice_service_reported(task_id):
            from llm.tracing import log_trace

            log_trace(
                "suggestion_blocked_same_task",
                "policy",
                _same_task_handoff_block_message(),
                task_id=task_id,
                detail={"title": kwargs["title"]},
            )
            return json.dumps({"status": "error", "message": _same_task_handoff_block_message()})
        action_payload = _normalize_current_task_suggestion_payload(
            task_id=task_id,
            title=kwargs["title"],
            body=kwargs["body"],
            action_payload=kwargs.get("action_payload"),
        )
        if action_payload.get("action") == "request_file_upload" and task_id:
            if not _has_user_confirmed_upload_request(task_id):
                message = (
                    "Ask the user first whether they want a suggestion created for this upload request. "
                    "Explain what file is needed and why the current task is blocked."
                )
                from llm.tracing import log_trace

                log_trace(
                    "suggestion_deferred_pending_user_confirmation",
                    "policy",
                    message,
                    task_id=task_id,
                    detail={"action_payload": action_payload},
                )
                return json.dumps({"status": "error", "message": message})

        simulated_id = _queue_simulation_suggestion({
            "title": kwargs["title"],
            "body": kwargs["body"],
            "category": kwargs["suggestion_type"],
            "urgency": kwargs.get("urgency", "medium"),
            "property_id": kwargs.get("property_id"),
            "unit_id": kwargs.get("unit_id"),
            "document_id": kwargs.get("document_id"),
            "task_id": task_id or None,
            "action_payload": action_payload or None,
            "risk_score": kwargs.get("risk_score", 5),
            "suggestion_type": kwargs["suggestion_type"],
        })
        if simulated_id is not None:
            return json.dumps({
                "status": "ok",
                "suggestion_id": simulated_id,
                "message": f"Suggestion simulated: {kwargs['title']}",
            })

        options: list[SuggestionOption] = []
        if action_payload.get("action") == "request_file_upload":
            options = [
                SuggestionOption(key="upload", label=action_payload.get("requested_file_label", "Upload file"), action="request_file_upload", variant="default"),
                SuggestionOption(key="dismiss", label="Dismiss", action="reject_task", variant="ghost"),
            ]

        try:
            suggestion_id = _create_suggestion(
                title=kwargs["title"],
                ai_context=kwargs["body"],
                category=kwargs["suggestion_type"],
                urgency=kwargs.get("urgency", "medium"),
                action_payload=action_payload or None,
                options=options,
                task_id=task_id or None,
                property_id=kwargs.get("property_id"),
                unit_id=kwargs.get("unit_id"),
                document_id=kwargs.get("document_id"),
                risk_score=kwargs.get("risk_score", 5),
                suggestion_type=kwargs["suggestion_type"],
            )
            if action_payload.get("action") == "request_file_upload" and task_id:
                _mark_task_waiting_on_upload_request(
                    task_id=task_id,
                    requested_file_label=action_payload.get("requested_file_label", "requested file"),
                    instructions=action_payload.get("instructions") or kwargs["body"],
                )

            return json.dumps({
                "status": "ok",
                "suggestion_id": str(suggestion_id),
                "message": f"Suggestion created: {kwargs['title']}",
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})


class CreateScheduledTaskTool(Tool):
    """Create a recurring or one-shot scheduled task."""

    @property
    def name(self) -> str:
        return "create_scheduled_task"

    @property
    def description(self) -> str:
        return (
            "Create a scheduled task that runs the AI agent on a recurring schedule. "
            "Use for recurring checks (lease expiry, rent reminders, maintenance schedules). "
            "Schedule can be: cron expression ('0 9 * * 1'), interval ('every 4h'), "
            "or named ('daily', 'weekly', 'monthly'). The prompt describes what the "
            "agent should do each time it runs."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["name", "prompt", "schedule"],
            "properties": {
                "name": {"type": "string", "description": "Human-friendly name for this scheduled task"},
                "prompt": {"type": "string", "description": "What the agent should do each run (natural language)"},
                "schedule": {
                    "type": "string",
                    "description": "Cron expression, interval, or named schedule (e.g. '0 9 * * 1', 'every 4h', 'weekly')",
                },
                "repeat": {
                    "type": "integer",
                    "description": "Number of times to run (omit for forever)",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.models import ScheduledTask
        from db.session import SessionLocal
        from handlers.scheduler import human_schedule, next_run, parse_schedule

        name = kwargs["name"]
        prompt = kwargs["prompt"]
        raw_schedule = kwargs["schedule"]

        cron_expr = parse_schedule(raw_schedule)
        display = human_schedule(cron_expr)
        nxt = next_run(cron_expr)

        db = SessionLocal.session_factory()
        try:
            import uuid
            from datetime import UTC, datetime

            task = ScheduledTask(
                id=str(uuid.uuid4()),
                creator_id=resolve_account_id(),
                name=name,
                prompt=prompt,
                schedule=cron_expr,
                schedule_display=display,
                enabled=True,
                state="scheduled",
                repeat=kwargs.get("repeat"),
                next_run_at=nxt,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(task)
            db.commit()

            return json.dumps({
                "status": "ok",
                "scheduled_task_id": task.id,
                "schedule": display,
                "next_run": nxt.isoformat(),
                "message": f"Scheduled task '{name}' created — {display}, next run {nxt.strftime('%b %d at %H:%M')}.",
            })
        except Exception as e:
            db.rollback()
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()


class UpdateOnboardingTool(Tool):
    """Mark onboarding steps done or dismiss onboarding entirely."""

    @property
    def name(self) -> str:
        return "update_onboarding"

    @property
    def description(self) -> str:
        return (
            "Update onboarding progress. Either mark a specific step as done "
            "(add_property, upload_document, tell_concerns) or dismiss onboarding "
            "entirely when the user wants to skip."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "step": {
                    "type": "string",
                    "enum": ["add_property", "upload_document", "tell_concerns"],
                    "description": "The step to mark as done",
                },
                "dismiss": {
                    "type": "boolean",
                    "description": "Set to true to dismiss onboarding entirely",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.session import SessionLocal
        from gql.services.settings_service import (
            dismiss_onboarding,
            get_onboarding_state,
            update_onboarding_step,
        )
        from llm.tracing import log_trace

        step = kwargs.get("step")
        dismiss = kwargs.get("dismiss", False)

        db = SessionLocal.session_factory()
        try:
            if dismiss:
                state = dismiss_onboarding(db)
                db.commit()
                log_trace("onboarding", "tool", "Onboarding dismissed by agent", detail=state)
                return json.dumps({"status": "ok", "message": "Onboarding dismissed."})

            if step:
                state = get_onboarding_state(db)
                if not state or state.get("status") != "active":
                    return json.dumps({"status": "ok", "message": "Onboarding is not active."})
                update_onboarding_step(db, step=step)
                db.commit()
                log_trace("onboarding", "tool", f"Step '{step}' marked done", detail={"step": step})
                return json.dumps({"status": "ok", "message": f"Step '{step}' marked as done."})

            return json.dumps({"status": "ok", "message": "No action taken."})
        except Exception as e:
            db.rollback()
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()
