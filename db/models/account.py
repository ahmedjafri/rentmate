from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from .base import Base


class User(Base):
    """A user identity — login credentials and profile."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=True, unique=True)
    password_hash = Column(String(255), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
