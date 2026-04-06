"""Agent workspace and file storage models.

Part of the nanobot-storage convergence (issue #2, phase 1).  These tables
will eventually hold every file that currently lives on disk inside an
agent's workspace directory.  Phase 1 only creates the schema — data
migration happens in a later PR.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from .base import Base


class AgentWorkspace(Base):
    """One workspace per account — the DB equivalent of ``data/agent/<account_id>/``."""

    __tablename__ = "agent_workspaces"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String(36), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC),
                        onupdate=lambda: datetime.now(UTC))

    files = relationship("AgentFile", back_populates="workspace",
                         cascade="all, delete-orphan")


class AgentFile(Base):
    """A single file stored inside an agent workspace.

    ``path`` is the slash-separated relative path (e.g. ``SOUL.md``).
    ``content`` holds the full text.  ``size_bytes`` is denormalised for
    quick listing without loading the content column.
    """

    __tablename__ = "agent_files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(String(36), ForeignKey("agent_workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    path = Column(String(512), nullable=False)
    content = Column(Text, nullable=False, default="")
    size_bytes = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC),
                        onupdate=lambda: datetime.now(UTC))

    workspace = relationship("AgentWorkspace", back_populates="files")

    __table_args__ = (
        Index("ix_agent_files_workspace_path", "workspace_id", "path", unique=True),
    )
