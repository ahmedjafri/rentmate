"""Data export/import for RentMate portability.

Export: GET /api/export → ZIP with manifest.json, data.json, documents/
Import: POST /api/import → accepts ZIP, inserts into clean instance
"""
import io
import json
import os
import zipfile
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id
from backends.wire import storage_backend
from db.models import (
    AgentMemory,
    AgentTrace,
    AppSetting,
    Conversation,
    ConversationParticipant,
    Document,
    DocumentTag,
    EntityNote,
    Lease,
    Message,
    MessageReceipt,
    Property,
    Routine,
    IdSequence,
    Suggestion,
    Task,
    Tenant,
    Unit,
)
from handlers.deps import get_db, require_user

router = APIRouter()

EXPORT_VERSION = 1

# (table_name, Model, remap_creator_id, self_ref_fk_column)
TABLE_ORDER = [
    ("app_settings", AppSetting, False, None),
    ("properties", Property, True, None),
    ("tenants", Tenant, True, None),
    ("units", Unit, True, None),
    ("leases", Lease, True, None),
    ("conversations", Conversation, True, "parent_conversation_id"),
    ("documents", Document, True, None),
    ("tasks", Task, True, None),
    ("id_sequences", IdSequence, False, None),
    ("document_tags", DocumentTag, False, None),
    ("suggestions", Suggestion, True, None),
    ("conversation_participants", ConversationParticipant, False, None),
    ("messages", Message, False, None),
    ("message_receipts", MessageReceipt, False, None),
    ("agent_memory", AgentMemory, True, None),
    ("agent_traces", AgentTrace, True, None),
    ("routines", Routine, True, None),
    ("entity_notes", EntityNote, False, None),
]

# entity_notes has creator_id but not via mixin — handle explicitly
_EXTRA_CREATOR_ID_TABLES = {"entity_notes", "id_sequences"}


# ─── Serialization ───────────────────────────────────────────────────────────

def _serialize_value(val):
    """Convert a Python value to a JSON-safe representation."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if hasattr(val, "value"):  # enum
        return val.value
    return val


def _serialize_row(row, *, model):
    """Convert a SQLAlchemy row to a JSON-safe dict."""
    return {c.name: _serialize_value(getattr(row, c.name)) for c in model.__table__.columns}


def _deserialize_value(col, value):
    """Convert a JSON value back to the appropriate Python type for a column."""
    if value is None:
        return None
    col_type = str(col.type)
    if "DATETIME" in col_type or "TIMESTAMP" in col_type:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value
    if "DATE" in col_type and "DATETIME" not in col_type:
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value
    return value


# ─── Export ──────────────────────────────────────────────────────────────────

def export_data(db: Session, *, include_files: bool = True) -> tuple[dict, dict, list]:
    """Export all data. Returns (manifest, data_dict, document_files).

    document_files is a list of (zip_path, storage_path) tuples.
    """
    manifest = {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
    }
    data = {}
    doc_files = []

    for table_name, model, _remap, _self_ref in TABLE_ORDER:
        rows = db.query(model).all()
        data[table_name] = [_serialize_row(r, model=model) for r in rows]

    if include_files:
        for doc_row in data.get("documents", []):
            spath = doc_row.get("storage_path")
            if spath:
                doc_files.append((f"documents/{spath}", spath))

    return manifest, data, doc_files


@router.get("/export")
async def export_endpoint(request: Request, db: Session = Depends(get_db)):
    """Export all data as a ZIP file."""
    await require_user(request)

    if os.getenv("RENTMATE_HOSTED") == "true":
        raise HTTPException(status_code=403, detail="Export is not available on hosted instances. Contact us to export your data.")

    include_files = request.query_params.get("include_files", "true").lower() != "false"
    manifest, data, doc_files = export_data(db, include_files=include_files)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("data.json", json.dumps(data, indent=2, default=str))
        for zip_path, storage_path in doc_files:
            try:
                file_data = await storage_backend.download(storage_path)
                zf.writestr(zip_path, file_data)
            except Exception:
                pass  # skip missing files

    buf.seek(0)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="rentmate-export-{today}.zip"'},
    )


# ─── Import ──────────────────────────────────────────────────────────────────

def import_data(
    db: Session,
    *,
    data: dict,
    files: dict[str, bytes] | None = None,
    target_creator_id: int,
) -> dict:
    """Import data into a clean instance. Returns summary {table: count}."""
    # Check instance is clean
    for table_name, model, _remap, _self_ref in TABLE_ORDER:
        if table_name == "app_settings":
            continue
        count = db.query(model).count()
        if count > 0:
            raise ValueError(f"Target instance is not empty: {table_name} has {count} rows. Import only works on fresh instances.")

    summary = {}

    for table_name, model, remap_creator, self_ref_col in TABLE_ORDER:
        rows = data.get(table_name, [])
        if not rows:
            summary[table_name] = 0
            continue

        columns = {c.name: c for c in model.__table__.columns}

        # Deserialize and remap
        processed = []
        deferred_updates = []  # for self-referential FKs

        for row in rows:
            record = {}
            for col_name, value in row.items():
                if col_name not in columns:
                    continue
                record[col_name] = _deserialize_value(columns[col_name], value)

            # Remap creator_id
            if remap_creator and "creator_id" in record:
                record["creator_id"] = target_creator_id
            if table_name in _EXTRA_CREATOR_ID_TABLES and "creator_id" in record:
                record["creator_id"] = target_creator_id

            # Handle self-referential FK (2-pass)
            if self_ref_col and record.get(self_ref_col) is not None:
                deferred_updates.append((record["id"], record[self_ref_col]))
                record[self_ref_col] = None

            processed.append(record)

        # Bulk insert
        if processed:
            db.execute(model.__table__.insert(), processed)
            db.flush()

        # Second pass: update self-referential FKs
        if deferred_updates:
            for row_id, ref_value in deferred_updates:
                db.execute(
                    model.__table__.update()
                    .where(model.__table__.c.id == row_id)
                    .values({self_ref_col: ref_value})
                )
            db.flush()

        summary[table_name] = len(processed)

    db.commit()
    return summary


@router.post("/import")
async def import_endpoint(
    request: Request,
    file: UploadFile,
    db: Session = Depends(get_db),
):
    """Import data from a ZIP or JSON file."""
    await require_user(request)
    target_creator_id = resolve_account_id()

    content = await file.read()
    files_map: dict[str, bytes] = {}

    try:
        # Try as ZIP first
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            data_json = zf.read("data.json")
            data = json.loads(data_json)
            # Extract document files
            for name in zf.namelist():
                if name.startswith("documents/") and not name.endswith("/"):
                    files_map[name] = zf.read(name)
    except zipfile.BadZipFile:
        # Try as bare JSON
        data = json.loads(content)

    try:
        summary = import_data(db, data=data, files=files_map, target_creator_id=target_creator_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Upload document files
    uploaded_docs = 0
    for zip_path, file_data in files_map.items():
        storage_path = zip_path.removeprefix("documents/")
        try:
            await storage_backend.upload(storage_path, data=file_data, content_type="application/octet-stream")
            uploaded_docs += 1
        except Exception:
            pass

    return {"ok": True, "summary": summary, "documents_uploaded": uploaded_docs}
