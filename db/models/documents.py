import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import relationship

from .base import Base


class Document(Base):
    """
    An uploaded document (e.g. lease PDF).
    """
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=True)
    storage_path = Column(String(512), nullable=True)
    document_type = Column(String(50), nullable=False, default="lease")
    status = Column(String(50), nullable=False, default="pending")
    progress = Column(String(255), nullable=True)

    raw_text = Column(Text, nullable=True)
    extracted_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    sha256_checksum = Column(String(64), nullable=True, index=True)
    suggestion_states = Column(JSON, nullable=True)  # {category: 'accepted'|'rejected'}
    confirmed_at = Column(DateTime, nullable=True)
    extraction_meta = Column(JSON, nullable=True)   # {llm_model, text_extractor, page_count, raw_text_chars, form_fields_found, form_fields_filled, input_chars_sent_to_llm}


class DocumentTask(Base):
    """
    Many-to-many junction: one document can spawn multiple tasks;
    one task can reference multiple documents.
    """
    __tablename__ = "document_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    task = relationship("Task", back_populates="document_tasks")

    __table_args__ = (UniqueConstraint("document_id", "task_id", name="uq_document_task"),)


class DocumentTag(Base):
    """
    Links a document to a property, unit, or tenant for the Documents UI tagging feature.
    """
    __tablename__ = "document_tags"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    tag_type = Column(String(20), nullable=False)  # property/unit/tenant
    property_id = Column(String(36), ForeignKey("properties.id", ondelete="CASCADE"), nullable=True)
    unit_id = Column(String(36), ForeignKey("units.id", ondelete="CASCADE"), nullable=True)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
