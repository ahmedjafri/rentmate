from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from .base import Base


class Account(Base):
    """A user identity — authentication record.

    Accounts are standalone user identities.
    """
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=True, unique=True)
    password_hash = Column(String(255), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
