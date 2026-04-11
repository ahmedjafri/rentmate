import hashlib
import uuid as _uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from backends.wire import storage_backend
from db.models import Document
from gql.services.document_service import dump_document_extraction_meta
from handlers.deps import get_db, require_user

router = APIRouter()


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

    existing = db.query(Document).filter(
        Document.sha256_checksum == checksum,
        Document.org_id == resolve_org_id(),
        Document.creator_id == resolve_account_id(),
    ).one_or_none()
    if existing:
        return {"document_id": existing.id, "duplicate": True}

    doc_id = str(_uuid.uuid4())
    storage_path = f"documents/{doc_id}/{file.filename}"

    await storage_backend.upload(storage_path, data=file_bytes, content_type=file.content_type or "application/octet-stream")

    doc = Document(
        id=doc_id,
        org_id=resolve_org_id(),
        creator_id=resolve_account_id(),
        filename=file.filename,
        content_type=file.content_type,
        storage_path=storage_path,
        document_type=document_type,
        status="pending",
        sha256_checksum=checksum,
        extraction_meta=dump_document_extraction_meta(task_id=task_id) if task_id else None,
    )
    db.add(doc)
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
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.org_id == resolve_org_id(),
        Document.creator_id == resolve_account_id(),
    ).one_or_none()
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
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.org_id == resolve_org_id(),
        Document.creator_id == resolve_account_id(),
    ).one_or_none()
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


@router.delete("/document/{document_id}")
async def delete_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.org_id == resolve_org_id(),
        Document.creator_id == resolve_account_id(),
    ).one_or_none()
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
    tags = db.query(DocumentTag).filter(
        DocumentTag.document_id == document_id,
        DocumentTag.org_id == resolve_org_id(),
    ).all()
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
            tenant = db.query(SqlTenant).filter_by(id=tag.tenant_id, org_id=resolve_org_id()).first()
            if tenant:
                user = tenant.user
                entry["label"] = f"{user.first_name or ''} {user.last_name or ''}".strip()
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
    q = db.query(DocumentTag).filter_by(document_id=document_id, org_id=resolve_org_id())
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
        org_id=resolve_org_id(),
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
    tag = db.query(DocumentTag).filter_by(id=tag_id, org_id=resolve_org_id()).first()
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
    tags = db.query(DocumentTag).filter(
        DocumentTag.property_id == property_id,
        DocumentTag.org_id == resolve_org_id(),
    ).all()
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



# Document suggestion endpoints (dashboard/suggestions, confirm-all, suggestion-group/*)
# removed — suggestions are now created by the agent via the create_suggestion tool.
