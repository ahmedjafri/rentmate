import uuid

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()

DEFAULT_ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"


class HasAccountId:
    """Mixin that adds account_id to any model for multi-tenancy scoping."""
    account_id = Column(String(36), nullable=False, default=DEFAULT_ACCOUNT_ID, index=True)


class HasContext:
    """Mixin for entities that support shared/system context notes.

    The ``context`` column stores shared context visible to all accounts
    (e.g. lease terms from document extraction).  Per-account private notes
    are stored in the ``EntityNote`` table instead.
    """
    context = Column(Text, nullable=True)


class EntityNote(Base):
    """Per-account private notes on any entity.

    Stores account-specific observations, assessments, and preferences that
    should not be visible to other accounts.  Shared/system context remains
    on the entity's own ``context`` column (via HasContext).
    """
    __tablename__ = "entity_notes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String(36), nullable=False, index=True)
    entity_type = Column(String(20), nullable=False)  # property, unit, tenant, vendor, document
    entity_id = Column(String(36), nullable=False, index=True)
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("account_id", "entity_type", "entity_id", name="uq_entity_note_account"),
    )
