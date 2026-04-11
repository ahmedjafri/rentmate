from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Session

from .base import Base, OrgId, SmallPrimaryId


class User(Base, OrgId, SmallPrimaryId):
    """A user identity — login credentials and profile.

    Shadow users (no credentials) represent tenants and external contacts
    (vendors, etc.) so every participant in a conversation has a User row.
    """
    __tablename__ = "users"

    email = Column(String(255), nullable=True, unique=True)
    password_hash = Column(String(255), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    phone = Column(String(50), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_users_org"),
    )


def create_shadow_user(db: Session) -> User:
    """Create a User with no credentials (shadow account for a tenant without login)."""
    user = User(active=True, created_at=datetime.now(UTC))
    db.add(user)
    db.flush()
    return user
