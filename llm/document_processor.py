# llm/document_processor.py

import os
from datetime import UTC, datetime
from io import BytesIO

import litellm
from pypdf import PdfReader
from sqlalchemy.orm import Session

from db.models import Document


def _split_text(text: str, *, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks, breaking at paragraph/sentence boundaries."""
    if not text:
        return []
    separators = ["\n\n", "\n", ". ", " "]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:].strip())
            break
        # Try to break at a natural boundary
        best_break = end
        for sep in separators:
            idx = text.rfind(sep, start, end)
            if idx > start:
                best_break = idx + len(sep)
                break
        chunks.append(text[start:best_break].strip())
        # Ensure forward progress: start must advance by at least 1 character
        start = max(start + 1, best_break - overlap)
    return [c for c in chunks if c]


def _get_session_factory():
    """Import lazily to avoid circular imports at module load time."""
    from main import SessionLocal
    return SessionLocal


def _set_progress(db: Session, doc: Document, progress: str) -> None:
    doc.progress = progress
    db.commit()


EXTRACTION_PROMPT = """You are a document parser for a property management app. Extract ALL rental properties and lease records from the document, including detailed context that a property manager would need.
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
      "property_type": "single_family" | "multi_family" | null,
      "property_context": string | null,
      "unit_context": string | null,
      "tenant_context": string | null,
      "lease_context": string | null
    }
  ]
}

The context fields capture EVERYTHING a property manager needs beyond the structured fields:

- **property_context**: Building details — furnishings provided (appliances, fixtures), safety systems (smoke detectors, sprinklers, fire alarms, CO alarms), yard/grounds responsibilities, landlord contact info, payment address/method, any building-specific rules.

- **unit_context**: Unit-specific details — parking assignment, included utilities (and who pays what), special notes about the unit (e.g. "2 of 3 floors"), keys provided (door, laundry, garage, mailbox), move-in condition notes, furnishings specific to unit.

- **tenant_context**: Tenant-specific rules and restrictions — pet/animal policy, smoking policy, vehicle limits, occupancy limits (max persons, guest policy), subletting rules, insurance requirements, any co-signer info.

- **lease_context**: Financial and legal terms — security deposit amount and breakdown (refundable vs nonrefundable portions), last month's rent paid, pro-rata rent details, late fee structure (amount, grace period, daily penalty), NSF fee, early termination fee, rent increase terms, notice period to vacate, security deposit refund schedule/conditions, payment methods accepted.

Rules:
- Include one object per property/tenant combination found.
- For insurance docs, portfolios, or listings with no lease terms, include one entry per property address with null tenant/lease fields.
- Use "single_family" for single-unit homes/condos or when unit is "N/A"/"Main", "multi_family" for apartments or multi-unit buildings.
- Normalize addresses to "NUMBER STREET CITY STATE ZIP" format.
- Context fields should be concise bullet points, not full legal text. Capture the key facts a manager needs.
- Include specific dollar amounts, dates, percentages, and quantities — not vague descriptions.

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
            "llm_model": os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            "page_count": n_pages,
            "raw_text_chars": len(raw_text),
            "form_fields_found": _form_fields_found,
            "form_fields_filled": _form_fields_filled,
        }

        # 3. Chunk the text
        chunks = _split_text(raw_text, chunk_size=800, overlap=100) if raw_text else []

        # 4. Store chunks in vector backend
        if chunks:
            _set_progress(db, doc, f"Embedding {len(chunks)} chunks from {n_pages} page(s)…")
            metadatas = [{"doc_id": document_id, "chunk_index": i} for i in range(len(chunks))]
            vector_backend.add_document(document_id, chunks=chunks, metadatas=metadatas)

        # 5. LLM extraction pass
        extracted_data = None
        if raw_text or file_bytes:
            _set_progress(db, doc, "Extracting lease details with AI…")
            truncated = raw_text[:12000]
            if doc.extraction_meta:
                doc.extraction_meta = {**doc.extraction_meta, "input_chars_sent_to_llm": len(truncated)}
            user_content = EXTRACTION_PROMPT + truncated
            response = litellm.completion(
                model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
                api_key=os.getenv("LLM_API_KEY"),
                base_url=os.getenv("LLM_BASE_URL") or None,
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
        doc.processed_at = datetime.now(UTC)
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
