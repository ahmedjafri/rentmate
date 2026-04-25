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
    Text,
)
from sqlalchemy.orm import declared_attr, relationship

from db.enums import ChannelType, TaskCategory, TaskMode, TaskPriority, TaskSource, TaskStatus, Urgency

from .base import Base, HasCreatorId, NumberedPrimaryId, OrgId


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
    goal         = Column(String,      nullable=True)   # manager-authored intent — what "done" looks like
    context      = Column(String,      nullable=True)   # task-scoped agent notes (quotes, findings)
    last_message_at = Column(DateTime, nullable=True)
    channel_type = Column(SqlEnum(ChannelType, name="channel_type_enum"),   nullable=True)

    # Last time the task_review loop produced a decision. NULL means it has
    # never been reviewed and the loop should pick it up on the next sweep.
    last_reviewed_at      = Column(DateTime, nullable=True)
    last_review_status    = Column(String(32), nullable=True)
    last_review_summary   = Column(Text,       nullable=True)
    last_review_next_step = Column(Text,       nullable=True)
    last_seen_at          = Column(DateTime, nullable=True)

    property_id  = Column(String(36), nullable=True)
    unit_id      = Column(String(36), nullable=True)
    lease_id     = Column(String(36), nullable=True)

    # Direct FK to the task's AI conversation thread
    ai_conversation_id = Column(Integer, nullable=True)
    # FK to the originating tenant/vendor conversation (if task was spawned from one)
    parent_conversation_id = Column(Integer, nullable=True)

    resolved_at  = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at   = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True)

    # Relationships
    ai_conversation     = relationship("Conversation", foreign_keys=[ai_conversation_id])
    parent_conversation = relationship("Conversation", foreign_keys=[parent_conversation_id])
    # Reverse of Conversation.parent_task_id — any number of tenant/vendor coordination threads.
    external_conversations = relationship(
        "Conversation",
        primaryjoin=(
            "and_(foreign(Conversation.org_id) == Task.org_id,"
            " foreign(Conversation.parent_task_id) == Task.id)"
        ),
        uselist=True,
        viewonly=True,
        order_by="Conversation.updated_at.desc()",
    )

    @property
    def latest_external_conversation(self):
        """Most recently updated tenant/vendor coordination thread, or None."""
        return self.external_conversations[0] if self.external_conversations else None
    property       = relationship("Property", foreign_keys=[property_id])
    unit           = relationship("Unit", foreign_keys=[unit_id])
    lease          = relationship("Lease", foreign_keys=[lease_id])

    @declared_attr
    def __table_args__(cls):
        return (
            NumberedPrimaryId.primary_key(cls),
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
        )
