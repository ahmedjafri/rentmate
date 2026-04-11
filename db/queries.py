"""
db/queries.py — Shared ORM query functions.

These functions return fully-loaded ORM objects.  Both the GraphQL layer
(gql/queries.py) and the agent data tool (llm/agent_data.py) call these
instead of duplicating query logic.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backends.local_auth import resolve_account_id
from db.enums import TaskCategory, TaskSource, TaskStatus
from db.models import Conversation, ConversationParticipant, Lease, Message, Property, Task, Tenant, User

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
    u = t.user
    if u:
        return f"{u.first_name or ''} {u.last_name or ''}".strip()
    return ""


# ---------------------------------------------------------------------------
# Account scoping
# ---------------------------------------------------------------------------

def _account_id() -> int:
    """Get the current request's account_id from context."""
    return resolve_account_id()


# ---------------------------------------------------------------------------
# Query functions (all scoped to current account)
# ---------------------------------------------------------------------------

def fetch_properties(db: Session) -> list[Property]:
    return (
        db.execute(
            select(Property)
            .where(Property.creator_id == _account_id())
            .options(
                selectinload(Property.units),
                selectinload(Property.leases).selectinload(Lease.tenant).selectinload(Tenant.user),
                selectinload(Property.leases).selectinload(Lease.unit),
            )
        )
        .scalars()
        .all()
    )


def fetch_tenants(db: Session) -> list[Tenant]:
    return (
        db.execute(
            select(Tenant)
            .where(Tenant.creator_id == _account_id())
            .options(
                selectinload(Tenant.user),
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
            select(Lease)
            .where(Lease.creator_id == _account_id())
            .options(
                selectinload(Lease.tenant).selectinload(Tenant.user),
                selectinload(Lease.property),
                selectinload(Lease.unit),
            )
        )
        .scalars()
        .all()
    )


def fetch_tasks(
    db: Session,
    *, category: Optional[TaskCategory] = None,
    status: Optional[list[TaskStatus]] = None,
    source: Optional[TaskSource] = None,
) -> list[Task]:
    q = select(Task).where(Task.creator_id == _account_id())
    if category:
        q = q.where(Task.category == category)
    if status:
        q = q.where(Task.task_status.in_(status))
    if source:
        q = q.where(Task.source == source)
    q = q.options(
        selectinload(Task.ai_conversation).selectinload(Conversation.messages),
        selectinload(Task.parent_conversation).selectinload(Conversation.messages),
        selectinload(Task.external_conversation).selectinload(Conversation.messages),
        selectinload(Task.unit),
        selectinload(Task.lease).selectinload(Lease.tenant).selectinload(Tenant.user),
        selectinload(Task.lease).selectinload(Lease.unit),
    )
    return db.execute(q).scalars().all()


def fetch_task(db: Session, task_id: int) -> Optional[Task]:
    return db.execute(
        select(Task)
        .where(Task.id == task_id)
        .where(Task.creator_id == _account_id())
        .options(
            selectinload(Task.ai_conversation).selectinload(Conversation.messages),
            selectinload(Task.parent_conversation).selectinload(Conversation.messages),
            selectinload(Task.external_conversation).selectinload(Conversation.messages),
            selectinload(Task.unit),
            selectinload(Task.lease).selectinload(Lease.tenant).selectinload(Tenant.user),
            selectinload(Task.lease).selectinload(Lease.unit),
        )
    ).scalar_one_or_none()


def fetch_conversations(
    db: Session,
    *, conversation_type: str,
    limit: int = 50,
    offset: int = 0,
) -> list[Conversation]:
    """Fetch conversations by type, newest first."""
    q = (
        select(Conversation)
        .where(Conversation.conversation_type == conversation_type)
        .where(Conversation.is_archived.is_(False))
        .where(Conversation.creator_id == _account_id())
        .options(
            selectinload(Conversation.participants)
            .selectinload(ConversationParticipant.user),
            selectinload(Conversation.messages),
            selectinload(Conversation.property),
        )
        .order_by(Conversation.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return db.execute(q).scalars().all()


def fetch_vendors(db: Session) -> list[User]:
    return db.execute(
        select(User).where(User.creator_id == _account_id(), User.user_type == "vendor")
    ).scalars().all()


def fetch_messages(db: Session, conversation_id: str) -> list[Message]:
    conversation = db.execute(
        select(Conversation).where(
            Conversation.external_id == conversation_id,
            Conversation.creator_id == _account_id(),
        )
    ).scalar_one_or_none()
    if conversation is None:
        return []
    return (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.sent_at)
        )
        .scalars()
        .all()
    )
