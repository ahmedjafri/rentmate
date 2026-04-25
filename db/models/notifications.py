from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint

from .base import Base, HasCreatorId, OrgId, SmallPrimaryId


class Notification(Base, OrgId, SmallPrimaryId, HasCreatorId):
    """Persisted notification event for a PM, tenant, or vendor."""

    __tablename__ = "notifications"

    recipient_user_id = Column(Integer, nullable=False, index=True)
    task_id = Column(Integer, nullable=True, index=True)
    conversation_id = Column(Integer, nullable=True, index=True)

    kind = Column(String(64), nullable=False, default="generic")
    channel = Column(String(32), nullable=False, default="in_app")
    delivery_status = Column(String(32), nullable=False, default="pending")

    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    extra = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True)
    read_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    failure_reason = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_notifications_org"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "recipient_user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "conversation_id"],
            ["conversations.org_id", "conversations.id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "task_id"],
            ["tasks.org_id", "tasks.id"],
            ondelete="SET NULL",
        ),
    )
