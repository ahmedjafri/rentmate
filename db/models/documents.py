from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from .base import Base, HasContext, HasCreatorId, OrgId, PrimaryId


class Document(Base, OrgId, PrimaryId, HasCreatorId, HasContext):
    """An uploaded document (e.g. lease PDF)."""
    __tablename__ = "documents"

    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=True)
    storage_path = Column(String(512), nullable=True)
    document_type = Column(String(50), nullable=False, default="lease")
    status = Column(String(50), nullable=False, default="pending")
    progress = Column(String(255), nullable=True)

    raw_text = Column(Text, nullable=True)
    extracted_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    processed_at = Column(DateTime, nullable=True)
    sha256_checksum = Column(String(64), nullable=True, index=True)
    confirmed_at = Column(DateTime, nullable=True)
    extraction_meta = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_documents_server"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )


class DocumentTag(Base, OrgId, PrimaryId):
    """Links a document to a property, unit, or tenant for the Documents UI tagging feature."""
    __tablename__ = "document_tags"

    document_id = Column(String(36), nullable=False, index=True)
    tag_type = Column(String(20), nullable=False)  # property/unit/tenant
    property_id = Column(String(36), nullable=True)
    unit_id = Column(String(36), nullable=True)
    tenant_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_document_tags_server"),
        ForeignKeyConstraint(
            ["org_id", "document_id"],
            ["documents.org_id", "documents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "property_id"],
            ["properties.org_id", "properties.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "unit_id"],
            ["units.org_id", "units.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["tenants.org_id", "tenants.id"],
            ondelete="CASCADE",
        ),
    )
