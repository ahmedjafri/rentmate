from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKeyConstraint, Index, String, Text, UniqueConstraint

from .base import Base, HasCreatorId, OrgId, PrimaryId


class MemoryItem(Base, OrgId, PrimaryId, HasCreatorId):
    """Normalized retrieval record used by the domain memory manager."""

    __tablename__ = "memory_items"

    source_type = Column(String(50), nullable=False)
    source_id = Column(String(64), nullable=False)
    entity_type = Column(String(30), nullable=False)
    entity_id = Column(String(64), nullable=False)
    visibility = Column(String(20), nullable=False, default="shared")
    title = Column(String(255), nullable=True)
    content = Column(Text, nullable=False, default="")
    content_hash = Column(String(64), nullable=False)
    metadata_json = Column(JSON, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True)

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_memory_items_org"),
        UniqueConstraint("org_id", "creator_id", "source_type", "source_id", name="uq_memory_item_source"),
        Index("ix_memory_items_lookup", "org_id", "creator_id", "entity_type", "entity_id"),
        Index("ix_memory_items_visibility", "org_id", "creator_id", "visibility"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )
