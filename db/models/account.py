import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


class Account(Base):
    """A customer account — the top-level multi-tenancy boundary.

    All properties, tenants, tasks, and conversations belong to an account.
    """
    __tablename__ = "accounts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    users = relationship("AccountUser", back_populates="account")


class AccountUser(Base):
    """Links a user to an account with a role.

    A user can belong to multiple accounts (e.g. a property manager
    managing for several owners). Each link has a role:
    - admin: full access, can invite others
    - manager: day-to-day operations
    - tenant: tenant portal access only
    """
    __tablename__ = "account_users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    account_id = Column(String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False, default="admin")  # admin, manager, tenant
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    account = relationship("Account", back_populates="users")

    __table_args__ = (
        UniqueConstraint("user_id", "account_id", name="uq_account_user"),
    )
