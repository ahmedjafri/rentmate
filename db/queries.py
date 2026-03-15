"""
db/queries.py — Shared ORM query functions.

These functions return fully-loaded ORM objects.  Both the GraphQL layer
(gql/queries.py) and the agent data tool (llm/agent_data.py) call these
instead of duplicating query logic.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from db.models import Conversation, Lease, Message, Property, Tenant


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
) -> list[Conversation]:
    q = select(Conversation).where(Conversation.is_task == True)  # noqa: E712
    if category:
        q = q.where(Conversation.category == category)
    if status:
        statuses = [s.strip() for s in status.split(",")]
        q = q.where(Conversation.task_status.in_(statuses))
    if source:
        q = q.where(Conversation.source == source)
    q = q.options(
        selectinload(Conversation.messages),
        selectinload(Conversation.unit),
        selectinload(Conversation.lease).selectinload(Lease.tenant),
        selectinload(Conversation.lease).selectinload(Lease.unit),
    )
    return db.execute(q).scalars().all()


def fetch_task(db: Session, uid: str) -> Optional[Conversation]:
    return db.execute(
        select(Conversation)
        .where(Conversation.id == uid, Conversation.is_task == True)  # noqa: E712
        .options(
            selectinload(Conversation.messages),
            selectinload(Conversation.unit),
            selectinload(Conversation.lease).selectinload(Lease.tenant),
            selectinload(Conversation.lease).selectinload(Lease.unit),
        )
    ).scalar_one_or_none()


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
