from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backends.local_auth import resolve_org_id
from db.models import Document as SqlDocument, DocumentTag as SqlDocumentTag
from gql.types import AddDocumentTagInput


class ExtractedLeaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_first_name: str | None = None
    tenant_last_name: str | None = None
    tenant_email: str | None = None
    tenant_phone: str | None = None
    property_address: str | None = None
    unit_label: str | None = None
    lease_start_date: str | None = None
    lease_end_date: str | None = None
    monthly_rent: float | None = None
    security_deposit: float | None = None
    context: str | None = None


class DocumentExtractionData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leases: list[ExtractedLeaseRecord]


class DocumentExtractionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    text_extractor: str | None = None
    llm_model: str | None = None
    page_count: int | None = None
    raw_text_chars: int | None = None
    form_fields_found: int | None = None
    form_fields_filled: int | None = None
    input_chars_sent_to_llm: int | None = None
    leases_found: int | None = None


def parse_document_extraction_meta(meta: dict | None) -> DocumentExtractionMeta:
    return DocumentExtractionMeta.model_validate(meta or {})


def dump_document_extraction_meta(meta: DocumentExtractionMeta | dict | None = None, **updates) -> dict | None:
    parsed = meta if isinstance(meta, DocumentExtractionMeta) else parse_document_extraction_meta(meta)
    for key, value in updates.items():
        setattr(parsed, key, value)
    dumped = parsed.model_dump(exclude_none=True)
    return dumped or None


def dump_document_extraction_data(data: DocumentExtractionData | dict | None) -> dict | None:
    if data is None:
        return None
    parsed = data if isinstance(data, DocumentExtractionData) else DocumentExtractionData.model_validate(data)
    return parsed.model_dump(exclude_none=True)


class DocumentService:
    @staticmethod
    def add_document_tag(sess: Session, input: AddDocumentTagInput) -> SqlDocumentTag:
        tag = SqlDocumentTag(
            org_id=resolve_org_id(),
            document_id=input.document_id,
            tag_type=input.tag_type,
            property_id=input.property_id,
            unit_id=input.unit_id,
            tenant_id=input.tenant_id,
            created_at=datetime.now(UTC),
        )
        sess.add(tag)
        sess.commit()
        sess.refresh(tag)
        return tag

    @staticmethod
    def confirm_document(sess: Session, document_id: str) -> bool:
        doc = sess.execute(
            select(SqlDocument).where(
                SqlDocument.id == document_id,
                SqlDocument.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not doc:
            raise ValueError(f"Document {document_id} not found")
        doc.confirmed_at = datetime.now(UTC)
        sess.commit()
        return True
