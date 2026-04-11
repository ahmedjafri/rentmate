"""Agent memory storage -- DB-backed replacement for nanobot's file-based memory."""
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index, String, Text, UniqueConstraint

from .base import Base, HasCreatorId, OrgId, PrimaryId


class AgentMemory(Base, OrgId, PrimaryId, HasCreatorId):
    __tablename__ = "agent_memory"

    memory_type = Column(String(20), nullable=False)  # 'long_term' | 'history'
    content = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_agent_memory_org"),
        Index("ix_agent_memory_agent_type", "creator_id", "memory_type"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )
