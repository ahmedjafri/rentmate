from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from .base import Base


class Account(Base):
    """A user identity — authentication record.

    Accounts are standalone user identities. In hosted mode, they are linked
    to one or more orgs via OrgMember. In single-tenant mode, there is one
    default account (id=1).
    """
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True, unique=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
