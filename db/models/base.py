import uuid

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()

DEFAULT_ORG_ID = 1


class OrgId:
    """Mixin that adds org_id for multi-server data partitioning.

    Hardcoded to DEFAULT_ORG_ID for self-hosted; the hosted version
    assigns a unique org_id per deployment.
    """
    org_id = Column(Integer, nullable=False, default=DEFAULT_ORG_ID, index=True)


class PrimaryId:
    """UUID primary key for models that need globally unique, non-guessable IDs."""
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))


class NumberedPrimaryId:
    """Auto-incrementing integer primary key for models with sequential numbering."""
    id = Column(Integer, primary_key=True, autoincrement=True)


class SmallPrimaryId:
    """Auto-incrementing integer primary key with a UUID external_id for API exposure."""
    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))


class HasCreatorId:
    """Mixin that adds creator_id (integer) referencing User.id.

    Each model using this mixin must include a composite FK in __table_args__::

        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    """
    creator_id = Column(Integer, nullable=False)


class HasContext:
    """Shared context column (e.g. lease terms from document extraction)."""
    context = Column(Text, nullable=True)


class EntityNote(Base, OrgId, PrimaryId, HasCreatorId):
    """Per-account private notes on any entity."""
    __tablename__ = "entity_notes"

    entity_type = Column(String(20), nullable=False)
    entity_id = Column(String(36), nullable=False, index=True)
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_entity_notes_org"),
        UniqueConstraint("creator_id", "entity_type", "entity_id", name="uq_entity_note_creator"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )
