# llm/document_processor.py

import os
import uuid
from datetime import datetime
from functools import lru_cache
from io import BytesIO

import litellm
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from sqlalchemy.orm import Session

from db.models import Document

LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or None


def _get_session_factory():
    """Import lazily to avoid circular imports at module load time."""
    from main import SessionLocal
    return SessionLocal


def _set_progress(db: Session, doc: Document, progress: str) -> None:
    doc.progress = progress
    db.commit()


EXTRACTION_PROMPT = """You are a document parser for a property management app. Extract ALL rental properties and lease records from the document.
Respond ONLY with a valid JSON object — no markdown fences, no extra text.

{
  "leases": [
    {
      "tenant_first_name": string | null,
      "tenant_last_name": string | null,
      "tenant_email": string | null,
      "tenant_phone": string | null,
      "property_address": string | null,
      "unit_label": string | null,
      "lease_start_date": "YYYY-MM-DD" | null,
      "lease_end_date": "YYYY-MM-DD" | null,
      "monthly_rent": number | null,
      "property_type": "single_family" | "multi_family" | null
    }
  ]
}

Rules:
- Include one object per property/tenant combination found.
- For insurance docs, portfolios, or listings with no lease terms, include one entry per property address with null tenant/lease fields.
- Use "single_family" for single-unit homes/condos, "multi_family" for apartments or multi-unit buildings.
- Normalize addresses to "NUMBER STREET CITY STATE ZIP" format.

Document text:
"""


async def process_document(document_id: str) -> None:
    """
    Background task: extract text, embed chunks, and run LLM extraction on a Document.
    """
    import json
    from backends.wire import storage_backend, vector_backend

    SessionLocal = _get_session_factory()
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).one_or_none()
        if not doc:
            return

        doc.status = "processing"
        _set_progress(db, doc, "Downloading file…")

        # 1. Download file bytes from storage backend
        file_bytes: bytes = await storage_backend.download(doc.storage_path)

        # 2. Extract text with pypdf (page text + form fields)
        _set_progress(db, doc, "Extracting text from PDF…")
        reader = PdfReader(BytesIO(file_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        raw_text = "\n".join(pages_text).strip()
        n_pages = len(reader.pages)

        # Also extract AcroForm field values — PDFs filled with form fields
        # store data here rather than in the text layer.
        # Prepend form fields so they appear within the LLM truncation window.
        _form_fields_found = 0
        _form_fields_filled = 0
        try:
            fields = reader.get_fields() or {}
            _form_fields_found = len(fields)
            form_lines = []
            for name, field in fields.items():
                val = field.get("/V")
                if val and isinstance(val, str) and not val.startswith("/"):
                    form_lines.append(f"{name}: {val}")
            _form_fields_filled = len(form_lines)
            if form_lines:
                form_section = "[Form Fields]\n" + "\n".join(form_lines)
                raw_text = form_section + "\n\n" + raw_text
        except Exception:
            pass

        doc.raw_text = raw_text
        doc.extraction_meta = {
            "text_extractor": "pypdf",
            "llm_model": LLM_MODEL,
            "page_count": n_pages,
            "raw_text_chars": len(raw_text),
            "form_fields_found": _form_fields_found,
            "form_fields_filled": _form_fields_filled,
        }

        # 3. Chunk the text
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        chunks = splitter.split_text(raw_text) if raw_text else []

        # 4. Store chunks in vector backend
        if chunks:
            _set_progress(db, doc, f"Embedding {len(chunks)} chunks from {n_pages} page(s)…")
            metadatas = [{"doc_id": document_id, "chunk_index": i} for i in range(len(chunks))]
            vector_backend.add_document(document_id, chunks, metadatas)

        # 5. LLM extraction pass
        extracted_data = None
        if raw_text or file_bytes:
            _set_progress(db, doc, "Extracting lease details with AI…")
            truncated = raw_text[:12000]
            if doc.extraction_meta:
                doc.extraction_meta = {**doc.extraction_meta, "input_chars_sent_to_llm": len(truncated)}
            user_content = EXTRACTION_PROMPT + truncated
            response = litellm.completion(
                model=LLM_MODEL,
                api_key=LLM_API_KEY,
                base_url=LLM_BASE_URL,
                messages=[
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
            )
            try:
                extracted_data = json.loads(response.choices[0].message.content)
            except (json.JSONDecodeError, AttributeError):
                extracted_data = {"leases": []}

        leases = extracted_data.get("leases") if isinstance(extracted_data, dict) else []
        leases_found = len(leases) if isinstance(leases, list) else 0
        if doc.extraction_meta:
            doc.extraction_meta = {**doc.extraction_meta, "leases_found": leases_found}
        doc.extracted_data = extracted_data
        doc.status = "done"
        doc.progress = None
        doc.processed_at = datetime.utcnow()
        db.commit()

    except Exception as exc:
        db.rollback()
        try:
            doc = db.query(Document).filter(Document.id == document_id).one_or_none()
            if doc:
                doc.status = "error"
                doc.progress = None
                doc.error_message = str(exc)
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()
