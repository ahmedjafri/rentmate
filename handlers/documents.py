import hashlib
import uuid as _uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backends.wire import storage_backend
from db.lib import apply_document_extraction, compute_suggestions, find_candidate_properties, group_suggestions
from db.models import Document, DocumentTask
from handlers.deps import extract_json, get_db, require_user

router = APIRouter()

_SUGGESTION_FIELD_LABELS: Dict[str, List[Dict[str, str]]] = {
    "location": [
        {"key": "property_address", "label": "Property Address"},
        {"key": "unit_label",       "label": "Unit Label"},
    ],
    "tenant": [
        {"key": "tenant_first_name", "label": "First Name"},
        {"key": "tenant_last_name",  "label": "Last Name"},
        {"key": "tenant_email",      "label": "Email"},
        {"key": "tenant_phone",      "label": "Phone"},
    ],
    "lease": [
        {"key": "lease_start_date", "label": "Start Date (YYYY-MM-DD)"},
        {"key": "lease_end_date",   "label": "End Date (YYYY-MM-DD)"},
        {"key": "monthly_rent",     "label": "Monthly Rent ($)"},
    ],
}


class SuggestionChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class SuggestionChatRequest(BaseModel):
    category: str
    fields: Dict[str, Any]
    description: str
    message: str
    history: List[SuggestionChatMessage] = Field(default_factory=list)


@router.post("/upload-document")
async def upload_document(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    document_type: str = Form("lease"),
    task_id: Optional[str] = Form(None),
    skip_extraction: bool = Form(False),
    db: Session = Depends(get_db),
):
    await require_user(request)
    file_bytes = await file.read()
    checksum = hashlib.sha256(file_bytes).hexdigest()

    existing = db.query(Document).filter(Document.sha256_checksum == checksum).one_or_none()
    if existing:
        if task_id:
            exists = db.query(DocumentTask).filter_by(document_id=existing.id, task_id=task_id).one_or_none()
            if not exists:
                db.add(DocumentTask(document_id=existing.id, task_id=task_id))
                db.commit()
        return {"document_id": existing.id, "duplicate": True}

    doc_id = str(_uuid.uuid4())
    storage_path = f"documents/{doc_id}/{file.filename}"

    await storage_backend.upload(storage_path, data=file_bytes, content_type=file.content_type or "application/octet-stream")

    doc = Document(
        id=doc_id,
        filename=file.filename,
        content_type=file.content_type,
        storage_path=storage_path,
        document_type=document_type,
        status="pending",
        sha256_checksum=checksum,
    )
    db.add(doc)
    if task_id:
        db.add(DocumentTask(document_id=doc_id, task_id=task_id))
    db.commit()

    if not skip_extraction:
        from llm.document_processor import process_document
        background_tasks.add_task(process_document, doc_id)

    return {"document_id": doc_id}


@router.post("/document/{document_id}/reprocess")
async def reprocess_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    """Clear extracted data and re-run document processing with the current extraction prompt."""
    await require_user(request)
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.storage_path:
        raise HTTPException(status_code=400, detail="Document has no stored file to reprocess")

    doc.status = "pending"
    doc.progress = None
    doc.extracted_data = None
    doc.extraction_meta = None
    doc.context = None
    doc.error_message = None
    doc.processed_at = None
    db.commit()

    from llm.document_processor import process_document
    background_tasks.add_task(process_document, document_id)

    return {"ok": True, "document_id": document_id}


@router.get("/document/{document_id}")
async def get_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "document_type": doc.document_type,
        "status": doc.status,
        "progress": doc.progress,
        "extracted_data": doc.extracted_data,
        "extraction_meta": doc.extraction_meta,
        "context": doc.context,
        "raw_text": doc.raw_text,
        "error_message": doc.error_message,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
    }


