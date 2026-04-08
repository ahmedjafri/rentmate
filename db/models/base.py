from sqlalchemy import Column, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()

DEFAULT_ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"


class HasAccountId:
    """Mixin that adds account_id to any model for multi-tenancy scoping."""
    account_id = Column(String(36), nullable=False, default=DEFAULT_ACCOUNT_ID, index=True)


class HasContext:
    """Mixin for entities that support agent context notes."""
    context = Column(Text, nullable=True)
