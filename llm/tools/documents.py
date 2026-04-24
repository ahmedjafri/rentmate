"""Document tools: create (PDF generation), read, analyze."""
import hashlib
import json
import re
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import MessageType

from llm.tools._common import (
    Tool,
    ToolMode,
    _action_card_field,
    _load_entity_by_public_id,
    _load_tenant_by_public_id,
    _log_tool_error,
    _queue_chat_message,
)


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
    from gql.services.document_service import dump_document_extraction_meta
    from llm.generated_documents import render_document_async
    from llm.tools._common import tool_session

    with tool_session() as db:
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
    return doc_id, resolved_filename


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

    mode = ToolMode.READ_ONLY

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

    mode = ToolMode.READ_ONLY

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


__all__ = ["CreateDocumentTool", "ReadDocumentTool", "AnalyzeDocumentTool"]
