"""
db/queries.py — Shared ORM query functions.

These functions return fully-loaded ORM objects.  Both the GraphQL layer
(gql/queries.py) and the agent data tool (llm/agent_data.py) call these
instead of duplicating query logic.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from db.models import Conversation, ConversationParticipant, ExternalContact, Lease, Message, Property, Task, Tenant


# ---------------------------------------------------------------------------
# Formatting helpers (pure functions over ORM models)
# ---------------------------------------------------------------------------

def format_address(p: Property) -> str:
    parts = [
        p.address_line1 or "",
        p.address_line2 or "",
        ", ".join(x for x in [p.city, p.state] if x),
        p.postal_code or "",
        p.country or "",
    ]
    return ", ".join(s for s in parts if s).replace(",,", ",")


def tenant_display_name(t: Tenant) -> str:
    return f"{t.first_name} {t.last_name}".strip()


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def fetch_properties(db: Session) -> list[Property]:
    return (
        db.execute(
            select(Property).options(
                selectinload(Property.units),
                selectinload(Property.leases).selectinload(Lease.tenant),
                selectinload(Property.leases).selectinload(Lease.unit),
            )
        )
        .scalars()
        .all()
    )


def fetch_tenants(db: Session) -> list[Tenant]:
    return (
        db.execute(
            select(Tenant).options(
                selectinload(Tenant.leases).selectinload(Lease.property),
                selectinload(Tenant.leases).selectinload(Lease.unit),
            )
        )
        .scalars()
        .all()
    )


def fetch_leases(db: Session) -> list[Lease]:
    return (
        db.execute(
            select(Lease).options(
                selectinload(Lease.tenant),
                selectinload(Lease.property),
                selectinload(Lease.unit),
            )
        )
        .scalars()
        .all()
    )


def fetch_tasks(
    db: Session,
    category: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
) -> list[Task]:
    q = select(Task)
    if category:
        q = q.where(Task.category == category)
    if status:
        statuses = [s.strip() for s in status.split(",")]
        q = q.where(Task.task_status.in_(statuses))
    if source:
        q = q.where(Task.source == source)
    q = q.options(
        selectinload(Task.ai_conversation).selectinload(Conversation.messages),
        selectinload(Task.parent_conversation).selectinload(Conversation.messages),
        selectinload(Task.external_conversation).selectinload(Conversation.messages),
        selectinload(Task.unit),
        selectinload(Task.lease).selectinload(Lease.tenant),
        selectinload(Task.lease).selectinload(Lease.unit),
    )
    return db.execute(q).scalars().all()


def fetch_task(db: Session, uid: str) -> Optional[Task]:
    return db.execute(
        select(Task)
        .where(Task.id == uid)
        .options(
            selectinload(Task.ai_conversation).selectinload(Conversation.messages),
            selectinload(Task.parent_conversation).selectinload(Conversation.messages),
            selectinload(Task.external_conversation).selectinload(Conversation.messages),
            selectinload(Task.unit),
            selectinload(Task.lease).selectinload(Lease.tenant),
            selectinload(Task.lease).selectinload(Lease.unit),
        )
    ).scalar_one_or_none()


def fetch_conversations(
    db: Session,
    conversation_type: str,
    limit: int = 50,
    offset: int = 0,
) -> list[Conversation]:
    """Fetch conversations by type, newest first."""
    q = (
        select(Conversation)
        .where(Conversation.conversation_type == conversation_type)
        .where(Conversation.is_archived.is_(False))
        .options(
            selectinload(Conversation.participants)
            .selectinload(ConversationParticipant.tenant),
            selectinload(Conversation.participants)
            .selectinload(ConversationParticipant.external_contact),
            selectinload(Conversation.messages),
            selectinload(Conversation.property),
        )
        .order_by(Conversation.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return db.execute(q).scalars().all()


def fetch_vendors(db: Session) -> list[ExternalContact]:
    return db.execute(select(ExternalContact)).scalars().all()


def fetch_messages(db: Session, conversation_id: str) -> list[Message]:
    return (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sent_at)
        )
        .scalars()
        .all()
    )
