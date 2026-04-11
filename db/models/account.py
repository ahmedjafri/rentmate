from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKeyConstraint, Integer, JSON, String, Text, UniqueConstraint
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
    creator_id = Column(Integer, nullable=True)
    user_type = Column(String(20), nullable=True, default="account")
    company = Column(String(255), nullable=True)
    role_label = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    extra = Column(JSON, nullable=True)
    context = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    @property
    def name(self) -> str:
        return " ".join(part for part in [self.first_name, self.last_name] if part).strip()

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_users_org"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )


def create_shadow_user(db: Session, **fields) -> User:
    """Create a User with no credentials (shadow account for a tenant without login)."""
    user = User(active=True, created_at=datetime.now(UTC), **fields)
    db.add(user)
    db.flush()
    return user