@router.get("/document/{document_id}/suggestions")
async def get_document_suggestions(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status != "done" or not doc.extracted_data:
        return {"groups": [], "extracted": {}}
    suggestions = compute_suggestions(db, doc.extracted_data)
    states = doc.suggestion_states or {}
    groups = group_suggestions(str(doc.id), filename=doc.filename, suggestions=suggestions, suggestion_states=states, db=db)
    return {"groups": groups, "extracted": doc.extracted_data}


@router.get("/document/{document_id}/property-candidates")
async def get_property_candidates(
    document_id: str,
    address: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    candidates = find_candidate_properties(db, address=address)
    return {"candidates": candidates}


@router.post("/document/{document_id}/confirm")
async def confirm_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    apply_only = body.pop("apply", None)
    extracted_data = {**(doc.extracted_data or {}), **body}

    result = apply_document_extraction(db, extracted_data, apply_only=apply_only)
    return result


@router.delete("/document/{document_id}")
async def delete_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Best-effort: delete from storage and vector index
    try:
        await storage_backend.delete(doc.storage_path)
    except Exception as e:
        print(f"[delete_document] storage delete failed (ignored): {e}")
    try:
        from backends.wire import vector_backend
        vector_backend.delete_document(document_id)
    except Exception as e:
        print(f"[delete_document] vector delete failed (ignored): {e}")

    db.delete(doc)
    db.commit()
    return {"ok": True}


@router.get("/document/{document_id}/tags")
async def get_document_tags(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return all tags for a document, enriched with entity names."""
    await require_user(request)
    from db.models import DocumentTag, Property as SqlProperty, Tenant as SqlTenant, Unit as SqlUnit
    from db.queries import format_address as _format_address
    tags = db.query(DocumentTag).filter(DocumentTag.document_id == document_id).all()
    result = []
    for tag in tags:
        entry: Dict[str, Any] = {
            "id": str(tag.id),
            "tag_type": tag.tag_type,
            "property_id": str(tag.property_id) if tag.property_id else None,
            "unit_id": str(tag.unit_id) if tag.unit_id else None,
            "tenant_id": str(tag.tenant_id) if tag.tenant_id else None,
        }
        if tag.property_id:
            prop = db.query(SqlProperty).filter_by(id=tag.property_id).first()
            if prop:
                entry["label"] = prop.name or _format_address(prop)
        if tag.unit_id:
            unit = db.query(SqlUnit).filter_by(id=tag.unit_id).first()
            if unit:
                prop = db.query(SqlProperty).filter_by(id=unit.property_id).first()
                prop_label = (prop.name or _format_address(prop)) if prop else ""
                entry["label"] = f"{unit.label}" + (f" — {prop_label}" if prop_label else "")
        if tag.tenant_id:
            tenant = db.query(SqlTenant).filter_by(id=tag.tenant_id).first()
            if tenant:
                entry["label"] = f"{tenant.first_name} {tenant.last_name}"
        result.append(entry)
    return result


@router.post("/document/{document_id}/tags")
async def add_document_tag_rest(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Add a tag linking a document to a property, unit, or tenant."""
    await require_user(request)
    from db.models import DocumentTag
    body = await request.json()
    tag_type = body.get("tag_type")
    property_id = body.get("property_id")
    unit_id = body.get("unit_id")
    tenant_id = body.get("tenant_id")
    if not tag_type:
        raise HTTPException(status_code=400, detail="tag_type required")
    # Prevent duplicates
    q = db.query(DocumentTag).filter_by(document_id=document_id)
    if property_id:
        q = q.filter_by(property_id=property_id)
    if unit_id:
        q = q.filter_by(unit_id=unit_id)
    if tenant_id:
        q = q.filter_by(tenant_id=tenant_id)
    existing = q.first()
    if existing:
        return {"id": str(existing.id), "existed": True}
    tag = DocumentTag(
        id=str(_uuid.uuid4()),
        document_id=document_id,
        tag_type=tag_type,
        property_id=property_id,
        unit_id=unit_id,
        tenant_id=tenant_id,
    )
    db.add(tag)
    db.commit()
    return {"id": str(tag.id), "existed": False}


@router.delete("/document-tag/{tag_id}")
async def delete_document_tag(
    tag_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Remove a document tag."""
    await require_user(request)
    from db.models import DocumentTag
    tag = db.query(DocumentTag).filter_by(id=tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.delete(tag)
    db.commit()
    return {"ok": True}


@router.get("/properties/{property_id}/documents")
async def get_property_documents(
    property_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return documents tagged to a property."""
    await require_user(request)
    from db.models import Document as DocModel, DocumentTag
    tags = db.query(DocumentTag).filter(DocumentTag.property_id == property_id).all()
    doc_ids = [t.document_id for t in tags]
    if not doc_ids:
        return []
    docs = db.query(DocModel).filter(DocModel.id.in_(doc_ids)).order_by(DocModel.created_at.desc()).all()
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "document_type": d.document_type,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]


@router.get("/documents")
async def list_documents(
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    docs = (
        db.query(Document)
        .order_by(Document.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "document_type": d.document_type,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "processed_at": d.processed_at.isoformat() if d.processed_at else None,
        }
        for d in docs
    ]


@router.get("/dashboard/suggestions")
async def get_dashboard_suggestions(
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    docs = (
        db.query(Document)
        .filter(Document.status == "done", Document.extracted_data.isnot(None))
        .order_by(Document.processed_at.desc())
        .limit(50)
        .all()
    )
    all_groups = []
    for doc in docs:
        suggestions = compute_suggestions(db, doc.extracted_data)
        if not suggestions:
            continue
        states = doc.suggestion_states or {}
        groups = group_suggestions(str(doc.id), filename=doc.filename, suggestions=suggestions, suggestion_states=states, db=db)
        all_groups.extend(groups)
    return {"groups": all_groups}


@router.post("/document/{document_id}/confirm-all")
async def confirm_all_groups(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Apply all included suggestion groups in one shot.

    Body: {
      groups: [{category, suggestion_ids, fields, lease_index}],
      excluded: [group_id, ...],
      property_id_overrides: {"0": "existing-property-id", ...}
    }
    """
    await require_user(request)
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    body = await request.json()
    included = body.get("groups", [])
    excluded_ids = set(body.get("excluded", []))
    property_id_overrides: Dict[str, str] = body.get("property_id_overrides", {})

    # Group by lease_index so each lease is applied atomically
    from collections import defaultdict
    by_lease: Dict[int, Dict] = defaultdict(lambda: {"merged": {}, "suggestion_ids": [], "cats": set()})

    for g in included:
        gid = g.get("group_id", g.get("category", ""))
        if gid in excluded_ids:
            continue
        lease_index = int(g.get("lease_index", 0))
        by_lease[lease_index]["merged"].update(g.get("fields", {}))
        by_lease[lease_index]["suggestion_ids"].extend(g.get("suggestion_ids", []))
        by_lease[lease_index]["cats"].add(g.get("group_id", g.get("category", "")))

    from db.models import DocumentTag
    all_created: List[str] = []
    tagged_property_ids: set = set()
    for lease_index, lease_data in sorted(by_lease.items()):
        override = property_id_overrides.get(str(lease_index))
        result = apply_document_extraction(
            db,
            lease_data["merged"],
            apply_only=lease_data["suggestion_ids"],
            property_id_override=override,
        )
        all_created.extend(result.get("created", []))
        # Auto-tag document to the property
        pid = result.get("property_id")
        if pid and pid not in tagged_property_ids:
            exists = db.query(DocumentTag).filter_by(document_id=document_id, property_id=pid).first()
            if not exists:
                db.add(DocumentTag(
                    id=str(_uuid.uuid4()),
                    document_id=document_id,
                    tag_type="property",
                    property_id=pid,
                ))
            tagged_property_ids.add(pid)

    # Update suggestion states
    states = dict(doc.suggestion_states or {})
    for g in included:
        gid = g.get("group_id", g.get("category", ""))
        if gid not in excluded_ids:
            states[gid] = "accepted"
    for gid in excluded_ids:
        states[gid] = "rejected"
    doc.suggestion_states = states
    db.commit()

    return {"created": all_created}


@router.post("/document/{document_id}/suggestion-group/accept")
async def accept_suggestion_group(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    body = await request.json()
    category = body.get("category")
    suggestion_ids = body.get("suggestion_ids", [])
    field_overrides = body.get("fields", {})

    extracted = {**(doc.extracted_data or {}), **field_overrides}
    result = apply_document_extraction(db, extracted, apply_only=suggestion_ids)

    states = dict(doc.suggestion_states or {})
    states[category] = "accepted"
    doc.suggestion_states = states
    db.commit()

    return result


@router.post("/document/{document_id}/suggestion-group/reject")
async def reject_suggestion_group(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    doc = db.query(Document).filter(Document.id == document_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    body = await request.json()
    group_id = body.get("group_id", "")

    # State key is the group_id with the document_id prefix stripped
    # e.g. "<doc_id>_location_0" → "location_0" (matches group_suggestions key)
    state_key = group_id.removeprefix(f"{document_id}_") if group_id else body.get("category", "")

    states = dict(doc.suggestion_states or {})
    states[state_key] = "rejected"
    doc.suggestion_states = states
    db.commit()

    return {"ok": True}


@router.post("/document/{document_id}/suggestion-group/chat")
async def suggestion_group_chat(
    document_id: str,
    body: SuggestionChatRequest,
    request: Request,
):
    import os

    import litellm

    await require_user(request)

    field_schema = _SUGGESTION_FIELD_LABELS.get(body.category, [])
    fields_lines = "\n".join(
        f'- {f["label"]} ({f["key"]}): {body.fields.get(f["key"], "(empty)")}'
        for f in field_schema
    )

    system_prompt = (
        "You are a helpful assistant for a property management app. "
        "The user is reviewing a suggested change extracted from a lease document and wants to adjust it before applying.\n\n"
        f"Suggestion summary: {body.description}\n"
        f"Category: {body.category}\n\n"
        f"Current field values:\n{fields_lines}\n\n"
        "Help the user modify these values through conversation. "
        "When they request a change, update the relevant field(s).\n\n"
        "IMPORTANT: Always reply with a JSON object in exactly this format — no extra text:\n"
        '{"reply": "<your conversational response>", "fields": {"<key>": "<new_value>", ...}}\n\n'
        'Only include keys in "fields" that the user wants to change. '
        "Preserve all other values as-is. Be concise and confirm what you changed."
    )

    messages = [{"role": "system", "content": system_prompt}]
    for h in body.history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": body.message})

    try:
        resp = await litellm.acompletion(
            model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL") or None,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        parsed = extract_json(raw)
        reply = parsed.get("reply", raw)
        updated_fields = {**body.fields, **parsed.get("fields", {})}
    except Exception as e:
        print(f"Suggestion chat error: {e}")
        raise HTTPException(status_code=502, detail="AI unavailable")

    return {"reply": reply, "fields": updated_fields}
