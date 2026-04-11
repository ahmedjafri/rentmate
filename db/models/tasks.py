from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum as SqlEnum,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from db.enums import ChannelType, TaskCategory, TaskMode, TaskPriority, TaskSource, TaskStatus, Urgency

from .base import Base, HasCreatorId, NumberedPrimaryId, OrgId


class TaskNumberSequence(Base):
    """
    Monotonically increasing task-number counter, one row per server.
    Only ever incremented -- never decremented or reset -- so that task
    numbers are never reused even after tasks are deleted.
    """
    __tablename__ = "task_number_sequences"

    org_id = Column(Integer, primary_key=True)
    last_number = Column(Integer, nullable=False, default=0)


class Task(Base, OrgId, NumberedPrimaryId, HasCreatorId):
    """
    A first-class work-item (task / action-desk item).
    Owns task metadata; links directly to its AI conversation thread.
    The auto-incrementing id (from NumberedPrimaryId) doubles as the task number.
    """
    __tablename__ = "tasks"

    title        = Column(String(500), nullable=True)
    task_status  = Column(SqlEnum(TaskStatus, name="task_status_enum"),     nullable=True)
    task_mode    = Column(SqlEnum(TaskMode, name="task_mode_enum"),         nullable=True)
    source       = Column(SqlEnum(TaskSource, name="task_source_enum"),     nullable=True)
    category     = Column(SqlEnum(TaskCategory, name="task_category_enum"), nullable=True)
    urgency      = Column(SqlEnum(Urgency, name="urgency_enum"),           nullable=True)
    priority     = Column(SqlEnum(TaskPriority, name="task_priority_enum"), nullable=True)
    confidential = Column(Boolean,     nullable=False, default=False)
    steps        = Column(JSON,        nullable=True)   # ordered list of progress steps
    context      = Column(String,      nullable=True)   # task-scoped agent notes (quotes, findings)
    last_message_at = Column(DateTime, nullable=True)
    channel_type = Column(SqlEnum(ChannelType, name="channel_type_enum"),   nullable=True)

    property_id  = Column(String(36), nullable=True)
    unit_id      = Column(String(36), nullable=True)
    lease_id     = Column(String(36), nullable=True)

    # Direct FK to the task's AI conversation thread
    ai_conversation_id = Column(Integer, nullable=True)
    # FK to the originating tenant/vendor conversation (if task was spawned from one)
    parent_conversation_id = Column(Integer, nullable=True)
    # FK to the active vendor/tenant chat thread for this task (never deleted with task)
    external_conversation_id = Column(Integer, nullable=True)

    resolved_at  = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at   = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True)

    # Relationships
    ai_conversation       = relationship("Conversation", foreign_keys=[ai_conversation_id])
    parent_conversation   = relationship("Conversation", foreign_keys=[parent_conversation_id])
    external_conversation = relationship("Conversation", foreign_keys=[external_conversation_id])
    property       = relationship("Property", foreign_keys=[property_id])
    unit           = relationship("Unit", foreign_keys=[unit_id])
    lease          = relationship("Lease", foreign_keys=[lease_id])

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_tasks_server"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "property_id"],
            ["properties.org_id", "properties.id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "unit_id"],
            ["units.org_id", "units.id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "lease_id"],
            ["leases.org_id", "leases.id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "ai_conversation_id"],
            ["conversations.org_id", "conversations.id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "parent_conversation_id"],
            ["conversations.org_id", "conversations.id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "external_conversation_id"],
            ["conversations.org_id", "conversations.id"],
            ondelete="SET NULL",
        ),
    )
