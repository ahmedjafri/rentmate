import uuid

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()

def _resolve_creator_id():
    """Column default: resolve creator_id from request context.

    Raises RuntimeError if no context is set — catches code that creates
    entities without going through authentication.
    """
    from backends.local_auth import resolve_creator_id
    return resolve_creator_id()


class HasCreatorId:
    """Mixin that adds creator_id to any model, tracking which account created it.

    The default resolves from the request-scoped context var. If no context
    is set (missing auth), entity creation raises RuntimeError.
    Startup code must call set_request_context() first or pass creator_id explicitly.
    """
    creator_id = Column(String(36), nullable=False, default=_resolve_creator_id, index=True)


HasAccountId = HasCreatorId  # backward compat alias


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
    creator_id = Column(String(36), nullable=False, index=True)
    entity_type = Column(String(20), nullable=False)  # property, unit, tenant, vendor, document
    entity_id = Column(String(36), nullable=False, index=True)
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("creator_id", "entity_type", "entity_id", name="uq_entity_note_creator"),
    )
