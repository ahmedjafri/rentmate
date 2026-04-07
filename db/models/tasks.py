import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


class TaskNumberSequence(Base):
    """
    Monotonically increasing task-number counter, one row per account.
    Only ever incremented — never decremented or reset — so that task
    numbers are never reused even after tasks are deleted.
    """
    __tablename__ = "task_number_sequences"

    account_id  = Column(String(36), primary_key=True)
    last_number = Column(Integer, nullable=False, default=0)


class Task(Base):
    """
    A first-class work-item (task / action-desk item).
    Owns task metadata; links directly to its AI conversation thread.
    """
    __tablename__ = "tasks"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_number  = Column(Integer, nullable=True, index=True)   # assigned at insert, per-account
    account_id   = Column(String(36), nullable=False)           # required (NOT NULL)

    title        = Column(String(500), nullable=True)
    task_status  = Column(String(20),  nullable=True)
    task_mode    = Column(String(25),  nullable=True)
    source       = Column(String(25),  nullable=True)
    category     = Column(String(20),  nullable=True)
    urgency      = Column(String(20),  nullable=True)
    priority     = Column(String(20),  nullable=True)
    confidential = Column(Boolean,     nullable=False, default=False)
    steps        = Column(JSON,        nullable=True)   # ordered list of progress steps
    context      = Column(String,      nullable=True)   # task-scoped agent notes (quotes, findings)
    last_message_at = Column(DateTime, nullable=True)
    channel_type = Column(String(20),  nullable=True)

    property_id  = Column(String(36), ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    unit_id      = Column(String(36), ForeignKey("units.id",       ondelete="SET NULL"), nullable=True)
    lease_id     = Column(String(36), ForeignKey("leases.id",      ondelete="SET NULL"), nullable=True)

    # Direct FK to the task's AI conversation thread
    ai_conversation_id = Column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    # FK to the originating tenant/vendor conversation (if task was spawned from one)
    parent_conversation_id = Column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    # FK to the active vendor/tenant chat thread for this task (never deleted with task)
    external_conversation_id = Column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )

    resolved_at  = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at   = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Relationships
    ai_conversation       = relationship("Conversation", foreign_keys=[ai_conversation_id])
    parent_conversation   = relationship("Conversation", foreign_keys=[parent_conversation_id])
    external_conversation = relationship("Conversation", foreign_keys=[external_conversation_id])
    property       = relationship("Property")
    unit           = relationship("Unit")
    lease          = relationship("Lease")
    document_tasks = relationship("DocumentTask", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("account_id", "task_number", name="uq_task_number_per_account"),
    )
