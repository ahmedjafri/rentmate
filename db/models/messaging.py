import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Boolean,
    Text,
    UniqueConstraint,
    JSON,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy import Enum as SqlEnum

from .base import Base


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


class MessageType(str, Enum):
    MESSAGE    = "message"
    INTERNAL   = "internal"
    APPROVAL   = "approval"      # legacy — use SUGGESTION for new code
    SUGGESTION = "suggestion"    # links to a Suggestion via related_task_ids.suggestion_id
    CONTEXT    = "context"
    THREAD     = "thread"    # deprecated — use MESSAGE


class ExternalContact(Base):
    """
    Non-auth contacts (e.g., maintenance vendors).
    """
    __tablename__ = "external_contacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    company = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True, index=True)
    phone = Column(String(50), nullable=True)
    role_label = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    extra = Column(JSON, nullable=True)
    account_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Conversation(Base):
    """
    A message thread (1:1 or group).
    Optionally tied to a task and/or a property/unit/lease for context.
    """
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    property_id = Column(String(36), ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True)
    unit_id = Column(String(36), ForeignKey("units.id", ondelete="SET NULL"), nullable=True, index=True)
    lease_id = Column(String(36), ForeignKey("leases.id", ondelete="SET NULL"), nullable=True, index=True)

    subject = Column(String(255), nullable=True)
    is_group = Column(Boolean, nullable=False, default=False)
    is_archived = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Conversation type taxonomy
    conversation_type = Column(String(20), nullable=True, default="task_ai")
    parent_conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=True)
    ai_initiated = Column(Boolean, nullable=False, default=False)

    # Flexible metadata (vendor requirements, assignments, etc.)
    extra = Column(JSON, nullable=True)

    property = relationship("Property")
    unit = relationship("Unit")
    lease = relationship("Lease")
    parent = relationship("Conversation", remote_side="Conversation.id", foreign_keys="Conversation.parent_conversation_id")

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
        Index("ix_conversations_updated", "updated_at"),
    )


class ConversationParticipant(Base):
    """
    Links a conversation to a participant (tenant or external_contact).
    """
    __tablename__ = "conversation_participants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    conversation_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    participant_type = Column(SqlEnum(ParticipantType, name="participant_type"), nullable=False)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    external_contact_id = Column(String(36), ForeignKey("external_contacts.id", ondelete="CASCADE"), nullable=True, index=True)

    delivery_prefs = Column(JSON, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    joined_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    left_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="participants")
    tenant = relationship("Tenant")
    external_contact = relationship("ExternalContact")

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "participant_type",
            "tenant_id",
            "external_contact_id",
            name="uq_conversation_participant_unique_entity",
        ),
    )

    @property
    def display_name(self) -> str:
        if self.tenant:
            return f"{self.tenant.first_name} {self.tenant.last_name}"
        if self.external_contact:
            return self.external_contact.name
        return "Unknown"


class Message(Base):
    """
    A message within a conversation.
    """
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    conversation_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sender_type = Column(SqlEnum(ParticipantType, name="message_sender_type"), nullable=False)
    sender_tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    sender_external_contact_id = Column(String(36), ForeignKey("external_contacts.id", ondelete="SET NULL"), nullable=True, index=True)

    body = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    attachments = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)

    # AI chat enhancement fields
    message_type = Column(String(20), nullable=True)      # message/internal/approval/context
    sender_name = Column(String(255), nullable=True)
    is_ai = Column(Boolean, nullable=False, default=False)
    draft_reply = Column(Text, nullable=True)
    approval_status = Column(String(20), nullable=True)   # pending/approved/rejected/edited
    related_task_ids = Column(JSON, nullable=True)        # [{taskId, label}]

    sent_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    edited_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="messages")
    sender_tenant = relationship("Tenant")
    sender_external_contact = relationship("ExternalContact")

    __table_args__ = (
        Index("ix_messages_conversation_sent", "conversation_id", "sent_at"),
    )

    def validate_sender(self):
        if self.sender_type == ParticipantType.TENANT and not self.sender_tenant_id:
            raise ValueError("sender_tenant_id required for TENANT message")
        if self.sender_type == ParticipantType.EXTERNAL_CONTACT and not self.sender_external_contact_id:
            raise ValueError("sender_external_contact_id required for EXTERNAL_CONTACT message")


class MessageReceipt(Base):
    """
    Per-participant read/delivery receipts for a message.
    """
    __tablename__ = "message_receipts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    message_id = Column(
        String(36),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_participant_id = Column(
        String(36),
        ForeignKey("conversation_participants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    meta = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "conversation_participant_id",
            name="uq_message_receipt_unique",
        ),
    )
