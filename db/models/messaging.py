import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum as SqlEnum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .base import Base, HasCreatorId, OrgId, SmallPrimaryId


class ParticipantType(str, Enum):
    TENANT = "tenant"
    ACCOUNT_USER = "account_user"
    EXTERNAL_CONTACT = "external_contact"


class ConversationType(str, Enum):
    TENANT        = "tenant"
    VENDOR        = "vendor"
    USER_AI       = "user_ai"
    TASK_AI       = "task_ai"
    SUGGESTION_AI = "suggestion_ai"
    # Read-only snapshot of a thread mirrored from an external chat
    # platform. Replies happen back on the source platform — rentmate's
    # send-message paths refuse to write into these conversations.
    MIRRORED_CHAT = "mirrored_chat"


class MessageType(int, Enum):
    MESSAGE         = 1
    INTERNAL        = 2
    APPROVAL        = 3      # legacy -- use SUGGESTION for new code
    SUGGESTION      = 4      # links to a Suggestion via related_task_ids.suggestion_id
    CONTEXT         = 5
    THREAD          = 6      # deprecated -- use MESSAGE
    DRAFT_AI_REPLY  = 7      # AI-generated draft; content lives in body/body_html
    ACTION          = 8      # agent-created entity/action card rendered in chat


class DraftApprovalStatus(int, Enum):
    PENDING  = 1
    APPROVED = 2
    REJECTED = 3
    EDITED   = 4


class Conversation(Base, OrgId, SmallPrimaryId, HasCreatorId):
    """
    A message thread (1:1 or group).
    Optionally tied to a task and/or a property/unit/lease for context.
    """
    __tablename__ = "conversations"

    property_id = Column(String(36), nullable=True, index=True)
    unit_id = Column(String(36), nullable=True, index=True)
    lease_id = Column(String(36), nullable=True, index=True)

    subject = Column(String(255), nullable=True)
    is_group = Column(Boolean, nullable=False, default=False)
    is_archived = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    # Conversation type taxonomy
    conversation_type = Column(String(20), nullable=True, default="task_ai")
    parent_conversation_id = Column(Integer, nullable=True)
    # Optional link to the task that owns this coordination thread. A task can
    # own many conversations (e.g. tenant + multiple vendors).
    parent_task_id = Column(Integer, nullable=True, index=True)
    ai_initiated = Column(Boolean, nullable=False, default=False)

    # Flexible metadata (vendor requirements, assignments, etc.)
    extra = Column(JSON, nullable=True)

    property = relationship("Property", foreign_keys=[property_id])
    unit = relationship("Unit", foreign_keys=[unit_id])
    lease = relationship("Lease", foreign_keys=[lease_id])
    parent = relationship(
        "Conversation",
        foreign_keys=[parent_conversation_id],
        remote_side="Conversation.id",
    )
    # The owning task, if any. Composite-key match mirrors the FK on this row.
    parent_task = relationship(
        "Task",
        primaryjoin=(
            "and_(foreign(Conversation.org_id) == Task.org_id,"
            " foreign(Conversation.parent_task_id) == Task.id)"
        ),
        uselist=False,
        viewonly=True,
    )

    participants = relationship(
        "ConversationParticipant",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sent_at",
    )

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_conversations_server"),
        Index("ix_conversations_updated", "updated_at"),
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
        # Self-referential FK uses single-column (id is unique as PK)
        ForeignKeyConstraint(
            ["parent_conversation_id"],
            ["conversations.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "parent_task_id"],
            ["tasks.org_id", "tasks.id"],
            ondelete="SET NULL",
        ),
    )


class ConversationParticipant(Base, OrgId, SmallPrimaryId, HasCreatorId):
    """
    Links a conversation to a participant (tenant, account user, or vendor).
    Every participant has a User identity (real or shadow).
    """
    __tablename__ = "conversation_participants"

    conversation_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    participant_type = Column(SqlEnum(ParticipantType, name="participant_type"), nullable=False)

    delivery_prefs = Column(JSON, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    joined_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    left_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="participants")
    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_conversation_participants_server"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "conversation_id"],
            ["conversations.org_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
        ),
    )


class Message(Base, OrgId):
    """A message within a conversation."""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False, index=True)

    sender_type = Column(SqlEnum(ParticipantType, name="message_sender_type"), nullable=False)
    sender_id = Column(Integer, nullable=True, index=True)

    body = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    attachments = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)

    # AI chat enhancement fields
    message_type = Column(SqlEnum(MessageType, name="message_type_enum"), nullable=True)
    sender_name = Column(String(255), nullable=True)
    is_ai = Column(Boolean, nullable=False, default=False)
    draft_approval_status = Column(SqlEnum(DraftApprovalStatus, name="draft_approval_status_enum"), nullable=True)
    sent_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    edited_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="messages")
    sender = relationship("ConversationParticipant", foreign_keys=[sender_id])

    __table_args__ = (
        UniqueConstraint("org_id", "conversation_id", "id", name="uq_messages_server"),
        Index("ix_messages_conversation_sent", "conversation_id", "sent_at"),
        ForeignKeyConstraint(
            ["org_id", "conversation_id"],
            ["conversations.org_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "sender_id"],
            ["conversation_participants.org_id", "conversation_participants.id"],
            ondelete="SET NULL",
        ),
    )


class MessageReceipt(Base, OrgId):
    """Per-participant read/delivery receipts for a message."""
    __tablename__ = "message_receipts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    conversation_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False, index=True)
    conversation_participant_id = Column(Integer, nullable=False, index=True)

    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    meta = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "conversation_participant_id",
            name="uq_message_receipt_unique",
        ),
        ForeignKeyConstraint(
            ["org_id", "conversation_id", "message_id"],
            ["messages.org_id", "messages.conversation_id", "messages.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "conversation_participant_id"],
            ["conversation_participants.org_id", "conversation_participants.id"],
            ondelete="CASCADE",
        ),
    )
