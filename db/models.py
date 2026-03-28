# db/models.py

import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column,
    String,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Boolean,
    Text,
    UniqueConstraint,
    Integer,
    JSON,
)
from sqlalchemy.orm import declarative_base, relationship
from enum import Enum
from sqlalchemy import Enum as SqlEnum, Index

Base = declarative_base()


class Property(Base):
    """
    A property managed by the landlord.
    """

    __tablename__ = "properties"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    name = Column(String(255), nullable=True)
    address_line1 = Column(String(255), nullable=False)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    country = Column(String(100), nullable=True, default="USA")
    # 'single_family' — one tenant, no distinct units (house/condo)
    # 'multi_family'  — multiple units (apartment building, duplex, etc.)
    property_type = Column(String(20), nullable=True, default='multi_family')
    source = Column(String(20), nullable=True)  # 'manual' | 'document'

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    units = relationship(
        "Unit",
        back_populates="property",
        cascade="all, delete-orphan",
    )

    leases = relationship(
        "Lease",
        back_populates="property",
        cascade="all, delete-orphan",
    )


class Unit(Base):
    """
    A rentable unit within a property.
    """

    __tablename__ = "units"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    property_id = Column(
        String(36),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )

    label = Column(String(100), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "label",
            name="uq_units_property_label",
        ),
    )

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    property = relationship("Property", back_populates="units")

    leases = relationship(
        "Lease",
        back_populates="unit",
        cascade="all, delete-orphan",
    )


class Tenant(Base):
    """
    A tenant/contact.
    """

    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)

    extra = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    leases = relationship(
        "Lease",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    @property
    def units(self):
        return [lease.unit for lease in self.leases if lease.unit is not None]


class Lease(Base):
    """
    A lease agreement between a tenant and a unit.
    """

    __tablename__ = "leases"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    unit_id = Column(
        String(36),
        ForeignKey("units.id", ondelete="CASCADE"),
        nullable=False,
    )

    property_id = Column(
        String(36),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )

    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    rent_amount = Column(Float, nullable=False)
    payment_status = Column(String(20), nullable=True, default='current')  # current/late/overdue

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="leases")
    unit = relationship("Unit", back_populates="leases")
    property = relationship("Property", back_populates="leases")


# -------------------------------
# Messaging & Contacts
# -------------------------------

class ParticipantType(str, Enum):
    TENANT = "tenant"
    ACCOUNT_USER = "account_user"
    EXTERNAL_CONTACT = "external_contact"


class ConversationType(str, Enum):
    TENANT  = "tenant"
    VENDOR  = "vendor"
    USER_AI = "user_ai"
    TASK    = "task"


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
    Optionally tied to a property/unit/lease for context.
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

    # Task fields (unified task model)
    is_task = Column(Boolean, nullable=False, default=False)
    task_status = Column(String(20), nullable=True)   # suggested/active/paused/resolved/cancelled
    task_mode = Column(String(25), nullable=True)     # autonomous/waiting_approval/manual
    source = Column(String(25), nullable=True)        # ai_suggestion/tenant_report/document/sms/manual
    category = Column(String(20), nullable=True)      # rent/maintenance/leasing/compliance
    urgency = Column(String(20), nullable=True)       # low/medium/high/critical
    priority = Column(String(20), nullable=True)      # low/routine/urgent/emergency
    confidential = Column(Boolean, nullable=False, default=False)
    last_message_at = Column(DateTime, nullable=True)
    channel_type = Column(String(20), nullable=True)   # 'sms' | 'email' | None (manual/internal)

    # Conversation type taxonomy
    conversation_type = Column(String(20), nullable=True, default="task")
    parent_conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=True)
    ancestor_ids = Column(JSON, nullable=True, default=list)
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


class Document(Base):
    """
    An uploaded document (e.g. lease PDF).
    """
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=True)
    storage_path = Column(String(512), nullable=True)
    document_type = Column(String(50), nullable=False, default="lease")
    status = Column(String(50), nullable=False, default="pending")
    progress = Column(String(255), nullable=True)

    raw_text = Column(Text, nullable=True)
    extracted_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    sha256_checksum = Column(String(64), nullable=True, index=True)
    suggestion_states = Column(JSON, nullable=True)  # {category: 'accepted'|'rejected'}
    confirmed_at = Column(DateTime, nullable=True)
    extraction_meta = Column(JSON, nullable=True)   # {llm_model, text_extractor, page_count, raw_text_chars, form_fields_found, form_fields_filled, input_chars_sent_to_llm}


class DocumentTask(Base):
    """
    Many-to-many junction: one document can spawn multiple tasks;
    one task can reference multiple documents.
    """
    __tablename__ = "document_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("document_id", "task_id", name="uq_document_task"),)


class DocumentTag(Base):
    """
    Links a document to a property, unit, or tenant for the Documents UI tagging feature.
    """
    __tablename__ = "document_tags"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    tag_type = Column(String(20), nullable=False)  # property/unit/tenant
    property_id = Column(String(36), ForeignKey("properties.id", ondelete="CASCADE"), nullable=True)
    unit_id = Column(String(36), ForeignKey("units.id", ondelete="CASCADE"), nullable=True)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AutomationRevision(Base):
    """
    A snapshot of the automation config, stored as a linked list (git-like versioning).
    """
    __tablename__ = "automation_revisions"

    id = Column(String(16), primary_key=True)        # sha256[:16] of content+timestamp
    config = Column(JSON, nullable=False)             # full config snapshot
    message = Column(String(500), nullable=False, default="Update automation config")
    parent_id = Column(String(16), nullable=True)    # previous revision id (soft link)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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
