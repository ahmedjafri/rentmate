"""Agent memory storage — DB-backed replacement for nanobot's file-based memory."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Index, String, Text

from .base import Base


class AgentMemory(Base):
    __tablename__ = "agent_memory"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(100), nullable=False)
    memory_type = Column(String(20), nullable=False)  # 'long_term' | 'history'
    content = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_agent_memory_agent_type", "agent_id", "memory_type"),
    )
