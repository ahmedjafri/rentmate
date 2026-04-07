from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Document as SqlDocument, DocumentTag as SqlDocumentTag
from gql.types import AddDocumentTagInput


class DocumentService:
    @staticmethod
    def add_document_tag(sess: Session, input: AddDocumentTagInput) -> SqlDocumentTag:
        tag = SqlDocumentTag(
            document_id=input.document_id,
            tag_type=input.tag_type,
            property_id=input.property_id,
            unit_id=input.unit_id,
            tenant_id=input.tenant_id,
        )
        sess.add(tag)
        sess.commit()
        sess.refresh(tag)
        return tag

    @staticmethod
    def confirm_document(sess: Session, document_id: str) -> bool:
        doc = sess.execute(select(SqlDocument).where(SqlDocument.id == document_id)).scalar_one_or_none()
        if not doc:
            raise ValueError(f"Document {document_id} not found")
        doc.confirmed_at = datetime.now(UTC)
        sess.commit()
        return True
