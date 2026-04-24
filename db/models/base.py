import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
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
    """Per-org sequential integer primary key with a composite `(org_id, id)` PK.

    The `id` value is NOT driven by a DB sequence — it must be assigned by
    `NumberAllocator.allocate_next(sess, entity_type, org_id)` before insert,
    using the shared `id_sequences` table. This gives each org its own 1..N
    numbering per entity type: id=1 can coexist in org 1 and org 2.

    Any insert that omits `id` will fail with NotNullViolation — this is
    intentional to prevent accidental global autoincrement from interfering
    with the per-org counter.

    Subclasses MUST include `NumberedPrimaryId.primary_key(cls)` in their
    `__table_args__` to declare the composite PK.
    """
    id = Column(Integer, nullable=False, autoincrement=False)

    @staticmethod
    def primary_key(cls) -> PrimaryKeyConstraint:
        return PrimaryKeyConstraint(
            "org_id", "id", name=f"pk_{cls.__tablename__}"
        )


class IdSequence(Base):
    """Per-org, per-entity-type monotonic counter for `NumberedPrimaryId` models.

    Only ever incremented — never decremented or reset — so ids aren't reused
    even after rows are deleted. Concurrent allocations are race-safe via an
    atomic UPSERT with RETURNING in `NumberAllocator.allocate_next`.
    """
    __tablename__ = "id_sequences"

    org_id = Column(Integer, primary_key=True)
    entity_type = Column(String(64), primary_key=True)
    last_number = Column(Integer, nullable=False, default=0)


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
